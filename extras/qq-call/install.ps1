param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $Python -m venv .venv
}

$vpy = Join-Path $here ".venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip
& $vpy -m pip install -r requirements.txt

if (-not (Test-Path "config.json")) {
    Copy-Item "config.example.json" "config.json"
}

Write-Host ""
Write-Host "Python 依赖安装完成。接下来："
Write-Host "1. 安装 VB-CABLE，并在 QQ 设置里选择虚拟麦克风。"
Write-Host "2. 安装 FFmpeg，并把路径填到 config.json 的 ffmpeg。"
Write-Host "3. 准备 sherpa-onnx SenseVoice 模型，把 exe/model/tokens 填到 config.json 的 asr。"
Write-Host "4. 修改 config.json 的 llm、tts。"
Write-Host "5. 先手动接听测试：.venv\Scripts\python.exe call_agent.py"
