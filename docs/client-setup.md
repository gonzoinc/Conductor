# Connect Codex

Codex needs its own sign-in and native subagent controls. The stdio MCP bridge can use a private SQLite ledger on this computer, or connect to a hosted service. Local mode needs no VM, container, listener, or service key; the bridge runs only while Codex uses it. Hosted mode reads a private per-user key file. No bearer token is stored in TOML or passed as a command argument.

Automated client setup and stdio protocol checks cover Linux and Windows with local private storage. Windows tests include private ACL creation and rejection of a broadly readable credential. An isolated macOS/Python 3.14 install and unchanged rerun passed, including discovery of 14 tools, coordination health, configuration preservation and backup checks. The macOS command below verifies its installed stdio bridge before reporting success. These checks do not establish actual desktop or VS Code native delegation acceptance.

## Start here if this is your first installation

For a single user on Windows, use the [local-only graphical installer](windows-gui-setup.md). For a shared VM, first run [the VM installer](host-install.md) on that Linux host, then give each client user their own exported key file. The hosted client CLI below connects Codex to the running service; the Windows GUI does not install or connect the VM edition. The older guided command-line wizard is documented in the [guided walkthrough](guided-setup.md).

Choose **New installation**, then **On this computer** for one-person local mode or **Linux host** for a shared service. The remaining sections describe advanced/manual provisioning and configuration locations.

## Local macOS command-line setup

From a stable checkout on a local disk, run `bash install-macos.sh` as your normal user. It installs the fully local, single-user edition into your normal Codex profile for both desktop and the VS Code extension. It requires macOS, Python 3.11+ with `venv`/`pip`, and internet access for package downloads. It does not require sudo or configure a background service. If `python3` selects an older interpreter, use `CONDUCTOR_PYTHON=/path/to/python3.12 bash install-macos.sh` to choose an installed compatible Python.

The script uses the existing client installer and creates:

- A private Python environment in the checkout's `.codex/conductor-venv` directory. Keep the checkout and this environment in place because the connection records their absolute path.
- A `conductor_coordination` MCP entry in `~/.codex/config.toml`, or `$CODEX_HOME/config.toml` when set. Both desktop and the VS Code extension use that user configuration; no separate VS Code MCP entry is needed.
- The self-contained skill in `~/.agents/skills/conductor-coordinator` and private connection settings under the selected Codex home's `conductor-coordination` directory.
- A private SQLite ledger at `${XDG_STATE_HOME:-~/.local/state}/conductor/tasks.sqlite3`, shared by both clients. Keep it on a local disk outside synced/network folders.

Existing unrelated configuration is preserved byte for byte and backed up to `config.toml.before-conductor` when an existing file changes. An identical rerun preserves settings and ledger data. A conflicting Conductor connection, skill, settings file, or unowned nonempty data directory stops installation for manual review. Resolve the conflict before rerunning; the script does not silently replace it. A failed package download can be retried with the same command.

Before reporting completion, the script starts the installed bridge, initializes a real stdio MCP session, discovers its 14 tools and calls `coordination_health`. This does not open or restart either app. After active work is finished, restart Codex desktop, reload the VS Code window and start new chats. If using a custom `CODEX_HOME`, ensure both applications actually run with that same profile; exporting it in a terminal does not change an already-running app. Run the [quick setup check](acceptance.md#quick-setup-check) in each client to verify native worker support. Installation cannot enable missing native controls or override organization policy.

## Local mode on this computer

The guided installer creates a private ledger under `%LOCALAPPDATA%\Conductor` on Windows or `${XDG_STATE_HOME:-~/.local/state}/conductor` on Linux. You can select another directory on a local disk. Keep the database on the same computer as Codex; SMB, NFS, UNC, and symbolic-link data paths are refused. The installer creates a private ownership marker and refuses to adopt an unrelated nonempty data directory. Isolated desktop and VS Code profiles may point to the same owned local ledger, so their work persists across chats and is visible to both profiles. This is one local user's ledger; OS account permissions protect it from other users on the machine.

Advanced one-line example from the checkout, using an isolated desktop profile:

```bash
bash install.sh client --mode local --profile desktop
```

The local bridge opens SQLite directly for each MCP operation. SQLite WAL and transactional writes support multiple Codex bridge processes on the same computer; manager leases still prevent conflicting ownership of one workload. No process stays running after the clients close. Back up the database with SQLite's backup API while it is active; copying only `tasks.sqlite3` can omit uncheckpointed WAL data. macOS uses the POSIX state-directory convention; keep the ledger on local storage.

## Install on the computer running Codex

### Single-script Windows hosted setup

Download the complete repository ZIP, extract it to a local folder, and run `powershell -ExecutionPolicy Bypass -File .\connect-hosted.ps1` from that folder (or right-click the script and select **Run with PowerShell**). A local Git checkout also works. Enter only the VM hostname or IPv4 address. The script uses the SSH login already configured for that host; it also checks the Windows username and the common `ubuntu` image login. On first connection, the VM enrolls that authenticated SSH login, creates a distinct Conductor account and key, and exports the key to that login's private home directory. The installer copies it privately to the computer. The VM administrator must authorize the SSH identity, establish SSH host trust, and grant that login membership in the configured `conductor-enroll` group (or the custom `enrollment_group`). Those are VM access tasks, not extra installer questions. Existing SSH keys or SSH agent identities are used automatically; the installer does not ask for a private-key path.

The script copies a clean Git commit to a stable local installation folder, obtains a private Python runtime if needed, validates the key and live service, and installs one shared Codex MCP connection and skill for desktop and VS Code. Each bridge process starts its own private SSH loopback tunnel and closes it on normal exit. No long-running PowerShell window or separate tunnel command is needed. Existing conflicting Conductor configuration is preserved and causes a clear failure. Restart Codex desktop and VS Code after installation. The VM controls SSH and enrollment group membership; future directory or SSO policy can manage that membership without client changes.

Use Python **3.11+**. Clone this repository from a URL you can access and keep the clone and its environment at a stable path. The installer records the Python executable's absolute path. The hosted examples below require a running service and a private key file issued to you by its administrator.

The single setup entry point creates its managed Python environment and installs this package before configuring the client. Use a checkout on a local disk. It refuses to reuse an unrelated pre-existing environment.

Linux/macOS, isolated desktop profile:

```bash
bash install.sh client --profile desktop --token-file /path/to/private/service-token
```

Windows PowerShell with Git Bash installed, one line:

```powershell
& "C:\Program Files\Git\bin\bash.exe" .\install.sh client --profile desktop --token-file "C:\private\service-token"
```

Git Bash selects native Windows Python (`py.exe -3` or `python.exe`) and converts path arguments before invoking it. Do not substitute WSL Python for a Windows-native frontend installation. The repository enforces LF line endings for shell scripts, including on Git for Windows checkouts.

Use `--profile vscode` for the separate VS Code profile. Client mode is isolated by default; `--isolated` is an optional explicit spelling. Files live under the checkout's `.codex/conductor-acceptance/PROFILE`: `home` contains test configuration/private client settings, and `workspace/.agents/skills` contains the self-contained skill. The Python environment lives in `.codex/conductor-venv`. `--test-root` can select another dedicated test directory. A pre-existing unowned profile is preserved and setup stops.

The test configuration uses file-based Codex authentication so it does not request the normal Codex credential store. Do not copy normal auth, configuration, plugins or skill folders into a fresh profile. Sign in through the isolated frontend when it is launched. Existing user configuration and skills remain untouched by default.

**An installer profile does not automatically isolate a running app.** A test frontend must actually use its reported Codex home; VS Code additionally needs a separate user-data directory and extension directory. Desktop additionally needs separate application data. Do not assume VS Code switches apply to desktop, or that changing a shell variable changes an already running application. Verify active paths before sending acceptance prompts. Desktop launch isolation must be verified for the installed application version, or use a separate OS test account/VM. This entry point does not launch, close or restart either frontend.

For an ordinary user installation, deliberately opt in after reconciling active work:

```bash
bash install.sh client --configure-user --token-file /path/to/private/service-token
```

The existing lower-level `scripts/install_client.py` remains available for custom provisioning; manual virtual-environment and pip commands are no longer the normal setup path.

On the Linux service host, export its credential to a new file. The entry point creates a missing private parent directory (mode `0700`) and refuses insecure existing permissions:

```bash
bash install.sh server --export-token /path/to/private/service-token
```

The helper refuses overwrites and does not print the credential. Transfer that file using your existing secure administrative route. Keep it out of chat, source control, screenshots and shell history. On POSIX, make the transferred file mode `0600`. The lower-level Python installer also supports a hidden prompt; the shell entry point requires a private file so redirected shell input cannot cause a credential to echo. The installer copies it privately and does not print it. It sets private modes on POSIX and uses current-user Windows ACLs for its own credential/settings files. It refuses a second skill copy if a legacy `conductor-coordinator` already exists under the Codex home.

When using `--configure-user`, this adds the `conductor_coordination` MCP entry in `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`) and copies the self-contained `conductor-coordinator` skill to `~/.agents/skills`. The lower-level Python installer supports `--codex-home` and `--skills-dir`; the shell entry point selects both automatically for isolated profiles. It preserves other settings and makes a private `config.toml.before-conductor` backup when modifying an existing configuration.

An identical rerun makes no config changes and does not rotate the token. A conflicting server, credential, settings file or skill stops installation for manual review. Existing user files are not silently replaced. Keep the source token file only as long as needed for your secure provisioning/recovery process.

Open a new chat or reload the client when no active work would be interrupted. The installer does not restart clients, alter model settings, enable hidden features or sign in for you.

## Service on another VM

The default service listens on VM loopback. On the Codex computer, keep this tunnel running while using the service:

```bash
ssh -N -L 8012:127.0.0.1:8012 VM_USER@VM_HOST
```

The installer defaults to `http://127.0.0.1:8012/mcp`. For a separate test service/tunnel using port 48958, explicitly pass `--url http://127.0.0.1:48958/mcp` so the profile points to that test instance. For an existing HTTPS reverse proxy, pass `--url https://coordination.example.com/mcp`. The bridge verifies TLS through the system trust store, refuses redirects and avoids ambient HTTP proxies. Remote plaintext HTTP is rejected; use a tunnel rather than exposing the bearer on the network. A tunnel protects transport; the hosted service separately checks each user's account key on every request.

## Check before real use

Run the single [quick setup check](acceptance.md#quick-setup-check) in each actual desktop and VS Code client you intend to use. Backend tests and an MCP connection do not establish native worker availability. The prompt briefly checks direct execution and native delegation without creating project files.

## Manual connection alternative

The service accepts direct authenticated HTTP MCP connections. Codex supports:

```toml
[mcp_servers.conductor_coordination]
url = "http://127.0.0.1:8012/mcp"
bearer_token_env_var = "CONDUCTOR_COORDINATION_BEARER"
```

The environment variable must exist in the actual Codex host process. Setting it in a separate terminal does not modify an already-running desktop or extension process. Use either this direct connection or the installer-managed bridge, not duplicate entries to the same ledger.

Official references: [MCP configuration](https://learn.chatgpt.com/docs/extend/mcp), [native subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [skills](https://developers.openai.com/codex/skills/).

## Removal or relocation

Stop using the workflow and reconcile active workers before removal. Remove only the `conductor_coordination` MCP table, the installed `conductor-coordinator` skill and its private `conductor-coordination` settings directory after reviewing their ownership. Preserve unrelated configuration, and keep the backup until no longer needed. Removing the client does not delete the server database or stop native workers.

Moving/deleting the clone's virtual environment breaks the recorded executable path. Review/remove the old entry before reinstalling with the new path; the installer intentionally refuses to overwrite a different connection.
