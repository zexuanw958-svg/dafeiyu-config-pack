# 弹「大肥鱼掉线了」的通知（右下角），点一下重新登录。qq_watch.py 和工作台「试一下」调它。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File qq-toast.ps1 -Dir <工作目录> -Kind offline|kicked|test
# 点通知走系统的网址协议（只写当前用户的注册表，不用管理员）：本机那套打开 tools\relogin.cmd，云端那套打开工作台并弹出扫码。
# 通知接口只有 Windows PowerShell 5.1 能用；打印的最后一行是结果，退出码 0 才算弹出来。
param([Parameter(Mandatory)][string]$Dir, [ValidateSet('offline', 'kicked', 'test')][string]$Kind = 'offline')
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}   # 调它的 Python 按 UTF-8 读，不然中文提示是乱码
$Dir = [IO.Path]::GetFullPath($Dir).TrimEnd('\')
$tools = "$Dir\tools"
$cloudTxt = "$Dir\private\cloud.txt"
$isCloud = (Test-Path $cloudTxt) -and (Select-String -Path $cloudTxt -Pattern '^\s*(host|ip)\s*[:：]\s*\S' -Quiet) -and -not (Test-Path "$Dir\start-astrbot.cmd") -and -not (Test-Path "$Dir\astrbot\data")
$scheme = if ($isCloud) { 'dafeiyu-cloud-relogin' } else { 'dafeiyu-relogin' }
$target = if ($isCloud) { "$tools\workbench-relogin.cmd" } else { "$tools\relogin.cmd" }
$aumid = 'Dafeiyu.QQWatch'
$icon = "$tools\workbench\assets\toast.png"

# 登记：通知上显示的名字和图标、点通知打开什么。每次都写，工作目录挪过也跟得上
$app = "HKCU:\Software\Classes\AppUserModelId\$aumid"
New-Item -Path $app -Force | Out-Null
Set-ItemProperty -Path $app -Name DisplayName -Value '大肥鱼'
if (Test-Path $icon) { Set-ItemProperty -Path $app -Name IconUri -Value $icon }
$proto = "HKCU:\Software\Classes\$scheme"
New-Item -Path "$proto\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path $proto -Name '(default)' -Value 'URL:大肥鱼重新登录'
Set-ItemProperty -Path $proto -Name 'URL Protocol' -Value ''
Set-ItemProperty -Path "$proto\shell\open\command" -Name '(default)' -Value "`"$target`""

$text = @{
    offline = @('大肥鱼掉线了', '小号没连上她，QQ 上她现在不会回话。点一下重新登录。')
    kicked  = @('小号被 QQ 踢下线了', '点一下重新登录。手机 QQ 提示有风险的，先在手机上按提示处理。')
    test    = @('掉线提醒开着呢', '这是试一下。真掉线时会这样弹，点一下就重新登录。')
}[$Kind]
if ($isCloud -and $Kind -ne 'test') { $text[1] = $text[1] -replace '点一下重新登录', '点一下打开工作台扫码' }
$esc = { param($s) [Security.SecurityElement]::Escape($s) }
$img = if (Test-Path $icon) { "<image placement=`"appLogoOverride`" src=`"$(& $esc ('file:///' + ($icon -replace '\\', '/')))`"/>" } else { '' }
$uri = "${scheme}:go"
$xml = @"
<toast activationType="protocol" launch="$uri" scenario="reminder">
  <visual><binding template="ToastGeneric"><text>$(& $esc $text[0])</text><text>$(& $esc $text[1])</text>$img</binding></visual>
  <actions><action content="$(if ($isCloud) { '打开工作台' } else { '重新登录' })" activationType="protocol" arguments="$uri"/><action content="等会儿" activationType="system" arguments="dismiss"/></actions>
</toast>
"@

try {
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]
    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml($xml)
    $n = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aumid)
    $setting = ''; try { $setting = "$($n.Setting)" } catch {}   # 有的系统刚登记完读不出来，不算关了
    if ($setting -and $setting -ne 'Enabled') { Write-Output "系统把通知关了（$setting）。去「设置 → 系统 → 通知」打开，再打开「大肥鱼」那一项。"; exit 2 }
    $n.Show([Windows.UI.Notifications.ToastNotification]::new($doc))
    Write-Output '弹了'
    exit 0
} catch {
    # 老系统或被精简过的系统没有这套接口：退回托盘气泡。它要开着才认点击，等 25 秒（调它的最多等 40 秒）
    try {
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $tray = New-Object Windows.Forms.NotifyIcon
        $tray.Icon = if (Test-Path "$tools\workbench\dafeiyu.ico") { New-Object Drawing.Icon "$tools\workbench\dafeiyu.ico" } else { [Drawing.SystemIcons]::Information }
        $tray.Text = '大肥鱼'; $tray.Visible = $true
        Register-ObjectEvent $tray BalloonTipClicked -Action { Start-Process $event.MessageData } -MessageData $target | Out-Null
        $tray.ShowBalloonTip(25000, $text[0], $text[1], 'Warning')
        Write-Output '弹了（托盘气泡）'
        $end = (Get-Date).AddSeconds(25)
        while ((Get-Date) -lt $end) { [Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 300 }
        $tray.Dispose()
        exit 0
    } catch { Write-Output "通知没弹出来：$($_.Exception.Message)"; exit 1 }
}
