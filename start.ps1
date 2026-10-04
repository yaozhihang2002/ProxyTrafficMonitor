param([switch]$NoBrowser)
$ErrorActionPreference='Stop'
$url='http://127.0.0.1:18791'
try { $r=Invoke-RestMethod "$url/api/status" -TimeoutSec 2; if($r.product -eq 'proxy-traffic-monitor') { if(!$NoBrowser){Start-Process $url}; exit } } catch {}
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if(!(Test-Path -LiteralPath $python)){
    $python=(Get-Command python -ErrorAction SilentlyContinue).Source
    if(!$python){throw '请安装 Python，或直接使用 Windows EXE 分发包'}
}
New-Item -ItemType Directory -Path "$PSScriptRoot\data" -Force | Out-Null
Start-Process -FilePath $python -ArgumentList @('"'+(Join-Path $PSScriptRoot 'monitor.py')+'"') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$PSScriptRoot\data\stdout.log" -RedirectStandardError "$PSScriptRoot\data\stderr.log"
Start-Sleep -Seconds 2
if(!$NoBrowser){Start-Process $url}
