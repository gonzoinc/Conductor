# Hosted accounts and private workloads

A hosted Conductor service can serve multiple people. Each person has an account
with one or more independent bearer keys. The server stores SHA-256 hashes of
keys, not the key text. It checks the key on every request, so revoking a key
stops subsequent requests, including health checks. The separate health-only
key used by service monitoring cannot call the MCP tools.

Workloads, tasks, and events belong to one account. They are private by default;
another account cannot list or read them. An unknown workload and another
user's workload return the same not-found result. Separate users may choose the
same workload ID. The service prevents overlapping implementation or
integration workspaces within one account. It does not protect a shared
repository across separate accounts from concurrent edits; coordinate that
outside Conductor until an explicit sharing policy exists. Host administrators with access to
the SQLite file or MySQL schema can inspect all metadata, so the service and backups need
the same protection as other private work records. Account sharing and an
administrative web UI are not implemented. `owner` is reserved for the service
bootstrap and cannot be created as a normal user or given a normal user key.

## VM account administration

After `bash install-vm.sh`, run these commands from the same checkout on the Linux VM. They use sudo, then run the account tool as the dedicated service user. Commands print account metadata and private file locations, never key values:

```bash
bash conductor-users.sh add alice
bash conductor-users.sh issue alice desktop
bash conductor-users.sh issue alice vscode
bash conductor-users.sh export alice desktop
bash conductor-users.sh revoke KEY_ID
```

`add` creates a user and an initial key. `issue` creates another independently revocable key for an existing user. `export` copies one active key into the invoking Linux administrator's private `.conductor-keys` directory for secure transfer to that person's Codex computer. It refuses to overwrite an existing export. Do not give two people the same key; users on one account intentionally share that account's workload visibility.

For advanced/Salt installations, run the account module **on a Linux service host** as the Conductor service account. The directory containing any output key must already exist with mode `0700`. Commands write keys to mode `0600` files and print only metadata. `CONDUCTOR_CONFIG` selects the same YAML database settings used by the service; this works for SQLite and MySQL. Do not use a network filesystem for live SQLite state.

```bash
sudo -u conductor env CONDUCTOR_CONFIG=/etc/conductor/conductor.yaml /opt/conductor/current/.venv/bin/python -m conductor_coordination.accounts create-user alice
sudo -u conductor env CONDUCTOR_CONFIG=/etc/conductor/conductor.yaml /opt/conductor/current/.venv/bin/python -m conductor_coordination.accounts issue-key alice --label desktop --output /var/lib/conductor/private/users/alice-desktop.token
sudo -u conductor env CONDUCTOR_CONFIG=/etc/conductor/conductor.yaml /opt/conductor/current/.venv/bin/python -m conductor_coordination.accounts revoke-key KEY_ID
```

For SSH clients, the VM installer adds its initial Linux login to the
`conductor-enroll` group. Grant other authorized SSH logins membership in that
group through VM user management or Salt policy. Their first
`connect-hosted.ps1` run calls the narrow VM enrollment helper. The helper
derives the Conductor account from the authenticated SSH login, creates one
private key per login, and exports it into that login's `~/.conductor-keys/`
folder. The Windows installer copies that key into its protected client
configuration. Reconnecting reuses the same active key. Removing group
membership blocks new enrollment; revoke an already-issued Conductor key to
terminate existing access.

`create-user`, `bootstrap`, and `issue-key` with the same existing output file
are safe to repeat. `issue-key` refuses to replace an existing file that is not
an active key for that account and label. It prints a key ID for later
revocation. Copy each user's key file privately to only that user's client;
configure that client to load the file as its bearer credential. Remove the
client's old key file after confirming a replacement key works. Key revocation
does not delete a key file or cancel a native worker already running in Codex.

For an existing single-key Conductor database, reuse the existing service token
as `--owner-token-file` during bootstrap. Existing workloads stay with the
owner account, and the token remains valid. The schema is extended in place in
SQLite transactions. Take an online backup before upgrading a real service.
The original separate Firstmate deployment is unaffected.
