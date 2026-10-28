# Conductor CLI: use this user's existing SSH client, config, agent and jump hosts.
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^coordination_[a-z_]+$')][string]$Action,
    [hashtable]$Arguments = @{},
    [string]$HostAlias,
    [string]$ConfigRoot
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $HostAlias) {
    $root = if ($ConfigRoot) { $ConfigRoot } else { Join-Path $HOME '.codex\conductor-client' }
    $hostFile = Join-Path $root 'host.txt'
    if (-not (Test-Path -LiteralPath $hostFile -PathType Leaf)) {
        throw 'Conductor host is not configured. Run install-ssh-client.ps1 first.'
    }
    $HostAlias = (Get-Content -LiteralPath $hostFile -Raw).Trim()
}
if ($HostAlias -notmatch '^(?:[A-Za-z_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]{0,252}$') {
    throw 'Conductor host must be an SSH alias, hostname, IP address, or login@host.'
}
if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
    throw 'Windows OpenSSH Client is required.'
}

$request = @{ action = $Action; arguments = $Arguments } | ConvertTo-Json -Depth 16 -Compress
$bytes = [System.Text.Encoding]::UTF8.GetBytes($request)
if ($bytes.Length -gt 120000) { throw 'Conductor request exceeds 120 KB.' }
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = (Get-Command ssh.exe).Source
$start.Arguments = "-T -o ConnectTimeout=10 $HostAlias sudo -n /usr/local/lib/conductor-command"
$start.UseShellExecute = $false
$start.CreateNoWindow = $false
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$process = New-Object System.Diagnostics.Process
$process.StartInfo = $start
try {
    if (-not $process.Start()) { throw 'Could not start SSH.' }
    $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $process.StandardInput.Close()
    $output = $process.StandardOutput.ReadToEnd()
    $sshError = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0 -and -not $output) {
        throw "SSH or VM authorization failed: $($sshError.Trim())"
    }
    if ($output.Length -gt 200000) { throw 'Conductor response exceeds 200 KB.' }
    $reply = $output | ConvertFrom-Json
    if (-not $reply.ok) {
        throw "Conductor $($reply.error.code): $($reply.error.message)"
    }
    $reply.result | ConvertTo-Json -Depth 20 -Compress
} finally {
    $process.Dispose()
}
