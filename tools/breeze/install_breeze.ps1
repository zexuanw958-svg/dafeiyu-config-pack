# 装 Breeze TTS 2 本地语音（Windows）。由上两层的「装Breeze语音.bat」调用，可以反复运行，下好的会跳过。
# 程序来自 github.com/HoppouAI/Breeze-TTS-2.cpp（Apache-2.0），模型来自 Hugging Face（只许研究和非商业使用）。
param([string]$Dir = "", [string]$Model = "")
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Release = 'v0.1.3'
$ZipName = "breeze-tts-2-$Release-windows-x64-vulkan.zip"
$ZipUrl = "https://github.com/HoppouAI/Breeze-TTS-2.cpp/releases/download/$Release/$ZipName"
$ZipSha = 'd31feee5380e0609dc44b537d22cbe4f3cf8e93495756f3a580d8b448db79f7f'
$Models = @{
    'q8_0' = @{ File = 'breeze-tts-2-q8_0.gguf'; Bytes = 3568844480; Sha = 'a02bcc4b69b0601032727f8040c4942149b1b73aa0f69022fe5aaa6a8f0ef879' }
    'q4_k' = @{ File = 'breeze-tts-2-q4_k.gguf'; Bytes = 2538909376; Sha = '483418fbbb438f5f1c08dbe2b017e42db0f1d126765cd3f772d8622a30a4915c' }
}
# GitHub 加速站按顺序试，最后的空串是直连；挂了往这里补新的
$Accel = @('https://gh.dpik.top/', 'https://edgeone.gh-proxy.com/', 'https://hk.gh-proxy.org/', 'https://gh-proxy.org/', '')
$HfHosts = @('https://hf-mirror.com', 'https://huggingface.co')
$Port = 8137

function Say($m) { Write-Host $m }
function Fail($m) { Write-Host "  ！$m" -ForegroundColor Yellow; exit 1 }

function Test-Server {
    try { $null = Invoke-WebRequest "http://127.0.0.1:$Port/v1/voices" -UseBasicParsing -TimeoutSec 3; return $true } catch { return $false }
}

function Get-VramGB {
    # 先问 nvidia-smi（N 卡最准），再读显卡驱动在注册表里记的显存（A 卡、I 卡）
    try {
        $mb = & nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1
        if ($mb) { return [math]::Round([double]$mb / 1024, 1) }
    } catch {}
    $best = 0
    $key = 'HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
    Get-ChildItem $key -ErrorAction SilentlyContinue | ForEach-Object {
        $v = (Get-ItemProperty $_.PSPath -Name 'HardwareInformation.qwMemorySize' -ErrorAction SilentlyContinue).'HardwareInformation.qwMemorySize'
        if ($v -and [double]$v -gt $best) { $best = [double]$v }
    }
    return [math]::Round($best / 1GB, 1)
}

# 同一个文件的多个地址依次试；进度走 Host，管道里只返回成功的地址。
# ExpectedBytes 是确切大小；不知道时读响应头，MinBytes 仍会检查，防止把错误页当成文件。
function Get-BreezeDownload {
    param([Parameter(Mandatory)][string[]]$Urls, [Parameter(Mandatory)][string]$OutFile,
          [long]$MinBytes = 1, [string]$Sha256 = '', [long]$ExpectedBytes = 0)
    $OutFile = [IO.Path]::GetFullPath($OutFile)
    $parent = Split-Path -Parent $OutFile
    New-Item -ItemType Directory -Force $parent -ErrorAction Stop | Out-Null
    if ($Sha256 -and (Test-Path $OutFile) -and (Get-Item $OutFile).Length -ge $MinBytes -and
        (Get-FileHash $OutFile -Algorithm SHA256).Hash -eq $Sha256) { Say '文件已经下好，校验通过' | Write-Host; return $Urls[0] }
    $tag = [guid]::NewGuid().ToString('N')
    $headers = "$OutFile.$tag.headers"; $errors = "$OutFile.$tag.stderr"
    function Read-DownloadTotal {
        if (-not (Test-Path $headers)) { return 0L }
        # curl 正在写响应头；允许共享读写，没写完就等下一次进度再读。
        try {
            $stream = [IO.File]::Open($headers, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            $reader = New-Object IO.StreamReader($stream)
            try { $h = $reader.ReadToEnd() } finally { $reader.Dispose() }
        } catch { return 0L }
        if (-not $h.Trim()) { return 0L }
        # 只看最后一段响应，不能把重定向页面的大小当作文件大小。
        $blocks = @($h.Trim() -split '\r?\n\r?\n')
        $last = $blocks[-1]
        if ($last -match '(?im)^content-range:\s*bytes\s+(?:\d+-\d+|\*)/(\d+)') { return [long]$Matches[1] }
        if ($last -match '(?im)^HTTP/\S+\s+200\b' -and $last -match '(?im)^content-length:\s*(\d+)') { return [long]$Matches[1] }
        return 0L
    }
    function Check-DownloadSpace([long]$total) {
        if ($total -le 0) { return }
        $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($OutFile))
        if ($drive.AvailableFreeSpace -lt 2 * $total) {
            throw ('目标盘空间不够：下载和解压至少要留 {0:N1} GB，现在还剩 {1:N1} GB。腾出空间或换个盘再试。' -f (2 * $total / 1GB), ($drive.AvailableFreeSpace / 1GB))
        }
    }
    try {
        foreach ($u in $Urls) {
            $label = ([uri]$u).Host
            $total = $ExpectedBytes
            if (-not $total) {
                # HEAD 只取大小，不输出地址、响应头或 curl 的原始错误。
                & curl.exe -I -L -f -sS --connect-timeout 12 --speed-limit 30000 --speed-time 45 -D $headers -o NUL $u 2>$null
                $total = Read-DownloadTotal
            }
            Check-DownloadSpace $(if ($total -gt 0) { $total } else { $MinBytes })
            if ($Sha256 -and $total -gt 0 -and (Test-Path $OutFile) -and (Get-Item $OutFile).Length -ge $total) {
                # 开头已排除了校验正确的成品；这份已下满却校验不对，不能从文件尾一直续传。
                Say '已有文件校验不对，清掉后重新下载' | Write-Host
                Remove-Item -LiteralPath $OutFile -Force -ErrorAction Stop
            }
            Say "开始下载（走 $label，断了会接着下）" | Write-Host
            for ($attempt = 0; $attempt -lt 2; $attempt++) {
                [IO.File]::WriteAllText($headers, '')
                $args2 = @('-L', '-f', '-sS', '-C', '-', '--retry', '3', '--retry-delay', '2', '--connect-timeout', '12', '--speed-limit', '30000', '--speed-time', '45', '-D', ('"{0}"' -f $headers), '-o', ('"{0}"' -f $OutFile), ('"{0}"' -f $u))
                $p = Start-Process curl.exe -ArgumentList $args2 -PassThru -NoNewWindow -RedirectStandardError $errors
                $null = $p.Handle # PS 5.1：先保留句柄，否则 ExitCode 可能是空的
                try {
                    $lastSize = if (Test-Path $OutFile) { (Get-Item $OutFile).Length } else { 0L }
                    $lastTime = Get-Date
                    while (-not $p.WaitForExit(15000)) {
                        $size = if (Test-Path $OutFile) { (Get-Item $OutFile).Length } else { 0L }
                        if (-not $total) { $total = Read-DownloadTotal; Check-DownloadSpace $total }
                        $now = Get-Date; $speed = [math]::Max(0, ($size - $lastSize) / [math]::Max(1, ($now - $lastTime).TotalSeconds))
                        if ($total -gt 0) {
                            $eta = if ($speed -gt 0) { '大约还要 {0:N0} 分钟' -f [math]::Ceiling([math]::Max(0, $total - $size) / $speed / 60) } else { '这会儿没增加，正在等服务器' }
                            Say ('已下 {0:N1}/{1:N1} MB，{2}（走 {3}）' -f ($size / 1MB), ($total / 1MB), $eta, $label) | Write-Host
                        } else { Say ('已下 {0:N1} MB，当前 {1:N2} MB/s（走 {2}）' -f ($size / 1MB), ($speed / 1MB), $label) | Write-Host }
                        $lastSize = $size; $lastTime = $now
                    }
                    $p.WaitForExit(); $code = $p.ExitCode
                } finally { if (-not $p.HasExited) { $p.Kill(); $p.WaitForExit() }; $p.Dispose() }
                if ($code -eq 33 -and $attempt -eq 0) {
                    Say '这个地址不支持续传，清掉半截文件，从头再下一次' | Write-Host
                    Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
                    continue
                }
                $size = if (Test-Path $OutFile) { (Get-Item $OutFile).Length } else { 0L }
                $ok = $code -eq 0 -and $size -ge $MinBytes -and ($ExpectedBytes -le 0 -or $size -eq $ExpectedBytes)
                if ($ok -and $Sha256) {
                    $ok = (Get-FileHash $OutFile -Algorithm SHA256).Hash -eq $Sha256
                    if (-not $ok) { Say '文件校验没对上，删掉这份，换下一个源' | Write-Host; Remove-Item -LiteralPath $OutFile -Force }
                }
                if ($ok) { Say ('下好了（走 {0}，{1:N1} MB）' -f $label, ($size / 1MB)) | Write-Host; return $u }
                # curl 成功但文件太小/大小不对，说明不是要的文件；网络中断则保留半截文件。
                if ($code -eq 0 -and (Test-Path $OutFile)) { Remove-Item -LiteralPath $OutFile -Force }
                break
            }
            Say "走 $label 没下成，换下一个（半截文件会留着续传）" | Write-Host
        }
        throw '几个下载地址都没下成。已下载的部分会保留，查好网络后重跑即可。'
    } finally {
        Remove-Item -LiteralPath $headers, $errors -Force -ErrorAction SilentlyContinue
    }
}

function Get-File($urls, $out, $bytes, $sha) {
    Get-BreezeDownload -Urls @($urls) -OutFile $out -MinBytes $(if ($bytes) { $bytes } else { 1MB }) -Sha256 $sha -ExpectedBytes $bytes | Out-Null
}

Say "装 Breeze TTS 2 本地语音"
Say ""

# ---------- 装到哪 ----------
if (-not $Dir) {
    $def = "$env:SystemDrive\breeze-tts"
    $Dir = Read-Host "装到哪（直接回车用 $def；路径别带中文和空格）"
    if (-not $Dir) { $Dir = $def }
}
$Dir = $Dir.Trim().Trim('"').TrimEnd('\')
if ($Dir -match '[^\x21-\x7E]') { Fail "路径里有中文或空格，换一个全英文的，比如 D:\breeze-tts" }
New-Item -ItemType Directory -Force "$Dir\bin", "$Dir\models", "$Dir\voices" | Out-Null

# ---------- 看显卡，挑模型 ----------
$gpus = (Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) -join '、'
$vram = Get-VramGB
Say "显卡：$gpus（显存约 $vram G）"
if (-not $Model) {
    if ($vram -ge 6) { $Model = 'q8_0' }
    else { $Model = 'q4_k' }
}
$m = $Models[$Model]
if (-not $m) { Fail "不认识的模型 $Model，只能是 q8_0 或 q4_k" }
if ($vram -lt 3) {
    Say "  ！显存不到 3G，会退到 CPU 上算，一句话可能要十几秒以上。能用，但不推荐。"
}
Say "用的模型：$Model（$([math]::Round($m.Bytes / 1GB, 1)) GB）"

# ---------- 下程序 ----------
Say ""
Say "▶ 下载 Breeze 程序（约 75 MB）"
$zip = "$Dir\$ZipName"
Get-File ($Accel | ForEach-Object { $_ + $ZipUrl }) $zip 0 $ZipSha
Expand-Archive -Force $zip "$Dir\bin"
$exe = Get-ChildItem -Recurse "$Dir\bin" -Filter breeze-server.exe | Select-Object -First 1 -ExpandProperty FullName
if (-not $exe) { Fail "解压后没找到 breeze-server.exe" }

# ---------- 下模型 ----------
Say ""
Say "▶ 下载模型（$([math]::Round($m.Bytes / 1GB, 1)) GB，网速慢要十几分钟，下断了再双击会接着下）"
$gguf = "$Dir\models\$($m.File)"
Get-File ($HfHosts | ForEach-Object { "$_/HoppouAI/Breeze-TTS-2.cpp/resolve/main/$($m.File)" }) $gguf $m.Bytes $m.Sha

# ---------- 启动脚本 ----------
$bat = "$Dir\start-breeze.cmd"
$lines = @(
    '@echo off',
    'title Breeze TTS - keep this window open',
    "cd /d `"$Dir`"",
    "`"$exe`" `"$gguf`" --host 127.0.0.1 --port $Port --voices-dir `"$Dir\voices`" --ws-port -1 --webui"
)
[IO.File]::WriteAllText($bat, ($lines -join "`r`n") + "`r`n", (New-Object Text.UTF8Encoding $false))

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) '启动Breeze语音.lnk'))
$lnk.TargetPath = $bat; $lnk.WorkingDirectory = $Dir; $lnk.WindowStyle = 7; $lnk.Save()
Say ""
Say "桌面上放了「启动Breeze语音」，以后双击它就能开语音服务。"
$auto = Read-Host "开机自动启动 Breeze 吗？（直接回车是要，输入 n 不要）"
if ($auto -ne 'n') {
    $lnk = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) '启动Breeze语音.lnk'))
    $lnk.TargetPath = $bat; $lnk.WorkingDirectory = $Dir; $lnk.WindowStyle = 7; $lnk.Save()
    Say "  好，开机会自己起来（最小化的黑窗口，别关）"
}

# ---------- 启动并等它好 ----------
Say ""
if (Test-Server) {
    Say "Breeze 已经在运行了。换了模型的话，关掉那个黑窗口再双击桌面的「启动Breeze语音」。"
} else {
    Say "▶ 启动 Breeze（第一次要预热一会儿）"
    Start-Process -FilePath $bat -WorkingDirectory $Dir -WindowStyle Minimized
    $ok = $false
    for ($i = 0; $i -lt 90; $i++) { Start-Sleep 2; if (Test-Server) { $ok = $true; break } }
    if (-not $ok) { Fail "等了 3 分钟还没起来。看看任务栏里那个黑窗口有没有报错，截图发给帮你装的人。" }
}
Say ""
Say "装好了。浏览器打开 http://127.0.0.1:$Port 能看到 Breeze 自带的网页（英文的）。"
Say "下一步：双击配置包里的「做声音.bat」，给她做一个声音。"
