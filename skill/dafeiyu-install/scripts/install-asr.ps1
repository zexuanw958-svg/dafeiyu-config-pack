# 第 7 步（选了看视频）：装本机语音识别，让「大肥鱼看视频」插件听得懂视频里说的话。免费、不联网识别、不用注册。可以重复跑：已经有的会跳过。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File install-asr.ps1 -Dir C:\dafeiyu [-Plus]
# 默认装 sherpa-onnx 的现成程序（只留 sherpa-onnx-offline 一个 exe 和 onnxruntime 两个 dll）+ SenseVoice int8 模型（中英日韩粤，快）。
# -Plus 再装增强版 Fun-ASR-Nano（多约 840 MB）：湖南话、闽南话这类难懂的方言和歌词认得更准，但慢：每次先花约 6 秒读模型，短视频也要多等 10 秒左右、一分钟的多等半分钟左右。
# 不往 AstrBot 的 Python 里装包：插件直接调这个 exe，跟 ffmpeg 一样。
param([Parameter(Mandatory)][string]$Dir, [switch]$Plus)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$t0 = Get-Date
$asr = "$Dir\asr"
$nano = "$asr\funasr-nano"
$exe = "$asr\sherpa-onnx-offline.exe"
$rel = 'https://github.com/k2-fsa/sherpa-onnx/releases/download'
$base = @(
    @{ Name = 'sherpa-onnx-v1.13.8-win-x64-shared-MT-Release-no-tts.tar.bz2'; Url = "$rel/v1.13.8/sherpa-onnx-v1.13.8-win-x64-shared-MT-Release-no-tts.tar.bz2"; Sha = '4b0a94f7b5c606b1b64a19a831c2127559e4b3d34e195465ebc7be73d9ed4783'; Size = 23271851 },
    @{ Name = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09.tar.bz2'; Url = "$rel/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09.tar.bz2"; Sha = '7305f7905bfcf77fa0b39388a313f3da35c68d971661a65475b56fb2162c8e63'; Size = 165783878 }
)
$plusFile = @{ Name = 'sherpa-onnx-funasr-nano-int8-2025-12-30.tar.bz2'; Url = "$rel/asr-models/sherpa-onnx-funasr-nano-int8-2025-12-30.tar.bz2"; Sha = 'eb43d7ccc2e86b243f6a03b7df361033dda66db9523d1a92bf6aca2b50c9476b'; Size = 841730611 }
$nanoFiles = 'encoder_adaptor.int8.onnx', 'llm.int8.onnx', 'embedding.int8.onnx', 'Qwen3-0.6B\tokenizer.json'

function Has-Base { (Test-Path $exe) -and (Test-Path "$asr\model.int8.onnx") -and (Test-Path "$asr\tokens.txt") }
function Has-Plus { @($nanoFiles | Where-Object { -not (Test-Path "$nano\$_") }).Count -eq 0 }

# 拿模型自带的中文样例试认一句，认得出来才算装好；装了增强版两个都试
function Try-Recognize([string[]]$model) {
    $out = & $exe '--num-threads=4' @model "$asr\test-zh.wav" 2>&1 | Out-String
    $line = ($out -split "`r?`n") | Where-Object { $_ -match '^\{' } | Select-Object -First 1
    $text = if ($line) { (($line | ConvertFrom-Json).text -replace '<\|[^|>]*\|>', '').Trim() } else { '' }
    if (-not $text) { Say "识别程序跑了但没认出字，输出的最后几行："; ($out -split "`r?`n" | Select-Object -Last 8) | ForEach-Object { Say "  $_" }; exit 1 }
    $text
}
function Done {
    $sv = Try-Recognize @("--tokens=$asr\tokens.txt", "--sense-voice-model=$asr\model.int8.onnx", '--sense-voice-use-itn=1', '--sense-voice-language=zh')
    Say "语音识别能用，试认样例：$sv"
    if (Has-Plus) {
        $fn = Try-Recognize @("--funasr-nano-encoder-adaptor=$nano\encoder_adaptor.int8.onnx", "--funasr-nano-llm=$nano\llm.int8.onnx", "--funasr-nano-embedding=$nano\embedding.int8.onnx", "--funasr-nano-tokenizer=$nano\Qwen3-0.6B")
        Say "增强版也能用，试认样例：$fn"
    }
    [IO.File]::WriteAllText("$Dir\asr-path.txt", $asr)
    Say "位置记在 $Dir\asr-path.txt；导入配置包时 answers.json 里 asr_dir 填这个路径（video 填 1）"
    Say "用时 $([int]((Get-Date) - $t0).TotalSeconds) 秒"
    exit 0
}

# 下到 downloads 里并校验；不返回路径（Say 是 Write-Output，会混进返回值）
function Get-Verified($f) {
    $path = "$dl\$($f.Name)"
    $urls = @($script:GhProxies | ForEach-Object { "$_$($f.Url)" })
    if ($f.Size -ge 100MB) { $urls = @(Sort-BySpeed $urls) }
    Get-File -Urls $urls -OutFile $path -MinBytes $f.Size -Sha256 $f.Sha -ExpectedBytes $f.Size | Out-Null
}

if ((Has-Base) -and (-not $Plus -or (Has-Plus))) { Done }

$dl = "$Dir\downloads"; New-Item -ItemType Directory -Force $dl | Out-Null
$tmp = "$Dir\asr-unzip"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $tmp, $asr | Out-Null
if (-not (Has-Base)) {
    # PyPI sherpa_onnx_bin 的程序在 Windows 真识别会崩溃，保留官方带 DLL 的构建。
    Say '下语音识别程序（约 23 MB）'
    Get-Verified $base[0]
    & tar.exe -xf "$dl\$($base[0].Name)" -C $tmp 2>$null
    if ($LASTEXITCODE -ne 0) { throw '识别程序解压失败' }
    $bin = Get-ChildItem $tmp -Recurse -Filter 'sherpa-onnx-offline.exe' | Select-Object -First 1
    if (-not $bin) { throw "解压后没找到识别程序（看 $tmp）" }
    foreach ($n in 'sherpa-onnx-offline.exe', 'onnxruntime.dll', 'onnxruntime_providers_shared.dll') { Copy-Item "$($bin.DirectoryName)\$n" $asr -Force -ErrorAction Stop }
    # 仍用 2025-09-09 版。逐文件下，HF 镜像失败再下 GitHub 的原压缩包。
    $hf = 'https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09/resolve/355f4d4884d8afd08aef04b9007a8556d7b463b2'
    try {
        Say '下识别模型（HF 镜像，约 226 MB）'
        Get-File @("$hf/model.int8.onnx") "$asr\model.int8.onnx" 200MB '12ca1a2ae7ecf3e0019ef2822307ee0b5cadc9196569e379b4c4026f8205276d' 237115547 | Out-Null
        Get-File @("$hf/tokens.txt") "$asr\tokens.txt" 200KB 'f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc' | Out-Null
        Get-File @("$hf/test_wavs/zh.wav") "$asr\test-zh.wav" 100KB 'b77f1794fe374a0ba1ee1dc458bfaf9349496cbbfc32780c50ba3c5a7ad8e373' | Out-Null
    } catch {
        Say 'HF 镜像没下完整，改试 GitHub 的原模型包（约 158 MB）'
        Get-Verified $base[1]
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
Done
