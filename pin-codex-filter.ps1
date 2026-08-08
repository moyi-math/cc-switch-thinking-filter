# pin-codex-filter.ps1 - 按当前激活的 Codex 供应商隔离:
#   基元律动(id=c001b8e8-...) -> base_url 钉到 15722(走过滤代理)
#   其它供应商             -> base_url 钉到 15721(直连 CC Switch, 不经过滤)
# 幂等; 修改前自动备份。
param(
    [string]$SettingsPath = (Join-Path $env:USERPROFILE '.cc-switch\settings.json'),
    [string]$ConfigPath   = (Join-Path $env:USERPROFILE '.codex\config.toml')
)
$ErrorActionPreference = 'Stop'
$JIYUAN_ID = 'c001b8e8-3ffb-416a-9495-ae6d5669e36f'

if (-not (Test-Path $ConfigPath)) { Write-Output "[pin] config.toml 不存在: $ConfigPath"; exit 0 }

$current = $null
if (Test-Path $SettingsPath) {
    try { $current = (Get-Content $SettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json).currentProviderCodex } catch { $current = $null }
}

if ($current -eq $JIYUAN_ID -or $null -eq $current) {
    $target = '15722'
    $to = 'base_url = "http://127.0.0.1:15722/v1"'
    $fromPatterns = @('base_url\s*=\s*"http://127\.0\.0\.1:15721/v1"', 'base_url\s*=\s*"https://tokenrhythm\.studio/v1"')
} else {
    $target = '15721'
    $to = 'base_url = "http://127.0.0.1:15721/v1"'
    $fromPatterns = @('base_url\s*=\s*"http://127\.0\.0\.1:15722/v1"')
}

$text = Get-Content $ConfigPath -Raw -Encoding UTF8
$new = $text
foreach ($p in $fromPatterns) { $new = $new -replace $p, $to }
if ($new -eq $text) {
    Write-Output "[pin] 供应商=$current, base_url 已是 $target, 无需修改"
    exit 0
}
$bak = "$ConfigPath.bak-$(Get-Date -Format 'yyyyMMdd_HHmmss')"
Copy-Item $ConfigPath $bak
[System.IO.File]::WriteAllText($ConfigPath, $new, (New-Object System.Text.UTF8Encoding($false)))
Write-Output "[pin] 供应商=$current, 已修改 base_url -> $target (备份: $bak)"
