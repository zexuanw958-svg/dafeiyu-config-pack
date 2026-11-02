# 双击桌面「大肥鱼工作台」（云端是「云端大肥鱼工作台」）走到这里（tools\workbench.cmd 调它）：
# 找 AstrBot 自带的 Python（云端用 finish.ps1 放的便携版 <工作目录>\python），起本机小服务 server.py 并开 Edge 小窗口（已经在跑就只开窗口）；
# 找不到 Python 就退回 tools\workbench.ps1 生成的静态页（只能看，不能修）；云端没有静态页，弹个框说怎么办。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File open.ps1 [-Dir <工作目录>] [-Do relogin]
# -Do relogin：打开后直接开始扫码登录（云端的掉线通知点进来走这里）
param([string]$Dir = '', [ValidateSet('', 'relogin')][string]$Do = '')
$ErrorActionPreference = 'SilentlyContinue'
$here = $PSScriptRoot
if (-not $Dir) { $Dir = Split-Path (Split-Path $here) }   # <工作目录>\tools\workbench → <工作目录>
$Dir = [IO.Path]::GetFullPath($Dir).TrimEnd('\')

. "$(Split-Path $here)\common.ps1"
function Find-Py {
    if (Test-Path "$Dir\python\python.exe") { return "$Dir\python\python.exe" }
    return Find-AstrPython $Dir
}

$py = Find-Py
if ($py -and (Test-Path "$here\server.py")) {
    $pyw = Join-Path (Split-Path $py) 'pythonw.exe'   # 不出黑窗口
    $argline = "-X utf8 `"$here\server.py`" --dir `"$Dir`" --open"
    if ($Do) { $argline += " --do $Do" }
    $cwd = [IO.Path]::GetTempPath()   # 不把当前目录放在工作目录里：服务开着时卸载、挪文件夹也删得动
    if (Test-Path $pyw) { Start-Process -FilePath $pyw -ArgumentList $argline -WorkingDirectory $cwd }
    else { Start-Process -FilePath $py -ArgumentList $argline -WorkingDirectory $cwd -WindowStyle Hidden }
    exit 0
}
if ((Test-Path "$Dir\private\cloud.txt") -and -not (Test-Path "$Dir\start-astrbot.cmd")) {
    $msg = "工作台要用的 Python 没找到（应该在 $Dir\python）。`n`n在 DeepSeek Harness 里说：用大肥鱼的安装 Skill，帮我重新放一下云端大肥鱼工作台（她装在云服务器上）。"
    (New-Object -ComObject WScript.Shell).Popup($msg, 0, '云端大肥鱼工作台', 48) | Out-Null
    exit 1
}
$fb = Join-Path (Split-Path $here) 'workbench.ps1'
if (Test-Path $fb) { & $fb -Dir $Dir }
