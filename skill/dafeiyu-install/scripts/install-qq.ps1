# 第 4 步（协议端选 SnowLuma、这台电脑没装桌面 QQ 时）：从 QQ 官网拿官方安装包，确认是腾讯签名的，再静默安装。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File install-qq.ps1 -Dir <工作目录> [-InstallDir <装到哪>] [-DownloadOnly]
#   -InstallDir：不填时，工作目录不在系统盘就装到工作目录那个盘的 \Tencent\QQNT，在系统盘就用 QQ 默认位置。
#   -DownloadOnly：只下载、校验，不安装（测试用）。
# 安装时 Windows 会弹一次「你要允许此应用对你的设备进行更改吗」：QQ 是装给整台电脑的，要观众点「是」。
# 官网的下载地址要先向官网要一个签名（官网的下载按钮也是这么做的），没签名的地址直接下会被拒（403，10-28 实测）。
param([Parameter(Mandatory)][string]$Dir, [string]$InstallDir = '', [switch]$DownloadOnly)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$dl = "$Dir\downloads"; New-Item -ItemType Directory -Force $dl | Out-Null

$have = Find-DesktopQQ
if ($have -and -not $DownloadOnly) { Say "桌面 QQ 已经装好：$have，不用再装"; exit 0 }

# 1. 官网的下载配置：最新版本号和各架构的下载地址
$cfg = "$(& curl.exe -sS -L --connect-timeout 10 --max-time 30 'https://cdn-go.cn/qq-web/im.qq.com_new/latest/rainbow/windowsConfig.js' 2>$null)"
$key = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'ntDownloadARMUrl' } else { 'ntDownloadX64Url' }
$m = [regex]::Match($cfg, '"' + $key + '"\s*:\s*"([^"]+)"')
if (-not $m.Success) { Say '没从 QQ 官网读到下载地址（官网可能改版了）。请观众自己打开 im.qq.com 下载安装 QQ，装好后重跑 install-snowluma.ps1'; exit 4 }
$url = $m.Groups[1].Value
$ver = [regex]::Match($cfg, '"version"\s*:\s*"([^"]+)"').Groups[1].Value
$name = Split-Path $url -Leaf
Say "QQ 官网现在的版本：$ver（$name）"

# 2. 向官网要签名地址（跟官网下载按钮一样：POST 网址，回一个带 sign 和 t 的地址；拿不到就用原地址试）
function Get-SignedUrl([string]$Raw) {
    $tmp = "$dl\qq-sign"; New-Item -ItemType Directory -Force $tmp | Out-Null
    [IO.File]::WriteAllText("$tmp\body.json", (@{ url = $Raw } | ConvertTo-Json -Compress))
    # 请求头里有双引号，PowerShell 5.1 传给 curl 会把引号吃掉，所以写进文件让 curl 自己读（-H @文件）
    [IO.File]::WriteAllText("$tmp\headers.txt", "Content-Type: application/json`r`nx-oidb: {`"uint32_command`":`"0x9b8e`",`"uint32_service_type`":1}`r`n")
    $out = "$(& curl.exe -sS --connect-timeout 10 --max-time 20 -X POST -H "@$tmp\headers.txt" --data-binary "@$tmp\body.json" 'https://im.qq.com/http2rpc/gotrpc/noauth/trpc.qqntv2.urlsign.UrlSign/GetSign' 2>$null)"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    try { $j = $out | ConvertFrom-Json; if ($j.retcode -eq 0 -and $j.data.url) { return [string]$j.data.url } } catch {}
    Say '没拿到官网的签名地址，用原地址试'
    return $Raw
}

# 3. 下载（能接着下；签名地址有时效，第一次没下完就重新要一个签名接着下）
$exe = "$dl\$name"
$okSig = { param($f) $s = Get-AuthenticodeSignature $f; $s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'Tencent' }
if (-not ((Test-Path $exe) -and (& $okSig $exe))) {
    Say "下载 QQ 官方安装包（约 330 MB，腾讯自己的服务器，国内一般很快）"
    $done = $false
    foreach ($try in 1, 2) {
        try { Get-File -Urls @(Get-SignedUrl $url) -OutFile $exe -MinBytes 100MB | Out-Null; $done = $true; break }
        catch { Say "第 $try 次没下完：$($_.Exception.Message)" }
    }
    if (-not $done) { Say 'QQ 安装包没下下来。请观众自己打开 im.qq.com 下载安装 QQ，装好后重跑 install-snowluma.ps1'; exit 3 }
}

# 4. 校验：必须是腾讯签名、签名有效的安装包，不然删掉
$sig = Get-AuthenticodeSignature $exe
if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Tencent') {
    Remove-Item $exe -Force -ErrorAction SilentlyContinue
    throw "下载的安装包签名不对（$($sig.Status)），已删掉。重跑本脚本重新下"
}
Say "安装包是腾讯签名的正版（$($sig.SignerCertificate.Subject -replace ',.*$', '')）"
if ($DownloadOnly) { Say "只下载：$exe"; exit 0 }

# 5. 装到哪：工作目录不在系统盘，就跟着放那个盘，不占 C 盘
if (-not $InstallDir) {
    $drv = (Split-Path $Dir -Qualifier)
    if ($drv -and $drv -ne $env:SystemDrive) { $InstallDir = "$drv\Tencent\QQNT" }
}
$args2 = @('/S')
if ($InstallDir) { $args2 += "/D=$InstallDir" }   # NSIS 的规矩：/D= 放最后、不加引号，路径里可以有空格
Say "开始安装$(if ($InstallDir) { "到 $InstallDir" } else { '（QQ 默认位置）' })。Windows 会弹「你要允许此应用对你的设备进行更改吗」，请观众点「是」"
try { $p = Start-Process $exe -ArgumentList $args2 -Verb RunAs -PassThru }
catch { Say '观众点了「否」，或者这个账户没有管理员权限，QQ 没装上。要装的话重跑本脚本、弹窗时点「是」'; exit 5 }
if (-not $p.WaitForExit(600000)) { Say '安装 10 分钟还没结束，看看屏幕上是不是有 QQ 安装窗口在等着点' }
Start-Sleep 3
$qq = Find-DesktopQQ
if (-not $qq) { Say 'QQ 安装程序跑完了，但没找到装好的 QQ。请观众看看开始菜单里有没有 QQ；没有就自己去 im.qq.com 下载安装'; exit 6 }
Say "桌面 QQ 装好了：$qq。下一步跑 install-snowluma.ps1"
