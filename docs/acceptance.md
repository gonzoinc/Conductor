# Acceptance before wider distribution

Automated backend/bridge tests do not demonstrate that a particular desktop or VS Code version can dispatch native workers. Record the actual client version, native capability result, workload ID and outcome for each real-client run. A `client=vscode` metadata label alone proves nothing about where the test ran.

## Recorded candidate checks: October 24–27, 2026

| Check | Actual result |
| --- | --- |
| Linux ARM64, Python 3.13, complete source suite | 67 tests: 66 passed and the Windows-only ACL check skipped. |
| Windows, Python 3.14, client suite on NTFS | 14 tests: 13 passed and the POSIX-only check skipped. Private ACL creation, broad-read rejection, real stdio initialization and repeated installer commands passed. |
| Independent fresh container build | Healthy nonroot service; read-only root filesystem, dropped capabilities, loopback publishing and private database/credential permissions checked. |
| Installed bridge to the actual container backend | MCP initialization, 14-tool discovery, authenticated health, idempotent claim, conflicting-manager rejection and task completion passed. Existing unrelated TOML survived installation; an identical rerun changed nothing. |
| Persistence and recovery of storage | Restart preserved records; repeated setup preserved database and credential hashes; online backup and isolated restore preserved records and integrity. Existing restore destinations were rejected. |
| Single setup entry point | `install.sh` passed syntax/interpreter checks in Linux Bash and Windows Git Bash, including a checkout path with spaces and native Windows Python selection. |
| Setup orchestration checks | 14 targeted tests passed on Linux local/tmpfs storage and 14 on Windows NTFS. Cancellation/failure propagation, isolated defaults, profile preservation, retry, private export parent creation and explicit user-install opt-in were checked. |
| Complete Windows shell-entry-point run | Separate desktop and VS Code test homes and project-local skills were created using a private synthetic credential. An identical desktop rerun preserved configuration/settings hashes, no credential appeared in output, and normal user configuration and the existing coordinator skill were unchanged. No frontend was launched. |
| Complete Linux shell-entry-point run | A separate named Compose project built, initialized and reached healthy status through one command. The same entry point exported its live credential without echo into a private local file, creating its parent mode 0700 and file mode 0600. An insecure NAS destination was refused. Owned containers, volumes, network and image tag were removed. |
| Guided first-time-user checks | No-argument entry opens an explanation/menu without requiring a key. Missing-host/prerequisite/file routes, service-before-client ordering, pause/resume, error redaction, client isolation and guarded launch construction were checked. Windows: 35 targeted checks passed (15 entry-point + 20 guided). Linux local/tmpfs: 35 tests, 33 passed and two Windows-only checks skipped. |
| Real guided Windows-to-Linux SSH install | From a separate working checkout, the wizard verified an existing Linux host's prerequisites, transferred a source bundle without Git-host credentials, created a separate healthy service, generated/exported/transferred its private key, established key-authenticated SSH forwarding and prepared desktop/VS Code profiles. Each actual stdio MCP bridge initialized, exposed 14 tools and passed authenticated health through the new service. No key appeared in installer output. |
| Guided interruption/retry | The created service was preserved while fixing an installer check. Resume reused its saved connection instead of rebuilding/replacing the service. Completed-profile reopen reused the prepared runtime; rerunning the client stage preserved normal configuration and the existing coordinator skill. Conductor source/runtime validation did not open either frontend. |
| Independent instruction forward-test | Seven task-routing, bounded-worker, capability and recovery scenarios passed. This checks decisions from the skill, not a frontend installation. |
| Native desktop-session fixture smoke | Two exact native authors, independent byte validation and integration cleanup completed through the new service's gates. Premature completion was rejected. See the bounded evidence below. |
| Fresh Linux VM installer, October 27 | `bash install-vm.sh` installed on an Ubuntu 24.04 guest with 2 vCPUs, 2 GiB RAM and local storage. The first attempt exposed an executable-bit assumption; the next exposed release directory permissions under umask 077. Both were fixed. The corrected rerun recovered the interrupted release and started `conductor.service`; another rerun returned `unchanged`. Three distinct account keys passed live authenticated health, nine account/isolation tests passed, the initial key exported mode 0600 into a mode 0700 private directory, and a revoked key was rejected with HTTP 401. Repository checkout authentication and a real hosted Codex client remain unverified. |
| MySQL backend, October 27 | Against an isolated MySQL 8.0 schema on a disposable VM, two accounts used the same public workload ID without seeing each other's data. Issued key authentication and revocation passed. Two simultaneous managers contesting one account/workload yielded exactly one lease owner and one conflict. The same checks passed against a temporary dedicated MariaDB 10.11 schema over a local Unix socket. A clean package install and authenticated HTTP health check passed there. An opt-in Salt test state and cleanup state both applied successfully; the temporary schema and account were removed. A Salt-managed Conductor service using MySQL, remote TLS connection, and failover between two service nodes remain unverified. |
| Single-script hosted Windows client, October 27 | A clean isolated install from a private release ZIP asked for only the VM address, found the authorized SSH login and exported key, installed the client, and configured desktop and VS Code profiles. Both profiles opened their own SSH tunnels and completed authenticated MCP health and 14-tool discovery against the live test VM. A rerun of the Git-checkout route reported no config changes. The temporary transferred key was removed. An initial HTTP 500 exposed a VM installer rerun that reset `/etc/conductor` permissions; the host installer was corrected and the VM service updated. A first bridge attempt exposed Windows OpenSSH's need for `PROGRAMDATA` after the MCP stdio launcher filtered its environment; the bridge was corrected. This October 27 hosted-client run did not open the Codex frontends. |
| Local Windows GUI installation, October 29 | The user completed installation for Codex desktop and VS Code after the existing-folder, configuration-merge and permission-step fixes, and confirmed the setup works. The packaged GUI self-test and 12 targeted setup tests passed before release. |

Native smoke workload: `portable-native-desktop-20261024`, epoch 1, completed with its lease ended. The default 300-second lease and renewal were exercised. The graph was recorded before dispatch, and readiness was observed before steering each worker after its exact ID was bound.

| Recorded task | Exact native worker ID | Evidence |
| --- | --- | --- |
| Author A | `/root/portable_integrator/fixture_a` | Exclusive no-overwrite creation of the first 20-byte ASCII fixture. |
| Author B | `/root/portable_integrator/fixture_b` | Exclusive no-overwrite creation of the second 20-byte ASCII fixture in a separate workspace. |
| Independent test | `/root/portable_integrator/fixture_check` | Read both actual files; exact expected bytes, final LF and no BOM. |
| Integration | `/root/portable_integrator/fixture_check` | Sequential assignment after test completion; rechecked hashes, removed only the two fixtures and empty owned directories, and verified absence. This actor differed from both authors. |

Independent fixture hashes were `a16e61f1cec0808436c7980318ffe6312c720a3c5533ac080b75b08924aa47f9` and `56b232e133157969b15341f4083174f290ccf868388c7bfb6b5aa9322ca5f74a`. The manager performed no fixture inspection, editing, validation or cleanup. All four required task records completed, and the backend accepted workload completion only after independent test and integration coverage existed.

This earlier native smoke used available desktop-session controls plus explicit authenticated metadata calls to an isolated candidate service. It did not establish automatic MCP discovery in a newly configured frontend, and the desktop application version was not captured. The later local Windows GUI installation was confirmed by the user. Cross-client handoff and macOS installation were outside this native smoke.

## Quick setup check

Run this once in each actual Codex client you plan to use. It creates no files and limits native delegation to two tiny scout tasks:

> Use $conductor-coordinator for a quick setup check. Keep it brief and create no files. First call coordination_health. Then answer 2+3 yourself, without a worker or ledger. Next create one disposable workload with two independent scout tasks: ask exactly two native workers to answer 7+5 and 9-4, one each. Record their returned IDs and one-line results, then complete the workload. Do not add reviewers, integrators, tests, or other workers. If health, native workers, or completion is unavailable, stop and report the blocker without expanding the test. Reply in at most four lines: health, direct answer, worker IDs/results, workload status.

Expected: healthy connection, a direct answer from the main agent, two exact native worker IDs and short results, and a completed workload. If native workers or the ledger are unavailable, the agent reports that limit without starting more work. This is a setup smoke test. Release validation of implementation coverage, integration, cleanup, recovery, and cross-client behavior remains separate.

## Recovery exercise

For a separate disposable workload, pause after recording a native worker. Deliberately hand off or reconnect the manager, inspect persisted state, and reconcile that exact worker through its original supported controls. Confirm it stopped before any recovery/replacement. Verify stale epochs and a second live manager are rejected. An unavailable worker in the new client must produce uncertainty rather than duplicate dispatch.

Only claim recovery acceptance after actually performing it. Do not interrupt active user work to run this exercise. Service restart persistence and database restore tests cover storage, not cross-client worker adoption.

## Release gate

Before any public release: choose integration-code licensing, retain upstream attribution, review the entire source/history for private information, repeat clean setup on declared supported platforms, and record separate desktop/VS Code acceptance. Repository visibility stays private until the owner explicitly authorizes changing it.
