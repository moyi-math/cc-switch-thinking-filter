# start-codex.ps1 — 启动 Codex 前确保过滤代理在跑 + 重钉配置, 然后启动 codex
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CodexArgs)
$ErrorActionPreference = 'Continue'
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

if (-not (Test-Port 15722)) {
    Write-Host "[start-codex] 启动过滤代理(15722)..."
    Start-Process -FilePath $python -ArgumentList @("`"$script`"", "--listen", "127.0.0.1:15722", "--upstream", "127.0.0.1:15721", "--log", "`"$log`"") -WindowStyle Hidden
    for ($i = 0; $i -lt 24; $i++) {
        if (Test-Port 15722) { break }
        Start-Sleep -Milliseconds 500
    }
}
if (Test-Port 15722) { Write-Host "[start-codex] 过滤代理在线(15722)" }
else { Write-Warning "[start-codex] 过滤代理未就绪, 本次仍按现有配置启动 codex" }

& (Join-Path $PSScriptRoot 'pin-codex-filter.ps1')

Write-Host "[start-codex] 启动 codex $($CodexArgs -join ' ')"
& codex @CodexArgs
exit $LASTEXITCODE
