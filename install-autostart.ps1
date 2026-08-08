# install-autostart.ps1 - 安装自启动(启动文件夹 + 无中文运行路径, 无需管理员)
param()
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$dst = Join-Path $env:USERPROFILE 'cc-switch-fix'
$startup = [Environment]::GetFolderPath('Startup')

# 1) 复制运行时脚本到无中文路径(避免 wscript 按 ANSI 读 .vbs 时中文路径乱码)
New-Item -ItemType Directory -Force -Path $dst | Out-Null
foreach ($f in @('filter_proxy.py', 'filter_watchdog.ps1', 'pin-codex-filter.ps1')) {
    Copy-Item (Join-Path $src $f) (Join-Path $dst $f) -Force
}
Write-Output "[install] 运行时脚本已复制到 $dst"

# 2) 写 vbs(仅 ASCII 路径, wscript 任何编码都能正确读取)
$watchdog = Join-Path $dst 'filter_watchdog.ps1'
$pin      = Join-Path $dst 'pin-codex-filter.ps1'
$vbs1 = Join-Path $startup 'CCSwitchFilterProxy.vbs'
$content1 = "Set sh = CreateObject(`"WScript.Shell`")`r`n" +
            "sh.Run `"powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"`"$watchdog`"`"`", 0, False`r`n"
[System.IO.File]::WriteAllText($vbs1, $content1, (New-Object System.Text.ASCIIEncoding))
Write-Output "[install] 已写入 $vbs1"

$vbs2 = Join-Path $startup 'CCSwitchCodexPin.vbs'
$content2 = "Set sh = CreateObject(`"WScript.Shell`")`r`n" +
            "sh.Run `"powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"`"$pin`"`"`", 0, False`r`n"
[System.IO.File]::WriteAllText($vbs2, $content2, (New-Object System.Text.ASCIIEncoding))
Write-Output "[install] 已写入 $vbs2"

# 3) 启动看门狗(从新路径, 若未在跑)
$running = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {
    $_.CommandLine -like '*filter_watchdog.ps1*' -and $_.CommandLine -notlike '*Get-CimInstance*' }
if (-not $running) {
    Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',"`"$watchdog`"") -WindowStyle Hidden
    Write-Output "[install] 看门狗已启动(隐藏)"
} else {
    Write-Output "[install] 看门狗已在运行: $($running.ProcessId -join ',')"
}
