# 备份：把 private\（key、密码、QQ 号）和 astrbot\data（人格、记忆、聊天记录、插件设置）打成桌面上一个 zip。换电脑、重装、卸载前用。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File backup.ps1 -Dir <工作目录> [-Out <zip 路径>] [-WithMemes]
#   表情包（meme_manager，几百 MB，重装时会重新下）默认不进备份；他自己加过表情、想一起留着才加 -WithMemes。
#   AstrBot 开着也能备份（数据库连同 -wal、-shm 一起拷），最稳的是先停 AstrBot 再备份（uninstall.ps1 就是这么做的）。
# 包里有他的密钥：跟他说只给自己留着，别发给任何人，也别传网盘分享。
param([Parameter(Mandatory)][string]$Dir, [string]$Out = '', [switch]$WithMemes)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Stop'
$Dir = [IO.Path]::GetFullPath($Dir).TrimEnd('\')
if (-not (Test-Path "$Dir\private") -and -not (Test-Path "$Dir\astrbot\data")) { throw "$Dir 里没有 private 和 astrbot\data，不像大肥鱼的工作目录" }
if (-not $Out) { $Out = Join-Path ([Environment]::GetFolderPath('Desktop')) ('大肥鱼备份-{0}.zip' -f (Get-Date -Format 'yyyyMMdd-HHmm')) }
if (Test-Path $Out) { throw "$Out 已经有了，换个名字（-Out）或者等一分钟再备份" }

$skip = @('astrbot\data\temp')   # 临时文件，没用
if (-not $WithMemes) { $skip += 'astrbot\data\plugin_data\meme_manager' }
$files = New-Object Collections.ArrayList
foreach ($sub in 'private', 'astrbot\data') {
    if (-not (Test-Path "$Dir\$sub")) { continue }
    foreach ($f in Get-ChildItem "$Dir\$sub" -Recurse -File -Force -ErrorAction SilentlyContinue) {
        $rel = $f.FullName.Substring($Dir.Length + 1)
        if (-not ($skip | Where-Object { $rel.StartsWith("$_\", [StringComparison]::OrdinalIgnoreCase) })) { [void]$files.Add($f) }
    }
}
$total = ($files | Measure-Object Length -Sum).Sum
Say ('要备份 {0} 个文件，{1:N0} MB{2}' -f $files.Count, ($total / 1MB), $(if ($WithMemes) { '（含表情包）' } else { '（表情包没带，重装时会重新下）' }))
$free = (New-Object IO.DriveInfo([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Out)))).AvailableFreeSpace
if ($free -lt $total + 200MB) { throw ('放备份的盘只剩 {0:N1} GB，不够。用 -Out 换个盘，比如 -Out D:\大肥鱼备份.zip' -f ($free / 1GB)) }

Add-Type -AssemblyName System.IO.Compression
$readme = @"
大肥鱼的备份（$(Get-Date -Format 'yyyy-MM-dd HH:mm')，从 $Dir 打的包）

里面有：
  private\       DeepSeek 的 key、AstrBot 后台密码、QQ 号等（别发给任何人）
  astrbot\data\  她的人格、记忆、聊天记录、插件设置$(if (-not $WithMemes) { "`r`n  （表情包没放进来，重装时会重新下）" })

怎么还原：在新电脑（或重装后）照教程把大肥鱼装到能聊天，然后跟 AI 帮手说：
  「用桌面上的 $(Split-Path $Out -Leaf) 还原大肥鱼」
它会跑 restore.ps1 把这两样放回去。
"@
$zs = [IO.File]::Open($Out, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite)
$zip = New-Object IO.Compression.ZipArchive($zs, [IO.Compression.ZipArchiveMode]::Create)
$miss = New-Object Collections.ArrayList
try {
    $w = New-Object IO.StreamWriter($zip.CreateEntry('还原说明.txt').Open(), (New-Object Text.UTF8Encoding($true)))
    $w.Write($readme); $w.Dispose()
    # restore.ps1 读这个：原来的工作目录跟新的不一样时，提醒把看视频插件里的路径改过来
    $w = New-Object IO.StreamWriter($zip.CreateEntry('备份信息.json').Open(), (New-Object Text.UTF8Encoding($false)))
    $w.Write((@{ dir = $Dir; time = (Get-Date -Format 's'); memes = [bool]$WithMemes } | ConvertTo-Json -Compress)); $w.Dispose()
    $n = 0; $t0 = Get-Date
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($Dir.Length + 1)
        try {
            # AstrBot 开着时数据库被它占着写，要允许共享读写才打得开
            $src = [IO.File]::Open($f.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]'ReadWrite, Delete')
            try {
                $e = $zip.CreateEntry($rel.Replace('\', '/'), [IO.Compression.CompressionLevel]::Optimal)
                $e.LastWriteTime = $f.LastWriteTime
                $dst = $e.Open(); try { $src.CopyTo($dst) } finally { $dst.Dispose() }
            } finally { $src.Dispose() }
        } catch { [void]$miss.Add($rel) }
        $n++
        if (((Get-Date) - $t0).TotalSeconds -ge 15) { Say ('已打包 {0}/{1} 个文件' -f $n, $files.Count); $t0 = Get-Date }
    }
} finally { $zip.Dispose(); $zs.Dispose() }
Say ('备份好了：{0}（{1:N0} MB）' -f $Out, ((Get-Item $Out).Length / 1MB))
if ($miss.Count) { Say "有 $($miss.Count) 个文件没打进去（被别的程序锁着）：$(($miss | Select-Object -First 5) -join '、')$(if ($miss.Count -gt 5) { ' 等' })" }
Say '包里有密钥：只给自己留着，别发给任何人，也别传网盘分享'
