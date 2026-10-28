# Connect Codex desktop and the Codex VS Code extension to a hosted Conductor VM.
# The VM enrolls an authorized SSH login and exports its key on first connection.
param(
    [string]$VmHost,
    [string]$TestRoot
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Require-Success([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed. No Codex connection was reported as complete." }
}

if (-not $VmHost) { $VmHost = Read-Host 'Conductor VM hostname or IP address' }
if ($VmHost -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]*$' -or $VmHost.Length -gt 253) {
    throw 'Enter only a VM hostname or IPv4 address.'
}
if (-not (Get-Command ssh -ErrorAction SilentlyContinue) -or -not (Get-Command scp -ErrorAction SilentlyContinue)) {
    throw 'Windows OpenSSH (ssh and scp) is required. Install the Windows OpenSSH Client and retry.'
}
$checkout = Split-Path -Parent $PSCommandPath
$safeCheckout = $checkout.Replace('\', '/')
$gitRelease = $false
if ((Get-Command git -ErrorAction SilentlyContinue) -and (Get-Command tar -ErrorAction SilentlyContinue)) {
    $top = $null
    $found = $null
    try {
        $top = & git -c "safe.directory=$safeCheckout" -C $checkout rev-parse --show-toplevel 2>$null
        $found = & git -c "safe.directory=$safeCheckout" -C $checkout rev-parse --verify HEAD 2>$null
    } catch {
        # A downloaded repository ZIP has no .git directory; use its files directly.
    }
    if ($LASTEXITCODE -eq 0 -and $found -and $top -and
        ($top | Select-Object -First 1).Trim().Replace('\', '/').TrimEnd('/') -eq $safeCheckout.TrimEnd('/')) {
        $revision = ($found | Select-Object -First 1).Trim()
        $gitRelease = $true
        & git -c "safe.directory=$safeCheckout" -C $checkout diff --quiet HEAD --
        Require-Success 'Checking the Conductor release for local changes'
    }
}
if (-not $gitRelease) {
    # A repository ZIP also contains the complete maintained source but no .git folder.
    $revision = (Get-FileHash -Algorithm SHA256 -LiteralPath $PSCommandPath).Hash.ToLower()
}
foreach ($required in @('pyproject.toml', 'UPSTREAM_LICENSE.txt', 'upstream-lock.json',
                       'scripts\bootstrap.py', 'scripts\prepare_client_runtime.py',
                       'scripts\install_client.py', 'scripts\check_hosted.py',
                       'src\conductor_coordination\client_bridge.py', 'skills\conductor-coordinator\SKILL.md',
                       'gui\runtime-lock.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $checkout $required))) {
        throw "The Conductor package is incomplete: $required is missing. Download the full repository ZIP."
    }
}

$install = if ($TestRoot) { Join-Path $TestRoot 'hosted-client' } else { Join-Path $env:LOCALAPPDATA 'Programs\ConductorHostedClient' }
$source = Join-Path $install 'source'
$work = Join-Path $install '.codex'
$marker = Join-Path $source '.conductor-source-revision'
if (Test-Path -LiteralPath $source) {
    if (-not (Test-Path -LiteralPath $marker) -or (Get-Content -LiteralPath $marker -Raw).Trim() -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
        throw "An unrelated Conductor installation is already at $source. It was preserved."
    }
    if ((Get-Content -LiteralPath $marker -Raw).Trim() -ne $revision) {
        Write-Host 'Reusing the already installed client release. Existing code and settings were preserved.'
    }
} else {
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    New-Item -ItemType Directory -Path $source | Out-Null
    if ($gitRelease) {
        $archive = Join-Path $work 'source.tar'
        & git -c "safe.directory=$safeCheckout" -C $checkout archive --format=tar "--output=$archive" HEAD
        Require-Success 'Packaging the Conductor release'
        & tar -xf $archive -C $source
        Require-Success 'Unpacking the Conductor release'
        Remove-Item -LiteralPath $archive -Force
    } else {
        foreach ($item in @('README.md', 'UPSTREAM_LICENSE.txt', 'upstream-lock.json', 'pyproject.toml',
                            'scripts', 'src', 'skills')) {
            Copy-Item -LiteralPath (Join-Path $checkout $item) -Destination (Join-Path $source $item) -Recurse
        }
        New-Item -ItemType Directory -Path (Join-Path $source 'gui') | Out-Null
        Copy-Item -LiteralPath (Join-Path $checkout 'gui\runtime-lock.json') -Destination (Join-Path $source 'gui\runtime-lock.json')
    }
    Set-Content -LiteralPath $marker -Value $revision -NoNewline
}
New-Item -ItemType Directory -Path $work -Force | Out-Null

$python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $found = & py -3 -c 'import sys; print(sys.executable) if sys.version_info >= (3,11) else sys.exit(1)' 2>$null
    if ($LASTEXITCODE -eq 0) { $python = ($found | Select-Object -First 1).Trim() }
}
if (-not $python -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $found = & python -c 'import sys; print(sys.executable) if sys.version_info >= (3,11) else sys.exit(1)' 2>$null
    if ($LASTEXITCODE -eq 0) { $python = ($found | Select-Object -First 1).Trim() }
}
if (-not $python) {
    Write-Host 'Installing a private Python runtime for Conductor...'
    $lock = Get-Content -LiteralPath (Join-Path $source 'gui\runtime-lock.json') -Raw | ConvertFrom-Json
    $zip = Join-Path $work 'uv.zip'
    $uv = Join-Path $work 'uv.exe'
    if (-not (Test-Path -LiteralPath $uv)) {
        $url = "https://github.com/astral-sh/uv/releases/download/$($lock.uv_version)/$($lock.uv_asset)"
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLower() -ne $lock.uv_archive_sha256) {
            throw 'The Python runtime download failed its pinned checksum.'
        }
        Expand-Archive -LiteralPath $zip -DestinationPath $work -Force
        Remove-Item -LiteralPath $zip -Force
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $uv).Hash.ToLower() -ne $lock.uv_exe_sha256) {
        throw 'The Python installer failed its pinned checksum.'
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $install 'python'
    $env:UV_CACHE_DIR = Join-Path $work 'uv-cache'
    & $uv python install $lock.python_version
    Require-Success 'Installing private Python'
    $python = (& $uv python find $lock.python_version --managed-python).Trim()
    Require-Success 'Finding private Python'
}

$candidates = @()
$sshConfig = & ssh -G $VmHost 2>$null
if ($LASTEXITCODE -eq 0) {
    $configuredUser = @($sshConfig | Where-Object { $_ -match '^user\s+(.+)$' } | Select-Object -First 1)
    if ($configuredUser.Count) { $candidates += ($configuredUser[0] -split '\s+', 2)[1] }
}
$candidates += @($env:USERNAME, 'ubuntu')
$login = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if ($candidate -notmatch '^[A-Za-z_][A-Za-z0-9_.-]*$') { continue }
    $target = "$candidate@$VmHost"
    $probeOk = $false
    try {
        & ssh -o BatchMode=yes -o ConnectTimeout=5 $target 'true' 2>$null
        $probeOk = ($LASTEXITCODE -eq 0)
    } catch {
        # Windows PowerShell treats a rejected SSH login's stderr as a terminating error.
        # A failed candidate is expected; try the next configured login.
    }
    if ($probeOk) { $login = $candidate; break }
}
if (-not $login) {
    throw 'No SSH login was found on this VM. Ask its administrator to grant your SSH account access, then retry.'
}
$target = "$login@$VmHost"
$remoteKey = @(& ssh -o BatchMode=yes $target 'sudo -n /usr/local/lib/conductor-enroll' 2>$null)
if ($LASTEXITCODE -ne 0) {
    throw "The VM could not enroll your SSH login. Ask its administrator to add '$login' to the Conductor enrollment group and verify the VM service."
}
if ($remoteKey.Count -ne 1 -or $remoteKey[0] -notmatch '^/[^\s:]+/\.conductor-keys/[a-z0-9_-]+-initial\.token$') {
    throw 'The VM returned an invalid key path during enrollment.'
}

# Keep the transferred key in a current-user-only staging folder; the client installer
# copies it to its final protected Codex location and the staging copy is removed.
$sid = ((& whoami /user /fo csv /nh) -split ',')[1].Trim('"')
& icacls $work /inheritance:r /grant:r "*${sid}:(OI)(CI)(F)" | Out-Null
Require-Success 'Protecting the private setup folder'
$token = Join-Path $work 'service-token'
try {
    & scp -q "${target}:$($remoteKey[0])" $token
    Require-Success 'Transferring the private Conductor key'
    & icacls $token /inheritance:r /grant:r "*${sid}:(F)" | Out-Null
    Require-Success 'Protecting the private Conductor key'

    & $python (Join-Path $checkout 'scripts\prepare_client_runtime.py') $source
    Require-Success 'Installing the local Conductor client'
    $runtime = Join-Path $source '.codex\conductor-venv\Scripts\python.exe'
    & $runtime (Join-Path $source 'scripts\check_hosted.py') --ssh-target $target --token-file $token
    Require-Success 'Verifying the VM and Conductor key'

    $arguments = @($source, 'client', '--url', 'http://127.0.0.1:8012/mcp', '--ssh-target', $target, '--token-file', $token)
    if ($TestRoot) {
        foreach ($profile in @('desktop', 'vscode')) {
            & $python (Join-Path $source 'scripts\bootstrap.py') @arguments --profile $profile --test-root (Join-Path $TestRoot 'profiles')
            Require-Success "Configuring isolated $profile"
        }
    } else {
        & $python (Join-Path $source 'scripts\bootstrap.py') @arguments --configure-user
        Require-Success 'Configuring Codex desktop and VS Code'
    }
    Write-Host 'Conductor is configured. Restart Codex desktop and VS Code to load the connection.'
} finally {
    if (Test-Path -LiteralPath $token) { Remove-Item -LiteralPath $token -Force }
}
