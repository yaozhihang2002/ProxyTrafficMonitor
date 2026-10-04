param([string]$Python='python')
$ErrorActionPreference='Stop'
Push-Location $PSScriptRoot
try {
    $taskTclRoot = & $Python -c "import tkinter; from pathlib import Path; print(Path(tkinter.Tcl().eval('info library')).parent)"
    if($LASTEXITCODE -ne 0){throw '无法定位 Tcl 运行库'}
    $taskTclArgs=@()
    foreach($item in @(@('tcl8.6','_tcl_data'),@('tk8.6','_tk_data'),@('tcl8','tcl8'))){
        $taskLibrary=Join-Path $taskTclRoot $item[0]
        if(Test-Path -LiteralPath $taskLibrary){$taskTclArgs+=@('--add-data',($taskLibrary+';'+$item[1]))}
        elseif($item[0] -ne 'tcl8'){throw "缺少运行库 $taskLibrary，请使用含 Tk 的 Windows Python"}
    }
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name ProxyTrafficMonitor --distpath release --workpath work/build --specpath work --hidden-import pystray._win32 --exclude-module numpy --add-data "$PSScriptRoot\index.html;." --add-data "$PSScriptRoot\ui.css;." --add-data "$PSScriptRoot\trend-chart.js;." --add-data "$PSScriptRoot\collector.ps1;." @taskTclArgs "$PSScriptRoot\desktop.py"
    if($LASTEXITCODE -ne 0){throw 'EXE build failed'}
    Write-Output '构建完成：release\ProxyTrafficMonitor.exe。分发前还需运行测试、独立 EXE 启动检查与归档验证。'
} finally {Pop-Location}
