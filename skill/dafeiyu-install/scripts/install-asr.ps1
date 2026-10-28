# 第 7 步（选了看视频）：装本机语音识别，让「大肥鱼看视频」插件听得懂视频里说的话。免费、不联网识别、不用注册。可以重复跑：已经有的会跳过。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File install-asr.ps1 -Dir C:\dafeiyu [-Plus]
# 默认装 sherpa-onnx 的现成程序（只留 sherpa-onnx-offline 一个 exe 和 onnxruntime 两个 dll）+ SenseVoice int8 模型（中英日韩粤，快）。
# 模型先走国内 ModelScope，再 hf-mirror，最后 GitHub 原压缩包：三处是同一份文件（SHA256 一样，10-28 核过）。
#   10-28 群友那台 GitHub、hf-mirror、加速站全没进度，只能看画面，所以把国内源放最前面。
# 都下不动：退出码 2，打出浏览器手动下载的地址。他把文件下到「下载」文件夹（或桌面）就行，重跑本脚本会按大小和 SHA256 认出来、放好，不认文件名。
# -Plus 再装增强版 Fun-ASR-Nano（多约 840 MB，只有 GitHub 原包）：湖南话、闽南话这类难懂的方言和歌词认得更准，但慢：每次先花约 6 秒读模型，短视频也要多等 10 秒左右、一分钟的多等半分钟左右。
# 不往 AstrBot 的 Python 里装包：插件直接调这个 exe，跟 ffmpeg 一样。
param([Parameter(Mandatory)][string]$Dir, [switch]$Plus)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$t0 = Get-Date
$asr = "$Dir\asr"
$nano = "$asr\funasr-nano"
$exe = "$asr\sherpa-onnx-offline.exe"
$dl = "$Dir\downloads"
$rel = 'https://github.com/k2-fsa/sherpa-onnx/releases/download'
$base = @(
    @{ Name = 'sherpa-onnx-v1.13.8-win-x64-shared-MT-Release-no-tts.tar.bz2'; Url = "$rel/v1.13.8/sherpa-onnx-v1.13.8-win-x64-shared-MT-Release-no-tts.tar.bz2"; Sha = '4b0a94f7b5c606b1b64a19a831c2127559e4b3d34e195465ebc7be73d9ed4783'; Size = 23271851 },
    @{ Name = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09.tar.bz2'; Url = "$rel/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09.tar.bz2"; Sha = '7305f7905bfcf77fa0b39388a313f3da35c68d971661a65475b56fb2162c8e63'; Size = 165783878 }
)
$plusFile = @{ Name = 'sherpa-onnx-funasr-nano-int8-2025-12-30.tar.bz2'; Url = "$rel/asr-models/sherpa-onnx-funasr-nano-int8-2025-12-30.tar.bz2"; Sha = 'eb43d7ccc2e86b243f6a03b7df361033dda66db9523d1a92bf6aca2b50c9476b'; Size = 841730611 }
$nanoFiles = 'encoder_adaptor.int8.onnx', 'llm.int8.onnx', 'embedding.int8.onnx', 'Qwen3-0.6B\tokenizer.json'
# SenseVoice 2025-09-09 的三个文件。ModelScope 上没有作者的官方仓库，下面三个仓库是别人原样传的：只认 SHA256，跟 GitHub 原包里的逐字节一样。
# 三个仓库的文件在 ModelScope 的 CDN 上是同一份（按 SHA256 存），多列两个只防哪个仓库被删
$msName = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09'
$msRepos = @('aistoy', 'Mr7Cat', 'zephyrweng')
$hf = 'https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09/resolve/355f4d4884d8afd08aef04b9007a8556d7b463b2'
$svFiles = @(
    @{ Name = 'model.int8.onnx'; Src = 'model.int8.onnx'; Sha = '12ca1a2ae7ecf3e0019ef2822307ee0b5cadc9196569e379b4c4026f8205276d'; Size = 237115547; Repos = $msRepos },
    @{ Name = 'tokens.txt'; Src = 'tokens.txt'; Sha = 'f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc'; Size = 315894; Repos = $msRepos },
    @{ Name = 'test-zh.wav'; Src = 'test_wavs/zh.wav'; Sha = 'b77f1794fe374a0ba1ee1dc458bfaf9349496cbbfc32780c50ba3c5a7ad8e373'; Size = 178988; Repos = @('aistoy') }
)

function Test-Sha([string]$Path, $f) {
    (Test-Path -LiteralPath $Path) -and (Get-Item -LiteralPath $Path).Length -eq $f.Size -and (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $f.Sha
}
# 模型和词表要完整（按 SHA256 核）：以前的版本直接往 asr 里下，断了会留半截，光看「文件在不在」会当成装好了
function Has-Base { (Test-Path $exe) -and (Test-Sha "$asr\model.int8.onnx" $svFiles[0]) -and (Test-Sha "$asr\tokens.txt" $svFiles[1]) }
function Has-Plus { @($nanoFiles | Where-Object { -not (Test-Path "$nano\$_") }).Count -eq 0 }

function Get-DownloadsDir {
    try {
        $p = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -ErrorAction Stop).'{374DE290-123F-4565-9164-39C4925E467B}'
        if ($p) { return [Environment]::ExpandEnvironmentVariables($p) }
    } catch {}
    return "$env:USERPROFILE\Downloads"
}
# 他用浏览器下好的文件：在 downloads、asr、「下载」文件夹、桌面里按确切大小找，再核 SHA256（浏览器可能改名成「model.int8 (1).onnx」）
function Find-Local($f) {
    $dirs = @($dl, $asr, (Get-DownloadsDir), [Environment]::GetFolderPath('Desktop')) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique
    foreach ($d in $dirs) {
        foreach ($c in @(Get-ChildItem -LiteralPath $d -File -ErrorAction SilentlyContinue | Where-Object { $_.Length -eq $f.Size })) {
            if ((Get-FileHash -LiteralPath $c.FullName -Algorithm SHA256).Hash -eq $f.Sha) { return $c.FullName }
        }
    }
    return $null
}

# 都下不动时告诉他（和你）怎么用浏览器补，退出码 2
function Stop-WithManualHelp([string]$what) {
    Say '几个下载地址这会儿都没下动（下了一半的留着，重跑会接着下）。可以这样补：'
    if ($what -eq 'exe') {
        Say "  用浏览器打开下面任意一个地址，把 $($base[0].Name)（约 23 MB）下到「下载」文件夹："
        foreach ($p in $script:GhProxies) { Say "    $p$($base[0].Url)" }
    } else {
        Say '  用浏览器打开下面三个地址（国内 ModelScope），三个文件都下到「下载」文件夹，文件名不用改：'
        foreach ($f in $svFiles) { Say "    https://modelscope.cn/models/$($msRepos[0])/$msName/resolve/master/$($f.Src)" }
    }
    Say "  下好以后重跑本脚本：它在「下载」文件夹和桌面里按大小、校验认出来，放进 $asr。"
    Say '  有硅基流动 key 的，也可以先不装本机识别，改用在线识别（看图看视频.md「本机识别下不动」）。'
    exit 2
}

# 拿模型自带的中文样例试认一句，认得出来才算装好；装了增强版两个都试
function Try-Recognize([string[]]$model) {
    $out = & $exe '--num-threads=4' @model "$asr\test-zh.wav" 2>&1 | Out-String
    $line = ($out -split "`r?`n") | Where-Object { $_ -match '^\{' } | Select-Object -First 1
    $text = if ($line) { (($line | ConvertFrom-Json).text -replace '<\|[^|>]*\|>', '').Trim() } else { '' }
    if (-not $text) { Say "识别程序跑了但没认出字，输出的最后几行："; ($out -split "`r?`n" | Select-Object -Last 8) | ForEach-Object { Say "  $_" }; exit 1 }
    $text
}
function Done {
    if (-not (Test-Sha "$asr\test-zh.wav" $svFiles[2])) { try { Get-SvFile $svFiles[2] } catch {} }
    if (Test-Path "$asr\test-zh.wav") {
        $sv = Try-Recognize @("--tokens=$asr\tokens.txt", "--sense-voice-model=$asr\model.int8.onnx", '--sense-voice-use-itn=1', '--sense-voice-language=zh')
        Say "语音识别能用，试认样例：$sv"
    } else { Say '模型和程序都齐了（按校验核过），只是试认用的样例音频（179 KB）下不来，先不试认；装好以后发一段有人说话的视频验收' }
    if (Has-Plus) {
        $fn = Try-Recognize @("--funasr-nano-encoder-adaptor=$nano\encoder_adaptor.int8.onnx", "--funasr-nano-llm=$nano\llm.int8.onnx", "--funasr-nano-embedding=$nano\embedding.int8.onnx", "--funasr-nano-tokenizer=$nano\Qwen3-0.6B")
        Say "增强版也能用，试认样例：$fn"
    }
    [IO.File]::WriteAllText("$Dir\asr-path.txt", $asr)
    Say "位置记在 $Dir\asr-path.txt；导入配置包时 answers.json 里 asr_dir 填这个路径（video 填 1）"
    Say "用时 $([int]((Get-Date) - $t0).TotalSeconds) 秒"
    exit 0
}

# GitHub 上的压缩包：下到 downloads 里并校验（他手动下好的先拿来用）；不返回路径（Say 是 Write-Output，会混进返回值）
function Get-Verified($f) {
    $path = "$dl\$($f.Name)"
    $local = Find-Local $f
    if ($local -and $local -ne $path) { Copy-Item -LiteralPath $local $path -Force; Say "用已经下好的 $($f.Name)（$local）" }
    $urls = @($script:GhProxies | ForEach-Object { "$_$($f.Url)" })
    if ($f.Size -ge 100MB) { $urls = @(Sort-BySpeed $urls) }
    Get-File -Urls $urls -OutFile $path -MinBytes $f.Size -Sha256 $f.Sha -ExpectedBytes $f.Size | Out-Null
}

# SenseVoice 的单个文件：先找他手动下好的，再 ModelScope（三个仓库）→ hf-mirror；下在 downloads 里，核对通过才挪进 asr
function Get-SvFile($f) {
    $dest = "$asr\$($f.Name)"
    if (Test-Sha $dest $f) { return }
    $part = "$dl\sv-$($f.Name)"
    if (Test-Path -LiteralPath $dest) {
        # 老版本断在半截的：挪到 downloads 当续传的底子（那边已有半截就丢掉这份）
        if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $dest -Force } else { Move-Item -LiteralPath $dest $part -Force }
    }
    $local = Find-Local $f
    if ($local -eq $part) { Move-Item -LiteralPath $part $dest -Force; return }
    if ($local) { Copy-Item -LiteralPath $local $dest -Force; Say "用已经下好的 $($f.Name)（$local）"; return }
    $urls = @($f.Repos | ForEach-Object { "https://modelscope.cn/models/$_/$msName/resolve/master/$($f.Src)" }) + "$hf/$($f.Src)"
    Get-File -Urls $urls -OutFile $part -MinBytes $f.Size -Sha256 $f.Sha -ExpectedBytes $f.Size | Out-Null
    Move-Item -LiteralPath $part $dest -Force
}

New-Item -ItemType Directory -Force $dl, $asr | Out-Null
if ((Has-Base) -and (-not $Plus -or (Has-Plus))) { Done }

$tmp = "$Dir\asr-unzip"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $tmp | Out-Null
if (-not (Test-Path $exe)) {
    # PyPI sherpa_onnx_bin 的程序在 Windows 真识别会崩溃，保留官方带 DLL 的构建（只有 GitHub 有）。
    Say '下语音识别程序（约 23 MB，GitHub 加速地址）'
    try { Get-Verified $base[0] } catch { Say "没下成：$($_.Exception.Message)"; Stop-WithManualHelp 'exe' }
    & tar.exe -xf "$dl\$($base[0].Name)" -C $tmp 2>$null
    if ($LASTEXITCODE -ne 0) { throw '识别程序解压失败' }
    $bin = Get-ChildItem $tmp -Recurse -Filter 'sherpa-onnx-offline.exe' | Select-Object -First 1
    if (-not $bin) { throw "解压后没找到识别程序（看 $tmp）" }
    foreach ($n in 'sherpa-onnx-offline.exe', 'onnxruntime.dll', 'onnxruntime_providers_shared.dll') { Copy-Item "$($bin.DirectoryName)\$n" $asr -Force -ErrorAction Stop }
}
if (-not (Has-Base)) {
    $svOk = $true
    try {
        Say '下识别模型（约 226 MB；先走国内 ModelScope，不行再 hf-mirror）'
        foreach ($f in $svFiles[0..1]) { Get-SvFile $f }
    } catch {
        $svOk = $false
        Say "ModelScope、hf-mirror 都没下成（$($_.Exception.Message)），改试 GitHub 的原模型包（约 158 MB）"
    }
    if (-not $svOk) {
        try { Get-Verified $base[1] } catch { Say "没下成：$($_.Exception.Message)"; Stop-WithManualHelp 'model' }
        & tar.exe -xf "$dl\$($base[1].Name)" -C $tmp 2>$null
        if ($LASTEXITCODE -ne 0) { throw '识别模型解压失败' }
        $model = Get-ChildItem $tmp -Recurse -Filter 'model.int8.onnx' | Select-Object -First 1
        if (-not $model) { throw "解压后没找到识别模型（看 $tmp）" }
        Copy-Item $model.FullName, "$($model.DirectoryName)\tokens.txt" $asr -Force -ErrorAction Stop
        Copy-Item "$($model.DirectoryName)\test_wavs\zh.wav" "$asr\test-zh.wav" -Force -ErrorAction Stop
    }
}
if ($Plus -and -not (Has-Plus)) {
    Say '下增强版（约 840 MB，网速一般要 5 到 10 分钟，断了重跑会接着下）'
    Get-Verified $plusFile
    Say '下好了，解压（一两分钟）'
    & tar.exe -xf "$dl\$($plusFile.Name)" -C $tmp 2>$null
    if ($LASTEXITCODE -ne 0) { throw "解压 $($plusFile.Name) 失败" }
    $llm = Get-ChildItem $tmp -Recurse -Filter 'llm.int8.onnx' | Select-Object -First 1
    if (-not $llm) { throw "解压后没找到增强版模型（看 $tmp）" }
    New-Item -ItemType Directory -Force $nano | Out-Null
    foreach ($n in 'encoder_adaptor.int8.onnx', 'llm.int8.onnx', 'embedding.int8.onnx') { Move-Item "$($llm.DirectoryName)\$n" $nano -Force }
    Remove-Item "$nano\Qwen3-0.6B" -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item "$($llm.DirectoryName)\Qwen3-0.6B" $nano -Force
}
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
# 老版本用的断句程序和断句模型（10-27 晚改成切段整认后不用了）
Remove-Item "$asr\sherpa-onnx-vad-with-offline-asr.exe", "$asr\silero_vad.onnx", "$asr\test-zh-pad.wav" -Force -ErrorAction SilentlyContinue
if (-not (Has-Base)) { throw '识别程序或模型还不全（按校验核对没过），看上面的输出' }
Done
