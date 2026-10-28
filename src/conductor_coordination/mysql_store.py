"""MySQL-backed ledger with the same serialized mutation contract as SQLite."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import re
import threading
import time

from conductor_coordination.coordination_store import Store


_TABLES = ("workloads", "tasks", "operations", "events", "users", "user_keys")
_SCHEMA_READY: set[tuple] = set()
_SCHEMA_LOCK = threading.Lock()


class _Row(dict):
    """Keep sqlite.Row's name and positional access used by the ledger."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class _Connection:
    def __init__(self, raw, lock_name):
        self.raw = raw
        self.lock_name = lock_name
        self.locked = False

    def execute(self, sql, parameters=()):
        statement = sql.strip()
        if statement == "BEGIN IMMEDIATE":
            with self.raw.cursor() as cursor:
                cursor.execute("SELECT GET_LOCK(%s, 15)", (self.lock_name,))
                if cursor.fetchone()[0] != 1:
                    raise TimeoutError("Conductor MySQL write lock timed out")
            self.locked = True
            self.raw.begin()
            return None
        if statement.startswith("INSERT OR IGNORE INTO users"):
            statement = statement.replace("INSERT OR IGNORE", "INSERT", 1) + " ON DUPLICATE KEY UPDATE id=id"
        if "ON CONFLICT(id) DO UPDATE SET" in statement:
            statement = statement.split(" ON CONFLICT(id) DO UPDATE SET", 1)[0] + (
                " ON DUPLICATE KEY UPDATE manager=VALUES(manager),client=VALUES(client),"
                "epoch=VALUES(epoch),expires=VALUES(expires),updated=VALUES(updated)")
        for table in _TABLES:
            statement = re.sub(r"\b" + table + r"\b", "conductor_" + table, statement)
        statement = statement.replace("?", "%s")
        with self.raw.cursor() as cursor:
            cursor.execute(statement, parameters)
            # PyMySQL buffers results, so a copied result outlives the cursor.
            if cursor.description:
                columns = [item[0] for item in cursor.description]
                rows = [_Row(zip(columns, values)) for values in cursor.fetchall()]
            else:
                rows = []
            return _BufferedResult(rows, cursor.rowcount)

    def commit(self):
        try:
            self.raw.commit()
        finally:
            self._unlock()

    def rollback(self):
        try:
            self.raw.rollback()
        finally:
            self._unlock()

    def _unlock(self):
        if self.locked:
            with self.raw.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (self.lock_name,))
            self.locked = False

    def close(self):
        self.raw.close()


class _BufferedResult:
    def __init__(self, rows, rowcount):
        self.rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class MySQLStore(Store):
    storage_kind = "mysql"

    def __init__(self, settings: dict, clock=time.time):
        self.settings = settings
        self.clock = clock
        self.lock_name = "conductor:" + hashlib.sha256(settings["name"].encode()).hexdigest()[:48]
        identity = (settings.get("host"), settings.get("unix_socket"), settings.get("port", 3306), settings["name"])
        if identity not in _SCHEMA_READY:
            with _SCHEMA_LOCK:
                if identity not in _SCHEMA_READY:
                    self._initialize()
                    _SCHEMA_READY.add(identity)

    def _connect(self):
        import pymysql

        password_path = Path(self.settings["password_file"])
        password = password_path.read_text(encoding="utf-8").rstrip("\r\n")
        if not password:
            raise ValueError("MySQL password file is empty")
        arguments = dict(user=self.settings["user"], password=password,
                         database=self.settings["name"], autocommit=True,
                         charset="utf8mb4", connect_timeout=5, read_timeout=15, write_timeout=15)
        if self.settings.get("unix_socket"):
            arguments["unix_socket"] = self.settings["unix_socket"]
        else:
            arguments.update(host=self.settings["host"], port=self.settings.get("port", 3306),
                             ssl_ca=self.settings["ssl_ca"], ssl_verify_cert=True,
                             ssl_verify_identity=True)
        return pymysql.connect(**arguments)

    @contextmanager
    def _db(self):
        connection = _Connection(self._connect(), self.lock_name)
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self):
        ddl = (
            "CREATE TABLE IF NOT EXISTS conductor_schema (version INT NOT NULL PRIMARY KEY) ENGINE=InnoDB",
            "CREATE TABLE IF NOT EXISTS conductor_workloads (id VARCHAR(191) COLLATE utf8mb4_bin PRIMARY KEY, manager VARCHAR(256) NOT NULL, client VARCHAR(32) NOT NULL, epoch INT NOT NULL, expires DOUBLE NOT NULL, state VARCHAR(32) NOT NULL, summary VARCHAR(4000) NOT NULL DEFAULT '', updated DOUBLE NOT NULL) ENGINE=InnoDB",
            "CREATE TABLE IF NOT EXISTS conductor_tasks (workload VARCHAR(191) COLLATE utf8mb4_bin NOT NULL, id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL, document LONGTEXT NOT NULL, PRIMARY KEY(workload,id)) ENGINE=InnoDB",
            "CREATE TABLE IF NOT EXISTS conductor_operations (workload VARCHAR(191) COLLATE utf8mb4_bin NOT NULL, id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL, digest CHAR(64) NOT NULL, result LONGTEXT NOT NULL, PRIMARY KEY(workload,id)) ENGINE=InnoDB",
            "CREATE TABLE IF NOT EXISTS conductor_events (seq BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, workload VARCHAR(191) COLLATE utf8mb4_bin NOT NULL, at DOUBLE NOT NULL, kind VARCHAR(64) NOT NULL, task VARCHAR(128) NOT NULL, detail LONGTEXT NOT NULL, INDEX(workload,seq)) ENGINE=InnoDB",
            "CREATE TABLE IF NOT EXISTS conductor_users (id VARCHAR(32) COLLATE utf8mb4_bin PRIMARY KEY, name VARCHAR(63) COLLATE utf8mb4_bin NOT NULL UNIQUE, created DOUBLE NOT NULL) ENGINE=InnoDB",
            "CREATE TABLE IF NOT EXISTS conductor_user_keys (id VARCHAR(32) COLLATE utf8mb4_bin PRIMARY KEY, user_id VARCHAR(32) COLLATE utf8mb4_bin NOT NULL, label VARCHAR(200) NOT NULL, token_hash CHAR(64) COLLATE utf8mb4_bin NOT NULL UNIQUE, created DOUBLE NOT NULL, revoked DOUBLE NULL, INDEX(user_id)) ENGINE=InnoDB",
        )
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                with db.raw.cursor() as cursor:
                    try:
                        cursor.execute("SELECT version FROM conductor_schema")
                        versions = cursor.fetchall()
                    except Exception as error:
                        if getattr(error, "args", (None,))[0] != 1146:
                            raise
                        versions = ()
                if not versions:
                    for statement in ddl:
                        # DDL auto-commits in MySQL; the named lock still serializes setup.
                        with db.raw.cursor() as cursor:
                            cursor.execute(statement)
                    with db.raw.cursor() as cursor:
                        cursor.execute("INSERT INTO conductor_schema(version) VALUES(1) ON DUPLICATE KEY UPDATE version=version")
                        cursor.execute("SELECT version FROM conductor_schema")
                        versions = cursor.fetchall()
                if versions != ((1,),):
                    raise RuntimeError("Unsupported Conductor MySQL schema version")
                probes = (
                    "SELECT id,manager,client,epoch,expires,state,summary,updated FROM conductor_workloads LIMIT 0",
                    "SELECT workload,id,document FROM conductor_tasks LIMIT 0",
                    "SELECT workload,id,digest,result FROM conductor_operations LIMIT 0",
                    "SELECT seq,workload,at,kind,task,detail FROM conductor_events LIMIT 0",
                    "SELECT id,name,created FROM conductor_users LIMIT 0",
                    "SELECT id,user_id,label,token_hash,created,revoked FROM conductor_user_keys LIMIT 0",
                )
                for query in probes:
                    with db.raw.cursor() as cursor:
                        cursor.execute(query)
                db.commit()
            except Exception:
                db.rollback()
                raise
