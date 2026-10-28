# Conductor over the user's SSH connection

The recommended hosted path uses a short Codex skill and a command run in the user's existing shell. The command sends one bounded JSON request through SSH for each ledger operation. The VM derives the Conductor account from the authenticated Linux SSH login and runs the ledger operation as an unprivileged service account. No Conductor service key is copied to the client. No agent-specific SSH credentials, local Python runtime, MCP server configuration or background tunnel are needed.

The VM still needs a database: local SQLite for one host, or an existing shared MySQL service for enterprise HA. MySQL provisioning, backups and failover remain the database administrator's responsibility. The VM command is stateless between calls; multiple SSH logins can use it concurrently, and each sees only its account's work. A Codex session must still have native worker controls. Conductor records work; it cannot spawn, stop or resume native agents.

## VM self-install

On a Linux VM, clone the complete repository into a local checkout and run:

```bash
bash install-ssh-vm.sh
```

The script requires Git, sudo and a system package manager. On Debian/Ubuntu it installs only `python3`, `python3-yaml` and `sudo`; it adds `python3-pymysql` only when the YAML selects MySQL. It never invokes pip. On other distributions, install the equivalent OS packages first. For a new VM the script asks whether to create SQLite, import an existing Conductor SQLite file, or connect to an existing MySQL database. Existing `/etc/conductor/conductor.yaml` is preserved and no questions are asked. The VM administrator must allow SSH logins through the normal OS/enterprise access policy and grant authorized users membership in the YAML `enrollment_group` (default `conductor-enroll`). The initial local SSH login is added automatically.

The installer stores a pinned source release under `/opt/conductor/ssh-releases/`, points `/opt/conductor/ssh-current` to it, and installs the root-owned `/usr/local/lib/conductor-command` with a sudo rule for that group only. The command accepts no command-line arguments; each request arrives on standard input. It validates the SSH login and group, then invokes an unprivileged database worker. The installed release has no running application service.

## Salt-managed hosts

Provide `/etc/conductor/conductor.yaml` as a root-owned `0640` file readable by the `conductor` group. Set `database.engine` to `sqlite` or `mysql`, with `mode: existing` for MySQL. Keep the MySQL password in a separate private file and use TLS verification for TCP. Salt should install the same OS Python packages as above, manage SSH and group membership, and provide a reviewed source tarball with SHA256. Then run:

Build the pinned tarball from the reviewed Git commit using `git archive --format=tar HEAD pyproject.toml src scripts/ssh_command.py | gzip -n > conductor-ssh-source.tar.gz`, record its `sha256sum`, and place the tarball in Salt files. Do not include private YAML or password files in the tarball.

```bash
python3 scripts/install_ssh_host.py --config /etc/conductor/conductor.yaml --bundle /var/cache/conductor/conductor-ssh-SHA256.tar.gz --sha256 SHA256
```

Use the same command with `--check` for readiness. This installs code and the narrow sudo rule; no systemd unit is required. The [Salt example](../examples/salt/conductor-ssh/init.sls) shows the complete state shape. It does not grant general root access.

## Client setup

On Windows, download only `install-ssh-client.ps1` and follow the [one-file client setup](ssh-client-quickstart.md). No repository checkout or ZIP is needed on the user's computer. From the folder where the file was saved:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-ssh-client.ps1
```

Enter the SSH alias, hostname or IP. The script contains its own command wrapper and skill. The installer tries the user's existing SSH configuration first. A login already specified in `~/.ssh/config` is honored, including `ProxyJump` or `ProxyCommand`; a direct `login@host` is also accepted. The installer verifies the VM command, then writes only `conductor.ps1`, the skill and a host alias into the current user's Codex folder. For a recognized previous Conductor MCP installation, it backs up the old skill and Codex config, replaces the skill in place, and removes only that old Conductor MCP entry. Unrecognized or modified entries are preserved with a clear error. Codex desktop and VS Code can use the same skill. Open a new chat after setup. The normal user shell runs each command with that user's SSH agent, keys and enterprise login path. If an access system requires a fresh interactive challenge for every SSH process, that challenge may need to be completed for each ledger call or handled through the organization's SSH session policy.

For isolated acceptance, supply `-TestRoot PATH`; normal Codex files are not changed. Linux/macOS users can install `client/conductor.sh`, the skill and a `host.txt` file under `~/.codex/conductor-client/` using the same SSH alias. A packaged Linux/macOS installer is not yet included.

## Agent workflow and limits

The skill decides when the main agent should work directly and when distinct workstreams justify native subagents. It supplies the command contract only when coordination is needed. The VM command does not schedule or supervise workers. The manager must hold a lease, record exact native worker IDs, renew ownership, check worker evidence and report verified results. CLI errors have stable JSON codes; use the same `operation_id` when checking an uncertain write.

SSH and the OS enforce the initial identity. Removing `conductor-enroll` membership blocks new command calls. Disabling a user's SSH access also blocks calls. The old copied Conductor keys used by the previous MCP path are independent; revoke them when retiring that path. The previous MCP service may run alongside this command during migration, but the SSH command does not call it or require its packages.
