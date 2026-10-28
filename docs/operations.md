# Service operations

For a new multi-user Linux VM, run `bash install-vm.sh` from an updated Git checkout. It builds a pinned bundle, installs under systemd and checks health; see [VM installation](host-install.md). The older Docker Compose wizard remains available through `bash install.sh`; the commands below apply to that Compose deployment and use its recorded project name.

The service runs on a Linux host with Docker Engine and Compose, Python 3.11+ for the setup helper, and local Docker volume storage. The default bind is `127.0.0.1:8012`; use the client computer's SSH tunnel or your own HTTPS reverse proxy. Do not expose plaintext bearer traffic to untrusted networks.

```bash
bash install.sh server
```

Setup builds the local image and initializes named volumes using a short-lived initialization container. The persistent service runs as a non-root account. Credentials are generated once and kept mode `0600`; the live SQLite database uses local WAL storage. Repeating setup preserves the credential and database. No model credentials or project sources belong on this service.

To export the service token, choose a nonexistent file on local private storage. The entry point creates a missing parent directory with private permissions; it does not relax an existing directory or follow symbolic links:

```bash
bash install.sh server --export-token /path/to/private/service-token
```

The helper captures the credential privately and creates the destination exclusively. It never prints it. Transfer the file securely to the client; see [client setup](client-setup.md).

## Consistent backup

```bash
COMPOSE_PROJECT_NAME=conductor-codex docker compose run --rm --no-deps service python -m conductor_coordination.backup backup
```

This uses SQLite's online backup API, validates integrity and saves a private timestamped snapshot under `/backups` in a separate named Docker volume. It can run while the service is active. Do not copy only the main active database file; WAL state may contain newer transactions.

No automatic backup schedule, retention policy or off-host copy is configured by this candidate. Schedule the command and arrange protected off-host copies if you require disaster recovery. A separate volume on the same host does not survive host loss by itself. Backups contain project metadata and require the same access care as the live database.

For a restore test, substitute an actual snapshot basename and a new isolated destination:

```bash
COMPOSE_PROJECT_NAME=conductor-codex docker compose run --rm --no-deps service python -m conductor_coordination.backup restore --source /backups/tasks-EXACT.sqlite3 --destination /backups/restored-check.sqlite3
```

Restore refuses an existing destination. This command checks a separate copy; it does not replace the live database. Do not use an old metadata snapshot as proof a native worker stopped. Reconcile active worker IDs through the original client before operational recovery.

## Upgrades and removal

Record the current revision and take a verified backup before upgrading. Check out a reviewed revision, rerun setup, restart with Compose, and verify health and known workload records. Preserve a rollback plan appropriate to any schema changes.

`docker compose down` stops this deployment while preserving named volumes. `docker compose down --volumes` destroys its persistent state and backups; use it only when you intentionally want to erase that deployment after preserving any needed records. Stopping the metadata service does not stop native Codex workers.

## Configuration

Use `bash install.sh server --help` for bind/port/export/project options. `server` mode builds, initializes and starts the named Compose project, waiting up to 90 seconds for health. See [Docker Compose startup options](https://docs.docker.com/reference/cli/docker/compose/up/). Use the same `--project` name for setup and credential export, and set `COMPOSE_PROJECT_NAME` to that name for direct backup/restore/removal commands. Omitting the option uses the `conductor-codex` project. The lower-level `deploy/setup.py` is retained for custom provisioning. The generated `.env` contains deployment settings, not the token. Core service variables are `CONDUCTOR_DB`, `CONDUCTOR_TOKEN_FILE`, `MCP_HOST` and `MCP_PORT`; Compose supplies its container paths. Hosted user keys and workload privacy are described in [hosted accounts](accounts.md).
