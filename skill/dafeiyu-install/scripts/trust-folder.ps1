# 把工作目录加进 Windows 自带杀毒（Defender）的排除项，NapCat、SnowLuma 的文件就不会被它当成可疑文件拿走。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File trust-folder.ps1 -Dir <工作目录> [-Check]
#   -Check：只看现在是谁在管杀毒、能不能用这个脚本，不改任何东西。
#   -Force：认不准谁在管杀毒时也照样加（Defender 关着时加了不起作用，也没坏处）。
# 只在 Defender 是这台电脑的主力杀毒时有用；装了火绒、360、电脑管家的，Defender 是关着的，要在那家软件里加信任区（references/杀毒软件.md）。
# 改排除项要管理员权限：Windows 会弹「你要允许此应用对你的设备进行更改吗」，观众点「是」。先跟他说清：加了以后 Defender 不再扫这个文件夹，里面只放大肥鱼的程序。
param([Parameter(Mandatory)][string]$Dir, [switch]$Check, [switch]$Force)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$Dir = [IO.Path]::GetFullPath($Dir).TrimEnd('\')

$av = Get-ThirdPartyAV
$mp = try { Get-MpComputerStatus -ErrorAction Stop } catch { $null }
$defOn = $mp -and $mp.AntivirusEnabled -and $mp.RealTimeProtectionEnabled
if ($av.Count -and -not $defOn -and -not $Force) { Say "这台电脑在用 $($av -join '、') 杀毒，Windows 自带的那个是关着的：要在 $($av -join '、') 里把 $Dir 加进信任区，步骤见 references/杀毒软件.md"; exit 2 }
if (-not $defOn -and -not $Force) { Say 'Windows 自带的杀毒没开着实时保护，也没认出别家杀毒软件：不用加'; exit 0 }
if ($av.Count) { Say "注意：除了 Windows 自带的，还认出了 $($av -join '、')，那边也要加信任区（references/杀毒软件.md）" }
if ($Check) { Say "Windows 自带的杀毒（Defender）在管这台电脑，可以用本脚本把 $Dir 加进排除项（会弹一次管理员确认）"; exit 0 }

# 在管理员窗口里加排除项、再读回来核对；结果写进一个小文件，因为管理员窗口的输出拿不回来
$flag = Join-Path $env:TEMP "dfy-trust-$([guid]::NewGuid().ToString('N')).txt"
$q = $Dir.Replace("'", "''"); $fq = $flag.Replace("'", "''")
$inner = "`$r = try { Add-MpPreference -ExclusionPath '$q' -ErrorAction Stop; if ((Get-MpPreference).ExclusionPath -contains '$q') { 'ok' } else { 'notfound' } } catch { 'err ' + `$_.Exception.Message }; `$r | Set-Content -Encoding UTF8 '$fq'"
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($inner))
Say "要把 $Dir 加进 Windows 自带杀毒的排除项。马上会弹「你要允许此应用对你的设备进行更改吗」，请观众点「是」"
try { $p = Start-Process powershell.exe -ArgumentList '-NoProfile', '-EncodedCommand', $enc -Verb RunAs -WindowStyle Hidden -PassThru; [void]$p.WaitForExit(120000) }
catch { Say '观众点了「否」，或者这个账户没有管理员权限：没加上。要加就重跑本脚本、弹窗时点「是」；也可以照 references/杀毒软件.md 在「Windows 安全中心」里手动加'; exit 5 }
$r = if (Test-Path $flag) { (Get-Content -Raw -Encoding UTF8 $flag).Trim() } else { '' }
Remove-Item $flag -Force -ErrorAction SilentlyContinue
if ($r -eq 'ok') { Say "加好了：Windows 自带的杀毒不会再动 $Dir 里的文件"; exit 0 }
Say "没加上（$(if ($r) { $r } else { '管理员窗口没回结果' })）。照 references/杀毒软件.md 在「Windows 安全中心」里手动加"
exit 3
