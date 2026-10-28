# 第 2 步：命令行装 AstrBot（不用启动器），全部放进工作目录；后台只听本机；账号密码写进 private\backend.txt；启动并等后台能打开。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File install-astrbot.ps1 -Dir C:\dafeiyu
# 可以重复跑：做过的步骤会跳过。
param([Parameter(Mandatory)][string]$Dir, [int]$Port = 6185, [string]$UvVersion = '0.13.6')
. "$PSScriptRoot\common.ps1"
# 原生程序往 stderr 写进度，PowerShell 5.1 在 Stop 模式下会当成错误中断，所以用 Continue，靠退出码判断
$ErrorActionPreference = 'Continue'
$t0 = Get-Date
$priv = "$Dir\private"; $dl = "$Dir\downloads"; $root = "$Dir\astrbot"; $uvHome = "$Dir\uv"
New-Item -ItemType Directory -Force $priv, $dl, $root, "$uvHome\bin" | Out-Null
# 记下工作目录，下次体检、工作台、换功能时都从这里找（观众可能挑了任何盘、任何文件夹名）
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\dafeiyu" | Out-Null
[IO.File]::WriteAllText("$env:LOCALAPPDATA\dafeiyu\workdir.txt", $Dir)

# uv 和它装的 Python、AstrBot 都放工作目录里（用户名带中文也不受影响，卸载删一个文件夹就行）
$env:UV_TOOL_DIR = "$uvHome\tools"; $env:UV_TOOL_BIN_DIR = "$uvHome\bin"
$env:UV_PYTHON_INSTALL_DIR = "$uvHome\python"; $env:UV_CACHE_DIR = "$uvHome\cache"
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$uv = "$uvHome\bin\uv.exe"; $ab = "$uvHome\bin\astrbot.exe"

# 1. uv：阿里云 PyPI 的 wheel 就是 zip，解出程序；不通再试 GitHub。
function Install-DfyUv([string]$Version, [string]$BinDir, [string]$DownloadDir) {
    New-Item -ItemType Directory -Force $BinDir, $DownloadDir | Out-Null
    $zip = "$DownloadDir\uv-$Version-wheel.zip"
    try {
        $page = Invoke-WebRequest 'https://mirrors.aliyun.com/pypi/simple/uv/' -UseBasicParsing -TimeoutSec 30
        $pattern = 'href="([^"]*/uv-' + [regex]::Escape($Version) + '-py3-none-win_amd64\.whl)#sha256=([a-f0-9]{64})"'
        $m = [regex]::Match($page.Content, $pattern)
        if (-not $m.Success) { throw '阿里云上没找到这个版本的 uv wheel' }
        $u = [uri]::new([uri]'https://mirrors.aliyun.com/pypi/simple/uv/', $m.Groups[1].Value).AbsoluteUri
        Get-File @($u) $zip 5MB $m.Groups[2].Value | Out-Null
        Expand-Archive -Force $zip "$DownloadDir\uv-wheel-$Version" -ErrorAction Stop
        $bin = Get-ChildItem "$DownloadDir\uv-wheel-$Version" -Recurse -Filter uv.exe | Select-Object -First 1
        if (-not $bin) { throw 'wheel 里没找到 uv.exe' }
        Copy-Item $bin.FullName $BinDir -Force -ErrorAction Stop
        $uvx = Get-ChildItem "$DownloadDir\uv-wheel-$Version" -Recurse -Filter uvx.exe | Select-Object -First 1
        if ($uvx) { Copy-Item $uvx.FullName $BinDir -Force }
        & "$BinDir\uv.exe" --version | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'wheel 里的 uv 跑不起来' }
        return
    } catch { Say '阿里云的 uv 没装成，改试 GitHub' }
    $zip = "$DownloadDir\uv-$Version.zip"
    Get-GitHubFile "https://github.com/astral-sh/uv/releases/download/$Version/uv-x86_64-pc-windows-msvc.zip" $zip 5MB | Out-Null
    Expand-Archive -Force $zip "$DownloadDir\uv-$Version" -ErrorAction Stop
    Copy-Item "$DownloadDir\uv-$Version\uv.exe", "$DownloadDir\uv-$Version\uvx.exe" $BinDir -Force -ErrorAction Stop
}
if (-not (Test-Path $uv)) { Say "下载 uv $UvVersion（Python 的安装工具，约 16 MB）"; Install-DfyUv $UvVersion "$uvHome\bin" $dl }
Say ("uv：" + (& $uv --version))

# 2. 先单独装 Python，避免 Python 下不来时把每个 PyPI 源都白试一遍。
function Install-DfyPython([string]$UvExe) {
    $mirrors = @('https://registry.npmmirror.com/-/binary/python-build-standalone', 'https://mirror.nju.edu.cn/github-release/astral-sh/python-build-standalone')
    $mirrors += @($script:GhProxies | ForEach-Object { "${_}https://github.com/astral-sh/python-build-standalone/releases/download" })
    foreach ($mirror in $mirrors) {
        $env:UV_PYTHON_INSTALL_MIRROR = $mirror
        Say "装 Python 3.12（走 $(([uri]$mirror).Host)）"
        & $UvExe python install 3.12 --no-bin 2>&1 | Select-Object -Last 3 | ForEach-Object { "  $_" }
        if ($LASTEXITCODE -eq 0) { return }
    }
    throw 'Python 没下下来，查好网络后重跑即可'
}
if (-not (Test-Path $ab)) {
    Install-DfyPython $uv
    $indexes = @('https://mirrors.aliyun.com/pypi/simple/', 'https://pypi.tuna.tsinghua.edu.cn/simple/', 'https://mirrors.cloud.tencent.com/pypi/simple/', 'https://repo.huaweicloud.com/repository/pypi/simple/', 'https://pypi.org/simple/')
    $done = $false
    foreach ($ix in $indexes) {
        $env:UV_DEFAULT_INDEX = $ix
        Say "装 AstrBot（包走 $ix）"
        & $uv tool install astrbot --python 3.12 2>&1 | Select-Object -Last 3 | ForEach-Object { "  $_" }
        if ($LASTEXITCODE -eq 0 -and (Test-Path $ab)) { $done = $true; break }
    }
    if (-not $done) { throw 'AstrBot 没装上，看上面几行报错' }
}
Say ("AstrBot：" + ((& $uv tool list 2>$null) -match '^astrbot' | Select-Object -First 1))

# 3. 账号密码（必须带大写字母）
$backend = "$priv\backend.txt"
if (-not (Test-Path $backend)) {
    $pw = New-RandomSecret 16 -Mixed
    "url: http://127.0.0.1:$Port`r`nusername: dafeiyu`r`npassword: $pw" | Set-Content -Encoding UTF8 $backend
    Say '后台账号密码已生成，写在 private\backend.txt'
}
$B = Read-KeyValueFile $backend
if (-not (Test-Path "$priv\napcat-ws-token.txt")) { [IO.File]::WriteAllText("$priv\napcat-ws-token.txt", (New-RandomSecret 24)) }

# 4. 初始化 + 后台只听本机 + 设账号密码（第一次启动前设好，就不用找初始密码）
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
Push-Location $root
try {
    if (-not (Test-Path "$root\data")) { & $ab init -y 2>&1 | Out-Null; Say '初始化好了' }
    $cfgFile = "$root\data\cmd_config.json"
    if (-not (Test-Path $cfgFile)) {
        # 有的版本 init 不生成配置文件：先跑一次让它生成，再停掉
        Say '第一次启动生成配置文件（半分钟）'
        $p = Start-Process -FilePath $ab -ArgumentList 'run' -WorkingDirectory $root -WindowStyle Hidden -PassThru
        for ($i = 0; $i -lt 60 -and -not (Test-Path $cfgFile); $i++) { Start-Sleep 2 }
        Start-Sleep 5; Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; Stop-UnderDir $uvHome; Start-Sleep 2
    }
    $py = "$uvHome\tools\astrbot\Scripts\python.exe"
    # 改 JSON 交给 Python（PowerShell 5.1 的 ConvertTo-Json 会改坏大配置）
    & $py -X utf8 "$PSScriptRoot\astr_setup.py" --dir $Dir dashboard-local --port $Port
    & $ab conf set dashboard.username $B['username'] 2>&1 | Out-Null
    & $ab conf set dashboard.password $B['password'] 2>&1 | Out-Null
    $cfg = Get-Content -Raw -Encoding UTF8 $cfgFile | ConvertFrom-Json
    Say ("后台账号：$($cfg.dashboard.username)，密码已设（不显示），只听 $($cfg.dashboard.host):$($cfg.dashboard.port)")
}
finally { Pop-Location }

# 5. 启动脚本 + 启动
# PATH 里加上工作目录的 ffmpeg（install-ffmpeg.ps1 装在这；Edge 语音、转语音格式都要它）。现在还没装也不要紧，装上以后不用重启就能找到
$cmd = "@echo off`r`nchcp 65001 >nul`r`ntitle Dafeiyu-AstrBot (do not close)`r`nset PYTHONUTF8=1`r`nset PYTHONIOENCODING=utf-8`r`nset `"PATH=$Dir\ffmpeg\bin;%PATH%`"`r`ncd /d `"$root`"`r`n`"$ab`" run >> `"$root\run.log`" 2>&1`r`n"
[IO.File]::WriteAllText("$Dir\start-astrbot.cmd", $cmd, (New-Object Text.UTF8Encoding($false)))
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($busy) {
    # 占着端口的要是别的目录那套（另装一套、换了盘重装），等端口就会把旧的当成新的、假报「起来了」
    $who = (Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue).Path
    if ($who -and -not $who.StartsWith("$Dir\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "端口 $Port 被别的程序占着（$who）。另装一套就换个端口重跑（-Port 6285）；不要旧的了就先 stop-instance.ps1 -Dir <旧工作目录> 关掉它"
    }
    Say "这个目录的 AstrBot 已经在跑了（端口 $Port），不重复启动"
} else {
    Start-Detached 'astrbot' "$Dir\start-astrbot.cmd"
}
if (-not (Wait-Port $Port 150)) { Get-Content "$root\run.log" -Tail 15 -ErrorAction SilentlyContinue; throw "AstrBot 150 秒没起来，看上面的日志" }
Start-Sleep 5
Say ("AstrBot 后台起来了：http://127.0.0.1:$Port（用时 {0:N0} 秒）" -f ((Get-Date) - $t0).TotalSeconds)
Say "下一步：python 用 $uvHome\tools\astrbot\Scripts\python.exe 跑 astr_setup.py"
