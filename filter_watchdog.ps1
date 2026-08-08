# filter_watchdog.ps1 - self-healing watchdog for the cc-switch thinking filter.
#   1) keep filter_proxy (127.0.0.1:15722) alive
#   2) auto re-pin base_url in config.toml via pin-codex-filter.ps1 (idempotent)
#   3) detect cc-switch restart (PID change) and re-pin immediately
#   4) auto-detect cc-switch upstream port from cc-switch.db and restart filter with it
# single instance via lock file. ASCII only (no BOM).
param()
$ErrorActionPreference = 'SilentlyContinue'

$python = $env:CCSWITCH_FILTER_PYTHON
if (-not $python) { $python = 'python' }
$script = Join-Path $PSScriptRoot 'filter_proxy.py'
$log    = Join-Path $PSScriptRoot 'filter_proxy.log'
$pin    = Join-Path $PSScriptRoot 'pin-codex-filter.ps1'
$lock   = Join-Path $PSScriptRoot 'filter_watchdog.lock'

# single instance guard: atomic lock file with live PID check
$self = $PID
$gotLock = $false
for ($try = 0; $try -lt 3; $try++) {
    try {
        $fs = [System.IO.File]::Open($lock, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
        $sw = New-Object System.IO.StreamWriter($fs)
        $sw.Write($self)
        $sw.Close()
        $gotLock = $true
        break
    } catch {
        $old = Get-Content $lock -ErrorAction SilentlyContinue
        if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
            exit 0
        }
        Start-Sleep -Milliseconds 300
        Remove-Item $lock -Force -ErrorAction SilentlyContinue
    }
}
if (-not $gotLock) { exit 0 }

function Test-Port([int]$port, [int]$timeoutMs = 800) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($timeoutMs)) { return $false }
        $c.EndConnect($iar)
        return $true
    } catch { return $false }
    finally { $c.Close() }
}

function Get-UpstreamPort {
    $py = 'import sqlite3,os;con=sqlite3.connect(os.path.expanduser(r"~\.cc-switch\cc-switch.db"));cur=con.cursor();cur.execute("SELECT port FROM proxy_config WHERE app_type=''codex''");r=cur.fetchone();print(r[0] if r else 15721)'
    $out = & $python -c $py 2>$null
    $n = 0
    if ($out -and [int]::TryParse(($out | Select-Object -Last 1), [ref]$n)) { return $n }
    return 15721
}

function Get-FilterPid {
    try {
        $lines = netstat -ano | Select-String '127.0.0.1:15722' | Select-String 'LISTENING'
        foreach ($l in $lines) {
            $t = ($l.ToString() -split '\s+') | Where-Object { $_ -ne '' }
            if ($t.Count -ge 5) { return [int]$t[$t.Count - 1] }
        }
    } catch { }
    return $null
}

function Start-FilterProxy([int]$upstreamPort) {
    Start-Process -FilePath $python -ArgumentList @("`"$script`"", "--listen", "127.0.0.1:15722", "--upstream", "127.0.0.1:$upstreamPort", "--log", "`"$log`"") -WindowStyle Hidden
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-Port 15722) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return (Test-Port 15722)
}

function Restart-FilterProxy([int]$upstreamPort) {
    $fpid = Get-FilterPid
    if ($fpid) { Stop-Process -Id $fpid -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 800
    Start-FilterProxy $upstreamPort | Out-Null
}

$ccSwitchPid = $null
$lastUpstreamPort = 15721

while ($true) {
    $ccProc = Get-Process -Name 'cc-switch' -ErrorAction SilentlyContinue | Select-Object -First 1
    $curPid = if ($ccProc) { $ccProc.Id } else { $null }

    if ($curPid -and $curPid -ne $ccSwitchPid) {
        $ccSwitchPid = $curPid
        $up = Get-UpstreamPort
        if ($up -ne $lastUpstreamPort) {
            $lastUpstreamPort = $up
            if (Test-Port 15722) { Restart-FilterProxy $lastUpstreamPort }
        }
        & $pin | Out-Null
    } elseif (-not $curPid) {
        $ccSwitchPid = $null
    }

    if (-not (Test-Port 15722)) {
        $lastUpstreamPort = Get-UpstreamPort
        Start-FilterProxy $lastUpstreamPort | Out-Null
    }

    & $pin | Out-Null
    Start-Sleep -Seconds 5
}
