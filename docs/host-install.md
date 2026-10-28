# Linux VM and Salt installation

Conductor runs a small systemd service on Linux. Codex and its native workers remain on each person's computer. The Windows graphical installer is local-only; use the client CLI to connect Codex to a hosted service.

## One-person VM setup

On Ubuntu 24.04 or Debian 12, clone this repository on the VM and run:

```bash
cd conductor-codex
git pull --ff-only
bash install-vm.sh
```

The first run asks one database question:

1. Create a new private SQLite database on this VM (default).
2. Import an existing **Conductor** SQLite database. The source is preserved and a checked copy becomes Conductor's private database.
3. Use an existing MySQL/MariaDB database. Supply its host or Unix socket, database name, application user, private password **file**, and a CA certificate for TCP. The database and user must already exist. No password is entered or printed by the wizard.

The script writes `/etc/conductor/conductor.yaml`, packages the clean Git commit, installs required Python packages and the dedicated service account, creates the first Conductor user/key, starts `conductor.service`, and checks authenticated health. It asks for sudo using the normal terminal. Reruns reuse the YAML and existing data; they do not repeat the questions. A new release takes an online SQLite backup before upgrade. MySQL backups remain the database administrator's responsibility. Git credentials, Codex sign-in and model keys never go to the service.

The service listens on `127.0.0.1:8012` by default. Reach it through SSH forwarding or an administrator-managed HTTPS proxy. Export the first user's private key to the invoking Linux account with `bash conductor-users.sh export LOGIN`. The file is never printed. See [accounts](accounts.md) and [client setup](client-setup.md).

## Salt managed installation

Salt manages the complete noninteractive route. Give every service node the same reviewed source bundle and hash, its `/etc/conductor/conductor.yaml`, and a private database password file when using MySQL. [The example service state](../examples/salt/conductor/init.sls) installs dependencies, creates the service account, manages files and secrets, runs the pinned installer only on drift, and keeps the service running. Set `conductor:mysql_tcp` only for a TCP database connection so Salt also installs the CA certificate; Unix-socket connections need no CA file. [The example database state](../examples/salt/conductor/database.sls) creates a dedicated database when requested, provisions an application user and grants access on the **database host**. The database minion's Salt Python runtime needs a MySQL driver and private privileged connection settings before applying that state. Apply the database state first; then apply the service state to each Conductor node. A preexisting dedicated database can skip database creation while still managing its application user/grants.

The YAML format is [shown here](../examples/salt/conductor/conductor.yaml.example). The same file is used by the host installer and running service. It contains service user, code and state locations, listener, initial accounts, and database engine/connection details. The MySQL password is referenced by file path; its value stays in private Salt pillar and a `0640` file readable by the service group. Salt should manage `/etc/conductor` as `root:conductor` mode `0750` and `conductor.yaml` as `root:conductor` mode `0640`.

For SQLite, use `engine: sqlite`, `mode: create`, and an absolute `.sqlite3` path inside the private `state_dir`. To import a prior Conductor SQLite database, use `mode: existing` and `source_path`; the destination `path` must still be inside `state_dir`. The source is never replaced. SQLite data must be on local storage, not NFS or SMB.

For MySQL/MariaDB, use `engine: mysql`, `mode: existing`, a dedicated `name`, `user`, `password_file`, and either `unix_socket` or `host`/`port`/`ssl_ca`. The application verifies the server certificate and identity for TCP. Salt creates a new database first when desired; Conductor then treats it as existing. Do not point Conductor at an unrelated application's schema. Conductor creates its own `conductor_*` tables and checks schema version 1. The application account needs `SELECT`, `INSERT`, `UPDATE`, `DELETE`, and initial schema `CREATE`/`INDEX` privileges. Planned schema upgrades may require `ALTER`.

The lower-level installer is completely noninteractive:

```bash
sudo ./install-host.sh --config /etc/conductor/conductor.yaml --bundle /var/cache/conductor/conductor-source.tar.gz --sha256 ACTUAL_64_CHARACTER_SHA256
```

It verifies the bundle digest and archive safety before creating an owned release under `/opt/conductor`, creates private keys under `/var/lib/conductor/private`, starts the hardened systemd unit, and checks health. `--check` is the Salt `unless` guard. A matching healthy rerun reports `unchanged`. Source tarballs must include `pyproject.toml`, `src/`, `install-host.sh` and `scripts/install_host.py`; exclude `.git`, `.codex`, tokens, databases and client profiles. On Ubuntu/Debian, `python3`, `python3-venv` and `python3-yaml` are required; the script installs them noninteractively if absent. Runtime Python dependencies come from PyPI or an approved wheelhouse configured in YAML.

## Multiple service nodes

Point every Conductor service node at the same MySQL database or database failover endpoint. InnoDB transactions plus a database named lock serialize ledger mutations across nodes, preserving manager leases and fencing. Each node can keep its own private health and initial key files; account/key hashes and workloads live in the shared database. A key issued on one node authenticates on the others. Place the nodes behind an HTTPS load balancer with health checks. Salt deploys the same pinned release and compatible YAML to each node. Conductor does not create the load balancer or provide MySQL replication/failover; those are part of the enterprise platform.

Use your database platform's backup and restore process for MySQL. A Conductor code rollback does not roll back an external database. Schedule database backups and test restores before upgrades. For SQLite, the installer saves consistent upgrade snapshots under `/var/lib/conductor/backups/`; those are not off-VM disaster-recovery copies. Inspect `journalctl -u conductor.service` after a failed upgrade. The installer attempts to restore the previous code/service state; SQLite also restores its upgrade backup. Reconcile native Codex workers before retrying leases after any data restore.

MySQL 8.0 adapter checks and MariaDB 10.11 compatibility checks passed against isolated test schemas, including distinct accounts, revocation, private workloads and concurrent lease fencing. The MariaDB-backed package installed cleanly and its HTTP authentication and health checks passed. A Salt-managed MySQL service, remote TLS connection, and live multi-node failover test remain release checks.
