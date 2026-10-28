"""Read the Salt-managed host configuration without loading executable YAML tags."""
from __future__ import annotations

import os
from pathlib import Path
import stat


def read_config(path: str | Path) -> dict:
    import yaml

    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Conductor configuration must be a regular file")
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"conductor", "database"}:
        raise ValueError("Configuration needs conductor and database mappings")
    if not isinstance(data["conductor"], dict) or not isinstance(data["database"], dict):
        raise ValueError("Conductor and database settings must be mappings")
    return data


def database_settings(config: dict) -> dict:
    database = config["database"]
    engine = database.get("engine")
    mode = database.get("mode")
    if engine not in {"sqlite", "mysql"} or mode not in {"create", "existing"}:
        raise ValueError("database.engine must be sqlite or mysql; mode must be create or existing")
    allowed = ({"engine", "mode", "path", "source_path"} if engine == "sqlite" else
               {"engine", "mode", "host", "port", "name", "user", "password_file", "ssl_ca", "unix_socket"})
    if set(database) - allowed:
        raise ValueError("Unknown database setting")
    if engine == "sqlite":
        value = database.get("path")
        if not isinstance(value, str) or not Path(value).is_absolute() or Path(value).suffix != ".sqlite3":
            raise ValueError("SQLite path must be an absolute .sqlite3 file")
    else:
        if mode != "existing":
            raise ValueError("Provision a MySQL database with Salt first, then select existing mode")
        for field in ("name", "user", "password_file"):
            if not isinstance(database.get(field), str) or not database[field]:
                raise ValueError(f"MySQL {field} is required")
        if not database.get("host") and not database.get("unix_socket"):
            raise ValueError("MySQL host or unix_socket is required")
        if database.get("host") and database.get("unix_socket"):
            raise ValueError("Choose one MySQL transport")
        if database.get("host") and not database.get("ssl_ca"):
            raise ValueError("MySQL TCP requires ssl_ca to verify the server")
        port = database.get("port", 3306)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("MySQL port must be 1..65535")
        password = Path(database["password_file"])
        if not password.is_absolute() or password.is_symlink() or not password.is_file():
            raise ValueError("MySQL password_file must be an existing regular absolute path")
        if stat.S_IMODE(password.stat().st_mode) & 0o037:
            raise ValueError("MySQL password_file must be private (0600 or 0640)")
        if database.get("ssl_ca") and not Path(database["ssl_ca"]).is_file():
            raise ValueError("MySQL ssl_ca is unavailable")
    return database


def open_store(clock=None):
    from conductor_coordination.coordination_store import Store

    config_path = os.environ.get("CONDUCTOR_CONFIG")
    if not config_path:
        database = os.environ.get("CONDUCTOR_DB", "/var/lib/conductor-coordination/tasks.sqlite3")
        return Store(database) if clock is None else Store(database, clock)
    database = database_settings(read_config(config_path))
    if database["engine"] == "sqlite":
        return Store(database["path"]) if clock is None else Store(database["path"], clock)
    from conductor_coordination.mysql_store import MySQLStore
    return MySQLStore(database) if clock is None else MySQLStore(database, clock)
