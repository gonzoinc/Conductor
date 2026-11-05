# Conductor for Windows: graphical setup

`ConductorSetup.exe` installs the local, single-user ledger for Codex desktop and the Codex VS Code extension. It downloads a private Python runtime and Python packages during installation, so an internet connection is required. It does not ask for a Git-host token or a Codex API key. To install a shared service on a Linux VM, use [the VM installer](host-install.md) on that VM.

1. Run `ConductorSetup.exe` on Windows 10 or 11 as the same normal Windows user who runs Codex. Administrator rights are not needed. Running as another administrator account installs into that account's profile instead.
2. Pick an app folder and a separate local task-data folder (default: `%USERPROFILE%\.conductor`). Task data under AppData is rejected because Windows packaged apps can redirect it. No VM, Docker, SSH, or service key is needed.
3. Choose Codex desktop, VS Code, or both. **Separate test profiles** is checked by default and leaves normal Codex settings alone. Clear it only when you want Conductor added to your usual Codex configuration.
4. Select **Install Conductor**. Progress and errors appear in the app. Fix an error and select the same button again to resume. Cancel stops the current command and can be retried. A newer setup package can reuse an earlier Conductor-owned app folder; it stages updated source inside that folder and preserves the earlier source during setup.

The app bundles a pinned, checksum-verified `uv.exe` and uses it to download a pinned Python 3.12 runtime into the selected app folder. It then installs the local Conductor client and writes the selected Codex connections. Task data stays in the chosen local folder. The app does not start Codex automatically.

The private test profile contains its own Codex home and workspace under `test-profiles` in the chosen app folder. A normal running client will not use the isolated profile. The GUI can open an isolated VS Code window. It cannot reliably open a second isolated Codex desktop window while the desktop app is running. For desktop checks in the current app, select **Use Conductor in this Codex app** in the guided checks, then **Install Conductor in this Codex app** in the same window. Wait for setup to finish, restart Codex, and start a new chat. The first button only prepares the choices; the second applies the change.

## Shared service

The Windows GUI has no VM option. A Linux VM uses its own one-command installer to create the continuously running, multi-user service. Each person then connects with a private key through the [hosted client setup](client-setup.md); the local GUI does not configure hosted clients.

## Building the Windows app

From a Windows checkout, run `py -3 gui/build_windows.py .`. The script requires an installed Python 3.11+ and network access. It pins PyInstaller, downloads uv from its official release, verifies SHA256 for the archive and executable, and includes the maintained Conductor source tree. Build scratch and the resulting executable stay under the repository's primary `.codex/gui-build/` directory. The expected output is `.codex/gui-build/dist/ConductorSetup.exe`. Run setup and the client checks on the target machine after building.

## Repair an existing redirected local ledger

Changing the default does not move an existing ledger. Do not delete the old database or rerun setup into an empty folder to work around a missing-ledger error. The bridge and repeat installer refuse to recreate a configured missing database.

Close Codex Desktop, VS Code, and any other Conductor writers before repair. Use the new source with a Python environment containing Conductor's legacy MCP dependencies, as the same normal Windows user. The repair command is included in the GUI source payload. Supply the **physical existing** database path, a destination that does not yet exist, and every affected `client.json` (repeat `--settings` for separate profiles). Do not copy a live SQLite file with Explorer; it may have uncheckpointed WAL records.

```powershell
& '<Conductor Python>' '<updated source>\scripts\repair_local.py' --source '<physical existing tasks.sqlite3>' --destination "$HOME\.conductor" --settings "$HOME\.codex\conductor-coordination\client.json" --clients-stopped
```

For a redirected installation, the physical source is typically beneath `%LOCALAPPDATA%\Packages\<Desktop package>\LocalCache\Local\Conductor`. The tool checks the configured location and matching package-cache candidates. It refuses missing or ambiguous sources and existing destinations. It verifies source ownership and private permissions, holds a SQLite writer lock during backup, checks integrity and all schema/records at the destination, then switches settings using private atomic writes. The original ledger is retained. Interrupted repairs retain the destination and `.conductor-repair.json` journal for recovery; do not retry into that existing destination.

With clients still stopped, restore original settings using:

```powershell
& '<Conductor Python>' '<updated source>\scripts\repair_local.py' --rollback "$HOME\.conductor\.conductor-repair.json" --clients-stopped
```

Rollback preserves both databases and refuses to overwrite unrelated settings changes. If either ledger received writes after cutover, resolve those records before rollback or deleting either copy; rollback does not merge databases. A forced interruption between settings writes can leave profiles temporarily split: keep clients closed and use the journal to restore settings before restarting.

Installation or repair success means **actual-client verification is pending**. In actual Desktop and VS Code sessions, confirm `coordination_health` is discovered and succeeds. Create a bounded test workload in Desktop and read it in VS Code, write a change in VS Code and read it in Desktop, then restart both clients and repeat the read/health checks. Use the same workload ID throughout. Keep the old ledger and rollback journal until these checks pass. Backend checks alone do not establish shared physical storage.

macOS and Linux state-directory defaults are unchanged.
