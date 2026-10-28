# 第 7 步（选了看视频）：装 ffmpeg 到工作目录，给「大肥鱼看视频」插件截画面用。可以重复跑：已经有的会跳过。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File install-ffmpeg.ps1 -Dir C:\dafeiyu
# 不用 winget、不改 PATH；阿里云 PyPI 优先，GitHub essentials 备用，两份都校验 sha256。
param([Parameter(Mandatory)][string]$Dir,
      [string]$Version = '9.0.2',
      [string]$Sha256 = '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba')
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$t0 = Get-Date
$target = "$Dir\ffmpeg\bin\ffmpeg.exe"

function Done([string]$exe) {
    [IO.File]::WriteAllText("$Dir\ffmpeg-path.txt", $exe)
    $v = (& $exe -hide_banner -version 2>$null | Select-Object -First 1)
    Say "ffmpeg 能用：$v"
    Say "位置记在 $Dir\ffmpeg-path.txt；导入配置包时 answers.json 里 ffmpeg_path 填这个路径，video 填 1"
    Say "用时 $([int]((Get-Date) - $t0).TotalSeconds) 秒"
    exit 0
}

if (Test-Path $target) { Done $target }
$have = @((Get-Command ffmpeg -ErrorAction SilentlyContinue).Source, "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe") | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($have) { Say "电脑上已经有 ffmpeg：$have，直接用"; Done $have }

$dl = "$Dir\downloads"; New-Item -ItemType Directory -Force $dl | Out-Null
# 视频插件只用 ffmpeg，不依赖 ffprobe；优先装体积更小的 PyPI 构建。
$wheel = "$dl\imageio-ffmpeg-0.6.0.zip"
try {
    Say '下 ffmpeg（阿里云 PyPI，约 30 MB）'
    Get-File @('https://mirrors.aliyun.com/pypi/packages/2c/c6/fa760e12a2483469e2bf5058c5faff664acf66cadb4df2ad6205b016a73d/imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl') $wheel 20MB '02fa47c83703c37df6bfe4896aab339013f62bf02c5ebf2dce6da56af04ffc0a' | Out-Null
    Expand-Archive -Force $wheel "$dl\imageio-ffmpeg-0.6.0" -ErrorAction Stop
    $bin = Get-ChildItem "$dl\imageio-ffmpeg-0.6.0\imageio_ffmpeg\binaries" -Filter 'ffmpeg-win-x86_64-*.exe' | Select-Object -First 1
    if (-not $bin) { throw 'wheel 里没找到 ffmpeg' }
    & $bin.FullName -version 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'ffmpeg 跑不起来' }
    New-Item -ItemType Directory -Force "$Dir\ffmpeg\bin" | Out-Null
    Copy-Item $bin.FullName $target -Force -ErrorAction Stop
    Done $target
} catch { Say '阿里云的 ffmpeg 没装成，改试 GitHub 完整包' }
$zip = "$dl\ffmpeg-$Version-essentials_build.zip"
$url = "https://github.com/GyanD/codexffmpeg/releases/download/$Version/ffmpeg-$Version-essentials_build.zip"
Get-File -Urls @($script:GhProxies | ForEach-Object { "$_$url" }) -OutFile $zip -MinBytes 90MB -Sha256 $Sha256 | Out-Null
Say "下好了，解压"
$tmp = "$Dir\ffmpeg-unzip"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $tmp | Out-Null
& tar.exe -xf $zip -C $tmp 2>$null
if ($LASTEXITCODE -ne 0) { Expand-Archive $zip $tmp -Force }
$inner = Get-ChildItem $tmp -Directory | Where-Object { Test-Path "$($_.FullName)\bin\ffmpeg.exe" } | Select-Object -First 1
if (-not $inner) { throw "解压后没找到 ffmpeg.exe（看 $tmp）" }
Remove-Item "$Dir\ffmpeg" -Recurse -Force -ErrorAction SilentlyContinue
Move-Item $inner.FullName "$Dir\ffmpeg"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Done $target
