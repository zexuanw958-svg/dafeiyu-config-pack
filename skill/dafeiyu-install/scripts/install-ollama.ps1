# 第 7 步（记忆选本机）：装 Ollama、下 bge-m3、验证能出向量。可以重复跑：装好的、下好的会跳过。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File install-ollama.ps1 -Dir <工作目录>
# 新装的程序装到 <工作目录>\Ollama（官方安装包支持 /DIR），模型放 <工作目录>\ollama-models（OLLAMA_MODELS）；以前装过的不搬。
# 安装包是 Inno Setup，静默参数是 /VERYSILENT（/S 它不认，会停在「Install」按钮上等人点，10-27 测试踩过）。
param([Parameter(Mandatory)][string]$Dir, [string]$Version = 'v0.40.11')
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$t0 = Get-Date
$dl = "$Dir\downloads"; New-Item -ItemType Directory -Force $dl | Out-Null
# 装在哪：以前装过的（默认位置或工作目录里）就用原来的，不搬；新装的程序和模型一律放工作目录（问 ② 他挑的位置，不占 C 盘）
$home2 = @("$Dir\Ollama", "$env:LOCALAPPDATA\Programs\Ollama") | Where-Object { Test-Path "$_\ollama.exe" } | Select-Object -First 1
$fresh = -not $home2
if ($fresh) { $home2 = "$Dir\Ollama" }
$exe = "$home2\ollama.exe"
if ($fresh -and -not [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User')) {
    New-Item -ItemType Directory -Force "$Dir\ollama-models" | Out-Null
    [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', "$Dir\ollama-models", 'User')
    Say "模型放在 $Dir\ollama-models"
}
$m = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User'); if ($m) { $env:OLLAMA_MODELS = $m }

if (Test-Path $exe) { Say 'Ollama 已经装好了' } else {
    $setup = "$dl\OllamaSetup.exe"
    $url = "https://github.com/ollama/ollama/releases/download/$Version/OllamaSetup.exe"
    Say '下 Ollama 安装包（约 1.6 GB）'
    # 保留测速排序，真正下载由统一下载器负责续传和报进度。
    $srcs = @($script:GhProxies | ForEach-Object { "$_$url" }) + 'https://ollama.com/download/OllamaSetup.exe'
    $srcs = @(Sort-BySpeed $srcs)
    Get-File -Urls $srcs -OutFile $setup -MinBytes 1GB | Out-Null
    Say "安装包下好了（$([math]::Round((Get-Item $setup).Length / 1MB)) MB），静默安装到 $home2（不用管理员）"
    $p = Start-Process $setup -ArgumentList '/VERYSILENT', '/NORESTART', '/SUPPRESSMSGBOXES', "/DIR=`"$home2`"" -PassThru
    if (-not $p.WaitForExit(600000)) { throw '安装 10 分钟没结束：看屏幕上有没有弹窗（杀毒软件拦截之类）' }
    if (-not (Test-Path $exe)) { throw "安装器退出了（代码 $($p.ExitCode)），但没找到 $exe" }
    Say 'Ollama 装好了'
}

# 模型常驻、不自动卸载（默认空闲 5 分钟就卸，下一条消息要多等约 5 秒重新加载）。只在这次新装的 Ollama 上设：他以前自己装的 Ollama 里可能有大模型，全局常驻会一直占内存
if ($fresh -and -not [Environment]::GetEnvironmentVariable('OLLAMA_KEEP_ALIVE', 'User')) {
    [Environment]::SetEnvironmentVariable('OLLAMA_KEEP_ALIVE', '-1', 'User'); $env:OLLAMA_KEEP_ALIVE = '-1'
}

# 起服务：只起 ollama.exe serve，不开托盘程序（它每次启动都弹聊天窗口，见 common.ps1 的 Start-OllamaServe）。
# 安装包静默装完可能自己把托盘程序开起来了：这次新装的就把它关掉，换成不出窗口的服务
if ($fresh) { Get-Process 'ollama app' -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($home2, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -ErrorAction SilentlyContinue }
if (-not (Get-NetTCPConnection -State Listen -LocalPort 11434 -ErrorAction SilentlyContinue)) {
    Start-OllamaServe $exe
    if (-not (Wait-Port 11434 60)) { throw 'Ollama 60 秒没起来：让他从开始菜单打开 Ollama' }
}
Say 'Ollama 在跑（127.0.0.1:11434）'

# 模型单独封装，测试时可传临时名称，不动正在使用的 bge-m3。
function Install-DfyBge([string]$OllamaExe, [string]$DownloadDir, [string]$ModelName = 'bge-m3') {
    $have = (& $OllamaExe list 2>$null) -join "`n"
    if ($have -notmatch ('(?m)^' + [regex]::Escape($ModelName) + '(?::latest)?\s')) {
        try {
            Say '下记忆模型 bge-m3（ModelScope FP16，约 1.1 GB）'
            $gguf = "$DownloadDir\bge-m3-FP16.gguf"
            Get-File @('https://modelscope.cn/models/gpustack/bge-m3-GGUF/resolve/master/bge-m3-FP16.gguf') $gguf 1GB 'daec91ffb5dd0c27411bd71f29932917c49cf529a641d0168496c3a501e3062c' 1157671200 | Out-Null
            $mf = "$DownloadDir\bge-m3.Modelfile"
            [IO.File]::WriteAllText($mf, ('FROM "{0}"' -f ($gguf -replace '\\', '/')), (New-Object Text.UTF8Encoding($false)))
            & $OllamaExe create $ModelName -f $mf 2>&1 | Select-Object -Last 3 | ForEach-Object { "  $_" }
            if ($LASTEXITCODE -ne 0) { throw '导入 gguf 没成功' }
        } catch {
            Say 'ModelScope 的模型没装成，改从 Ollama 官方拉取'
            # 正常安装用 bge-m3；临时测试也只写临时名称。
            if ($ModelName -ne 'bge-m3') { throw '临时名称测试不拉取正式 bge-m3，避免碰已有模型' }
            & $OllamaExe pull bge-m3 2>&1 | Select-Object -Last 3 | ForEach-Object { "  $_" }
            if ($LASTEXITCODE -ne 0) { throw 'bge-m3 没下下来，查好网络后重跑会接着下' }
        }
    }
    $have = (& $OllamaExe list 2>$null) -join "`n"
    if ($have -notmatch ('(?m)^' + [regex]::Escape($ModelName) + '(?::latest)?\s')) { throw 'Ollama 模型列表里还没有要装的模型' }
    # 固定用 CPU 跑（10-28 测试机：放显卡上跟本机语音抢显存，空闲一会儿后第一下要卡 7～8 秒；i9 上 CPU 跑一次 50～170 毫秒，跟显卡一样快，还给语音和游戏腾出约 0.7 GB 显存）
    if (((& $OllamaExe show $ModelName --parameters 2>$null) -join "`n") -notmatch 'num_gpu\s+0') {
        $cpu = "$DownloadDir\bge-m3-cpu.Modelfile"
        [IO.File]::WriteAllText($cpu, "FROM $ModelName`nPARAMETER num_gpu 0`n", (New-Object Text.UTF8Encoding($false)))
        & $OllamaExe create $ModelName -f $cpu 2>&1 | Select-Object -Last 1 | ForEach-Object { "  $_" }
        if ($LASTEXITCODE -eq 0) { Say '记忆模型改成用 CPU 跑（不占显卡）' } else { Say '改成用 CPU 跑没成功，先照旧用显卡（偶尔会慢几秒）' }
    }
    # 不传 keep_alive：照 OLLAMA_KEEP_ALIVE 来（新装的常驻），自检完模型留在内存里，她第一条消息不用等加载
    $body = @{ model = $ModelName; input = '我家猫叫团子' } | ConvertTo-Json -Compress
    $r = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/embed' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 120
    $n = @($r.embeddings[0]).Count
    if ($n -ne 1024) { throw "模型向量长度是 $n，不是 1024" }
    Say "记忆模型能用（向量 1024 维）。用时 $([int]((Get-Date) - $t0).TotalSeconds) 秒。导入时 memory 填 1"
}
Install-DfyBge $exe $dl
