<#
.SYNOPSIS
    把 Auto-xdwlan 图形界面（app.py）打包成单文件 exe。

.DESCRIPTION
    1) 从 logo\Auto-xdwlan.png 生成 logo\Auto-xdwlan.ico（如果还没有）
    2) 用 PyInstaller 打包成 dist\Auto-xdwlan.exe（单文件、无控制台窗口）
    3) exe 运行后只在同目录生成配置文件 settings.json（运行日志只显示在窗口里，不写日志文件；
       只有内部异常才会追加 crash.log）

.PARAMETER Python
    Python 解释器路径（需要已经安装 PyQt5 / requests / pyinstaller）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
#>
param(
    [string]$Python = 'D:\Anaconda\python.exe',
    [string]$Name = 'Auto-xdwlan'
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
if (-not $here) { $here = (Get-Location).Path }
Set-Location $here

Write-Host '==> 检查 Python 环境' -ForegroundColor Cyan
if (-not (Test-Path $Python)) {
    throw "找不到 Python: $Python（用 -Python 参数指定路径）"
}
& $Python -c "import PyQt5, requests, PyInstaller; print('依赖检查通过')"
if ($LASTEXITCODE -ne 0) {
    throw '缺少依赖，请先执行: ' + $Python + ' -m pip install pyqt5 requests pyinstaller'
}

Write-Host '==> 准备图标与图片资源' -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $here 'logo\Auto-xdwlan.ico'))) {
    & $Python -c @"
from PIL import Image
import os
here = r'$here'
src = os.path.join(here, 'logo', 'Auto-xdwlan.png')
img = Image.open(src).convert('RGBA').resize((256, 256), Image.LANCZOS)
img.save(os.path.join(here, 'logo', 'Auto-xdwlan.ico'),
         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
small = Image.open(src).convert('RGBA').resize((192, 192), Image.LANCZOS)
small.save(os.path.join(here, 'logo', 'logo_192.png'), optimize=True)
print('图标已生成')
"@
}

Write-Host '==> 清理旧的构建目录' -ForegroundColor Cyan
$running = Get-Process -Name $Name -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "    检测到 $Name 正在运行，无法覆盖 dist\$Name.exe。" -ForegroundColor Yellow
    Write-Host "    请先退出程序（托盘图标右键 → 退出 Auto-xdwlan），然后重新运行本脚本。" -ForegroundColor Yellow
    exit 1
}

# 打包会重建整个 dist 目录，这里先把界面里填过的私有配置挪到临时目录，打完再放回去，
# 免得每次重新打包都把 dist\settings.json（账号、密码、各选项）清掉。
$settingsPath = Join-Path $here 'dist\settings.json'
$settingsTemp = Join-Path $env:TEMP 'Auto-xdwlan-settings.backup.json'
if (Test-Path $settingsPath) {
    Move-Item -Force -Path $settingsPath -Destination $settingsTemp
    Write-Host '    已备份 dist\settings.json（打包完成后会自动还原）' -ForegroundColor Yellow
}

foreach ($path in @('build', 'dist')) {
    $full = Join-Path $here $path
    if (-not (Test-Path $full)) { continue }
    try {
        Remove-Item $full -Recurse -Force -ErrorAction Stop
    } catch {
        Write-Host "    清理 $full 失败：$($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host '    常见原因：程序还在运行、被杀软临时占用。关掉程序后重试即可。' -ForegroundColor Yellow
        exit 1
    }
}

Write-Host '==> 开始打包（第一次会比较慢，请耐心等待）' -ForegroundColor Cyan
$arguments = @(
    '-m', 'PyInstaller', '--noconfirm', '--clean',
    '--onefile', '--noconsole', '--name', $Name,
    '--icon', (Join-Path $here 'logo\Auto-xdwlan.ico'),
    '--add-data', ((Join-Path $here 'logo\Auto-xdwlan.ico') + ';logo'),
    '--add-data', ((Join-Path $here 'logo\Auto-xdwlan.png') + ';logo'),
    '--add-data', ((Join-Path $here 'logo\logo_192.png') + ';logo'),
    '--exclude-module', 'tkinter',
    '--exclude-module', 'matplotlib',
    '--exclude-module', 'numpy',
    '--exclude-module', 'pandas',
    '--exclude-module', 'scipy',
    '--exclude-module', 'PIL',
    '--exclude-module', 'PyQt5.QtWebEngineWidgets',
    '--exclude-module', 'PyQt5.QtQml',
    '--exclude-module', 'PyQt5.QtQuick',
    '--distpath', (Join-Path $here 'dist'),
    '--workpath', (Join-Path $here 'build'),
    '--specpath', (Join-Path $here 'build'),
    (Join-Path $here 'app.py')
)
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw '打包失败，请查看上面的错误信息' }

$exe = Join-Path $here ('dist\' + $Name + '.exe')
if (-not (Test-Path $exe)) { throw "打包结束但没有找到 $exe" }

if (Test-Path $settingsTemp) {
    Move-Item -Force -Path $settingsTemp -Destination $settingsPath
    Write-Host '    已还原 dist\settings.json（账号密码等配置没丢）' -ForegroundColor Yellow
}

$size = [Math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ''
Write-Host "==> 打包完成: $exe ($size MB)" -ForegroundColor Green
Write-Host '    双击即可运行；首次使用请在界面里填好账号密码并点「保存配置」。' -ForegroundColor Green
Write-Host '    需要开机自启：在界面里勾选「开机自启动」即可（写入当前用户注册表 Run）。' -ForegroundColor Green
