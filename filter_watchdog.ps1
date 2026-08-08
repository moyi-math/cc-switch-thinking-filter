# filter_watchdog.ps1 — 守护过滤代理: 端口 15722 不在线则拉起, 崩溃自动重启(常驻)
param()
$ErrorActionPreference = 'SilentlyContinue'
$python = $env:CCSWITCH_FILTER_PYTHON
if (-not $python) { $python = 'python' }
$script = Join-Path $PSScriptRoot 'filter_proxy.py'
$log    = Join-Path $PSScriptRoot 'filter_proxy.log'

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

while ($true) {
    if (-not (Test-Port 15722)) {
        Start-Process -FilePath $python -ArgumentList @("`"$script`"", "--listen", "127.0.0.1:15722", "--upstream", "127.0.0.1:15721", "--log", "`"$log`"") -WindowStyle Hidden
        for ($i = 0; $i -lt 20; $i++) {
            if (Test-Port 15722) { break }
            Start-Sleep -Milliseconds 500
        }
    }
    Start-Sleep -Seconds 5
}
