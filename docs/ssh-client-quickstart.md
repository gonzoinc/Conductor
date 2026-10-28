# Connect Codex to a hosted Conductor VM

This is the complete Windows client setup. Your administrator must already have installed Conductor on the VM and given your normal Linux SSH login access. You need Windows OpenSSH (`ssh.exe`) and your usual SSH sign-in method. You do not need Git, Python, pip, MCP, a copied service key, or the repository on this computer.

1. Open the Conductor repository in your normal browser. Download the raw `install-ssh-client.ps1` file and save it. Do not copy the file's rendered preview text; download the raw `.ps1` file.
2. Open PowerShell in the folder containing the downloaded file. Run this one command:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install-ssh-client.ps1
   ```

3. When asked, enter the VM SSH alias, hostname or IP address. If your SSH configuration already names the login or jump host, use that alias. Otherwise, you can enter `login@host`. SSH may ask you to trust the host or complete your normal sign-in. The installer checks that your login can use Conductor before changing Codex files.
4. When it says **ready**, open a new chat in Codex desktop or the Codex VS Code extension. Both use the skill installed for this Windows user. The main agent handles one coherent task itself and coordinates native workers only for distinct workstreams.

The installer writes a small command and skill under your user profile. It does not install a local database, Python packages, MCP server, background service or separate SSH key. It uses the existing SSH account and access rules each time Codex needs the shared ledger. If it cannot connect or you are not authorized on the VM, it stops before installing client files.

For a simple connection check after setup, run:

```powershell
& "$HOME\.codex\conductor-client\conductor.ps1" -Action coordination_health
```

If setup stops, read the error shown in PowerShell, fix the SSH login or VM access, then run the same installer file again. No clone or new download is needed unless the file itself is updated.

## Update an existing Windows SSH client

Finish active work. Download the newest raw installer and run it again:

```powershell
Invoke-WebRequest 'https://raw.githubusercontent.com/gonzoinc/Conductor/main/install-ssh-client.ps1' -OutFile "$HOME\Downloads\install-ssh-client.ps1"
powershell -ExecutionPolicy Bypass -File "$HOME\Downloads\install-ssh-client.ps1"
```

It reuses the saved SSH alias, checks access, backs up the existing skill under
`~/.codex/conductor-client/skill-backups/`, then installs the current payload.
For an isolated profile, supply the same `-TestRoot` as before. It does not
upgrade the VM database. Restart desktop/reload VS Code after active work ends
and start new chats to load the new skill.
