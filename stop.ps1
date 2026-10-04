$ErrorActionPreference='Stop'
$pidFile=Join-Path $PSScriptRoot 'data\monitor.pid'
if(!(Test-Path $pidFile)){exit}
$monitorId=[int](Get-Content -LiteralPath $pidFile)
$p=Get-CimInstance Win32_Process -Filter "ProcessId=$monitorId"
$expected=Join-Path $PSScriptRoot 'monitor.py'
if($p -and $p.Name -match '^python(w)?\.exe$' -and $p.CommandLine.Contains($expected)){
 & taskkill.exe /PID $monitorId /T /F
} else { Write-Output '采集器未运行，或 PID 已被其他程序使用；未终止任何程序。' }
