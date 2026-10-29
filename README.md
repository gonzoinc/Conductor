# Conductor

**One task stays with the lead. Distinct workstreams get coordinated.**

![The Conductor logo above a lead agent directing distinct workstreams](assets/conductor-hero-with-logo.png)

Conductor gives Codex agents a shared, durable record of delegated work. The lead agent decides whether a task needs workers, records assignments and evidence when it does, and reports the verified result. Your project files, Codex sign-in, and native worker controls remain on your computer.

## What changes when work splits

| One coherent task | Several distinct workstreams |
| --- | --- |
| The lead agent does the work directly. No worker or ledger setup is needed for that task. | The lead agent becomes the coordinator. It assigns bounded work to native Codex agents, tracks dependencies and exact worker IDs, checks their evidence, and reports what was completed. |

For example, a single documentation correction stays with the lead. Updating an installer while independently reviewing its safety can use separate workers, with the lead integrating their results.

The ledger stores task briefs, ownership, dependencies, results, and concise evidence. It does **not** store your repository, run models, or provide worker file isolation.

## What has been verified

The local Windows installer completed in a user-led Codex desktop and VS Code setup. Automated service and installer checks passed, along with a bounded native desktop-session smoke using separate authors and independent validation. The [acceptance record](docs/acceptance.md) records the other deployment checks and their limits.

### Measured worker handoffs

In one controlled five-worker comparison, complete worker handoffs and predefined test commands reduced **total recorded tokens by 23.9%**. The tasks, model, reasoning effort, roles, concurrency setting, and acceptance tests were the same; both runs passed.

| Measurement | Before | Updated handoffs |
| --- | ---: | ---: |
| Total tokens, lead plus workers | 1,639,744 | **1,247,639** |
| Model requests | 46 | 43 |
| Runtime | 3m 31s | 3m 41s |
| Uncached input tokens | 38,244 | 62,126 |

Workers received source boundaries, environment facts, exact commands, and completion requirements in one packet. This is one exploratory pair, not a guaranteed saving: runtime rose by ten seconds, uncached input increased, and total-token savings do not establish equivalent billing or account-allowance savings. See the [results, controls, and reproduction steps](docs/token-benchmark-results.md).

## How coordination works

1. The lead agent decides whether to work directly or assign distinct workstreams.
2. For delegated work, the coordinator skill records the brief, dependencies, exact worker IDs, and manager ownership in the ledger. Codex itself runs the workers.
3. Workers return results and evidence. The lead checks the real output before reporting completion. Implementation work requires independent review **or** test coverage plus integration coverage.

Depending on setup, Codex reaches the ledger through a local MCP bridge or a command in the user's SSH session. A hosted VM can serve multiple accounts; each account sees its own work. Manager leases reject conflicting or stale writers, and operation IDs support safe retries.

## Choose a setup

- **Local Windows:** one person, no VM. Download the [Windows installer](https://github.com/gonzoinc/Conductor/releases/tag/v0.4.0), choose Codex desktop, VS Code, or both, and keep task data in a separate folder. Setup downloads a private Python runtime and runs the ledger only when Codex uses it.
- **Local macOS:** one person, no VM. Needs Python 3.11+, internet for package installation, and a stable local checkout. The SQLite ledger runs through an MCP bridge only while Codex uses it.
- **Shared Linux VM + Windows client:** a team or multiple computers. The Windows client uses an authorized SSH login and installs one command and the skill. It needs no local Python, MCP package, copied service key, or background tunnel.

The [older hosted MCP path](docs/client-setup.md#single-script-windows-hosted-setup) and [guided command-line wizard](docs/guided-setup.md) remain documented for existing setups and migration.

### Local Windows

Download `ConductorSetup.exe` from the [latest release](https://github.com/gonzoinc/Conductor/releases/tag/v0.4.0) and run it as your normal Windows user. The app guides you through the install folder, task-data folder, and Codex clients. See the [Windows setup guide](docs/windows-gui-setup.md) for the full walkthrough.

### Local macOS

From a stable checkout on your Mac:

```bash
git clone https://github.com/gonzoinc/Conductor.git
cd Conductor
bash install-macos.sh
```

The installer creates a private local ledger and bridge, adds the Conductor connection and skill to your user profile, and checks tool discovery and health. Keep the checkout in place. Then restart Codex desktop, reload VS Code, and start new chats. The installer does not sign in to Codex or prove that your client can dispatch native workers. See [macOS setup and verification](docs/client-setup.md#local-macos-command-line-setup).

### Shared Linux VM and Windows Codex

On the Linux VM, clone this repository and install the SSH command service:

```bash
git clone https://github.com/gonzoinc/Conductor.git
cd Conductor
bash install-ssh-vm.sh
```

The VM uses local SQLite or an existing MySQL database. Its administrator grants each user SSH access and membership in the configured enrollment group. The VM installer sets up the ledger command; Codex and its workers still run on each person's computer. See [VM deployment](docs/ssh-command.md) and [account access](docs/accounts.md).

On Windows, [download the one-file client installer](https://raw.githubusercontent.com/gonzoinc/Conductor/main/install-ssh-client.ps1), then run it from the folder where you saved it:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-ssh-client.ps1
```

Enter your VM SSH alias, hostname, or IP and use your normal SSH sign-in. When setup says **ready**, start a new chat in Codex desktop or VS Code. The [Windows walkthrough](docs/ssh-client-quickstart.md) includes the connection check and retry steps.

### Update an installed client

Finish active Conductor work first. These worker-handoff changes update the client skill; they do not require a VM or database upgrade.

**Windows with the SSH client:** download and rerun the latest one-file installer:

```powershell
Invoke-WebRequest 'https://raw.githubusercontent.com/gonzoinc/Conductor/main/install-ssh-client.ps1' -OutFile "$HOME\Downloads\install-ssh-client.ps1"
powershell -ExecutionPolicy Bypass -File "$HOME\Downloads\install-ssh-client.ps1"
```

It reuses the saved SSH alias, checks access, backs up the installed skill, and installs the new command and skill. For an isolated test profile, pass the same `-TestRoot` as before. See [Windows update details](docs/ssh-client-quickstart.md#update-an-existing-windows-ssh-client).

**Mac with a local checkout:** in your existing Conductor checkout, run:

```bash
git pull --ff-only
python3 -B scripts/update_skill.py
```

The updater backs up a known installed skill and replaces only that skill. It preserves the local database, MCP configuration, credentials, and Python environment; it stops if the installed files were customized. Keep the checkout in place. For a Windows local MCP installation, use `git pull --ff-only` followed by `py -3 -B scripts/update_skill.py` instead of the SSH installer. Custom profiles can use `--codex-home PATH` and `--skills-dir PATH`. The new updater has automated file-update tests; actual macOS execution is still a manual acceptance check.

After updating, restart Codex desktop and reload VS Code, then start new chats using the same updated user profile.

## Operate and extend

Conductor cannot keep a manager alive after its client closes, guarantee model identity, stop a native worker on its own, or isolate worker files. Reconcile the original worker before recovery or replacement; restoring ledger metadata does not stop live work.

The [service operations guide](docs/operations.md) covers backups, restore, upgrades, and recovery. Hosted SQLite should live on local persistent storage; shared MySQL needs a dedicated schema, verified transport, and database-managed backup and failover. Salt examples are under [`examples/salt/`](examples/salt/). The service does not need your Codex credentials or model API keys.

Before a public change, run the [public-material check](scripts/check_public_tree.py) and the [Windows installer payload check](scripts/build_windows_installer.py). These reduce accidental disclosure but do not replace review of Git history and release assets.

## Provenance and license

Conductor draws on [Firstmate](https://github.com/kunchenguid/firstmate) for its normalized status-transition policy and coordination concepts. The pinned references are in [`upstream-lock.json`](upstream-lock.json), with the upstream MIT notice in [`UPSTREAM_LICENSE.txt`](UPSTREAM_LICENSE.txt). Conductor's stores, leases, service, bridge, packaging, and Codex workflow are separate integration code; it does not run Firstmate's terminal fleet or adapters.

Conductor is licensed under [MIT](LICENSE).
