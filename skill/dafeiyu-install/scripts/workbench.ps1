# 装完以后的「大肥鱼工作台」：双击桌面图标 → 两三秒查一遍她在不在 → 生成本机网页打开（代替以前的《大肥鱼怎么用.txt》）。
# 只查不改：看端口、进程和文件，不登录后台、不读秘密。网页里的按钮直达 AstrBot 后台对应页面；换角色、上云这类复杂的事，复制一句话交给 Agent。
# 正式版工作台是 workbench\（本机小服务 + 小窗口，能修能补装）；这个脚本是它的兜底：找不到 AstrBot 自带的 Python 时，open.ps1 退回这里生成静态页。
# finish.ps1 把它拷到 <工作目录>\tools\，桌面快捷方式「大肥鱼工作台」指向同目录的 workbench.cmd。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File workbench.ps1 [-Dir C:\dafeiyu] [-NoOpen]
param([string]$Dir = (Split-Path $PSScriptRoot), [switch]$NoOpen)
$ErrorActionPreference = 'Continue'
$Dir = $Dir.TrimEnd('\')
function Esc([string]$s) { [Net.WebUtility]::HtmlEncode($s) }
function Listening([int]$p) { [bool](Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue) }

# 后台端口：backend.txt 的 url 里写的（启动器常另给端口），没有就 6185
$port = 6185
$bk = "$Dir\private\backend.txt"
if (Test-Path $bk) { foreach ($l in Get-Content -Encoding UTF8 $bk) { if ($l -match '^\s*url\s*[:：].*:(\d+)') { $port = [int]$Matches[1] } } }
$base = "http://127.0.0.1:$port"
$ans = @{}
try { (Get-Content -Raw -Encoding UTF8 "$Dir\answers.json" | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $ans[$_.Name] = "$($_.Value)" } } catch {}
$voice = $ans['voice']; $memory = $ans['memory']; $video = $ans['video'] -eq '1'
$reName = if ("$(Get-Content -Raw "$Dir\private\protocol.txt" -ErrorAction SilentlyContinue)".Trim() -eq 'snowluma') { '大肥鱼掉线-双击重登' } else { '大肥鱼掉线-双击扫码' }
$group = (Test-Path "$Dir\private\group.txt") -and ((Get-Content -Raw "$Dir\private\group.txt" -ErrorAction SilentlyContinue) -match '\d{5,}')

# ---- 状态灯：ok / bad / off（没装这项）
$rows = New-Object Collections.Generic.List[object]
function Row([string]$s, [string]$n, [string]$t) { $script:rows.Add(@($s, $n, $t)) }
$brain = Listening $port
if ($brain) { Row ok '大脑（AstrBot）' '开着' } else { Row bad '大脑（AstrBot）' "没开。双击桌面「$reName」，它会先把大脑开起来" }
$qq = [bool](Get-NetTCPConnection -RemotePort 6199 -State Established -ErrorAction SilentlyContinue)
if ($qq) { Row ok 'QQ' '小号在线' } elseif ($brain) { Row bad 'QQ' "小号没登上。双击桌面「$reName」，照弹出来的窗口做" } else { Row bad 'QQ' '大脑没开，QQ 也连不上，先照上一行做' }
switch ($voice) {
    '4' { if (Listening 8137) { Row ok '语音' 'Breeze 开着（你显卡算的，不花钱）' } else { Row bad '语音' "Breeze 没开，她只会打字。双击 $Dir\breeze-tts\start-breeze.cmd" } }
    '3' { Row ok '语音' '豆包（在线服务，不用开，按时长扣钱）' }
    '1' { Row ok '语音' 'Edge 免费女声（在线服务，不用开）' }
    '2' { Row ok '语音' '小米 MiMo（在线服务，不用开）' }
    default { Row off '语音' '没开' }
}
switch ($memory) {
    '1' { if (Listening 11434) { Row ok '记忆' 'Ollama 开着' } else { Row bad '记忆' 'Ollama 没开，她记不住新的事。从开始菜单打开 Ollama' } }
    '2' { Row ok '记忆' '硅基流动（在线服务，不用开）' }
    default { Row off '记忆' '没开' }
}
if ($video) {
    $asr = if ($ans['asr_dir']) { $ans['asr_dir'] } else { "$Dir\asr" }
    $ff = $ans['ffmpeg_path'] -and (Test-Path $ans['ffmpeg_path'])
    $hear = Test-Path "$asr\sherpa-onnx-offline.exe"
    $plus = Test-Path "$asr\funasr-nano\llm.int8.onnx"
    if (-not $ff) { Row bad '看视频' "找不到 ffmpeg（$($ans['ffmpeg_path'])），她看不了视频" }
             elseif ($hear) { Row ok '看视频' "能看画面，也能听视频里说的话$(if ($plus) { '（装了方言、歌词增强版）' })" }
             else { Row ok '看视频' '只能看画面，听不到视频里说的话' }
} else { Row off '看视频' '没装' }

$lights = for ($i = 0; $i -lt $rows.Count; $i++) {
    $r = $rows[$i]
    "<li class=`"$($r[0])`"><span class=`"dot`"></span><b>$(Esc $r[1])</b><span>$(Esc $r[2])</span></li>"
}
$allOk = -not ($rows | Where-Object { $_[0] -eq 'bad' })

# ---- 常用调整：直达后台。后台前端的网址是 /#/ 开头的（AstrBot 4.29 实测），哪页都能直接打开；
# /#/extension?open_config=插件名 打开就弹出那个插件的设置；插件自带的管理页是 /#/plugin-view/插件名/页名。
$links = @(
    @('她的性格、说话方式', '/#/persona', '点「大肥鱼」那张卡片改'),
    @('表情包', '/#/plugin-view/meme_manager/app', '看她有哪些表情包、上传新的'),
    @('她记住了什么', '/#/plugin-view/astrbot_plugin_livingmemory/dashboard', ''),
    @('群里插不插话、插多少', '/#/extension?open_config=astrbot_plugin_group_chat_plus', '插得越多越容易被风控，别调太高'),
    @('多久主动找你一次', '/#/extension?open_config=astrbot_plugin_proactive_chat', ''),
    @('发说说', '/#/plugin-view/astrbot_plugin_qzone/dashboard', '')
)
if ($video) { $links += , @('看视频（截几张、看多长）', '/#/extension?open_config=astrbot_plugin_dafeiyu_video', '') }
$links += @(
    @('换模型、语音', '/#/providers', ''),
    @('她跟谁聊过什么', '/#/conversation', ''),
    @('出问题了看日志', '/#/console', '复制最后几行发给 Agent 或在群里问')
)
$cards = foreach ($l in $links) {
    $note = if ($l[2]) { "<small>$(Esc $l[2])</small>" } else { '' }
    "<a class=`"card`" href=`"$(Esc ($base + $l[1]))`" target=`"_blank`"><b>$(Esc $l[0])</b>$note</a>"
}

# ---- 复杂的事交给 Agent：复制一句话，粘到 DeepSeek Harness（或 WorkBuddy）里
$asks = @(
    @('换成我自己的角色', '用大肥鱼的安装 Skill，帮我把大肥鱼换成我自己的角色：名字叫……，性格是……，说话风格像……'),
    @('换个声音', '用大肥鱼的安装 Skill，帮我给大肥鱼换个声音，先出几个让我挑'),
    @('让她进我的 QQ 群', '用大肥鱼的安装 Skill，我想让大肥鱼也在我的 QQ 群里聊，群号是……'),
    @('她不回话了', '大肥鱼不回话了，用大肥鱼的安装 Skill 帮我查查是哪出了问题，先只看不改'),
    @('电脑关机她也在线', '用大肥鱼的安装 Skill，帮我看看把大肥鱼搬到云服务器上要花多少钱、怎么弄')
)
$copy = foreach ($a in $asks) {
    "<div class=`"ask`"><b>$(Esc $a[0])</b><p>$(Esc $a[1])</p><button type=`"button`">复制</button></div>"
}

$voiceCost = switch ($voice) { '3' { '豆包语音按时长算，大概一分钟一块钱。' } '4' { 'Breeze 语音是你显卡算的，不花钱。' } default { '' } }
$html = @"
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>大肥鱼工作台</title>
<style>
:root{--bg:#f6f7fb;--card:#fff;--ink:#1f2330;--mute:#6b7080;--line:#e3e6ee;--ok:#22a45d;--bad:#e5484d;--off:#b9bdc8;--accent:#3c7be0}
@media (prefers-color-scheme:dark){:root{--bg:#15171d;--card:#1e2129;--ink:#e8eaf0;--mute:#9aa0ae;--line:#2c303a;--accent:#6aa0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 "Microsoft YaHei UI","PingFang SC",system-ui,sans-serif}
main{max-width:880px;margin:0 auto;padding:28px 16px 48px}h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 12px}
.sub{color:var(--mute);margin:0}.box{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:6px 18px}
ul.lights{list-style:none;margin:0;padding:0}ul.lights li{display:grid;grid-template-columns:14px 9em 1fr;gap:10px;align-items:baseline;padding:10px 0;border-bottom:1px solid var(--line)}
ul.lights li:last-child{border:0}.dot{width:10px;height:10px;border-radius:50%;display:inline-block;background:var(--off)}
li.ok .dot{background:var(--ok)}li.bad .dot{background:var(--bad)}li.bad span:last-child{color:var(--bad)}li.off span:last-child{color:var(--mute)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.card,.ask{display:block;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;color:inherit;text-decoration:none}
a.card:hover{border-color:var(--accent)}.card small{display:block;color:var(--mute);margin-top:2px}
.ask p{margin:4px 0 8px;color:var(--mute);font-size:14px}.ask button{border:1px solid var(--accent);background:none;color:var(--accent);border-radius:8px;padding:3px 14px;cursor:pointer;font:inherit}
.tips p{margin:6px 0}.tips code{background:var(--line);padding:1px 6px;border-radius:5px;font-size:13px;word-break:break-all}
.banner{margin:16px 0 0;padding:10px 14px;border-radius:10px;background:$(if ($allOk) { 'rgba(34,164,93,.12)' } else { 'rgba(229,72,77,.12)' })}
@media (max-width:560px){ul.lights li{grid-template-columns:14px 1fr}ul.lights li span:last-child{grid-column:2}}
</style></head><body><main>
<h1>大肥鱼工作台</h1>
<p class="sub">$(Get-Date -Format 'M月d日 HH:mm') 查的。想再查一次，重新双击桌面「大肥鱼工作台」。</p>
<p class="banner">$(if ($allOk) { '都好着，去 QQ 上找她聊吧。' } else { '有红灯，照红字做；做完重新双击工作台看看。' })</p>

<h2>她现在怎么样</h2>
<div class="box"><ul class="lights">
$($lights -join "`n")
</ul></div>

<h2>常用调整</h2>
<p class="sub">点了打开 AstrBot 后台。第一次会让你先登录，账号密码在 <code>$(Esc $bk)</code>；登完没跳到对应地方，回这页再点一次。</p>
<div class="grid" style="margin-top:10px">
$($cards -join "`n")
</div>

<h2>复杂的事交给 Agent</h2>
<p class="sub">复制一句，粘到 DeepSeek Harness 里发出去（省略号换成你的内容）。</p>
<div class="grid" style="margin-top:10px">
$($copy -join "`n")
</div>

<h2>怎么用</h2>
<div class="box tips">
<p><b>跟她聊：</b>用你大号私聊她就行$(if ($group) { '，或者在群里 @ 她' })，别的人她不理。想让她忘掉之前聊的，私聊发 <code>/reset</code>。</p>
<p><b>开机：</b>她会自己起来，等一两分钟就在线。任务栏里那几个最小化的黑窗口别关，关了她就下线了。</p>
<p><b>掉线：</b>QQ 风控挺严，小号偶尔被踢下线很正常，不是坏了。双击桌面「$reName」就行。</p>
<p><b>花钱：</b>聊天走 DeepSeek，按用量扣，私聊很省，群聊和发图费一点。<a href="https://platform.deepseek.com/usage" target="_blank">DeepSeek 用量页</a>能看每天花了多少。$voiceCost</p>
<p><b>东西都在哪：</b>全在 <code>$(Esc $Dir)</code> 下面，重装系统前把这个文件夹留着。</p>
</div>
</main>
<script>
document.querySelectorAll('.ask button').forEach(function (b) {
  b.addEventListener('click', function () {
    var t = b.previousElementSibling.textContent;
    function done() { b.textContent = '已复制'; setTimeout(function () { b.textContent = '复制'; }, 1500); }
    if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(t).then(done, fallback); } else { fallback(); }
    function fallback() { var a = document.createElement('textarea'); a.value = t; document.body.appendChild(a); a.select(); try { document.execCommand('copy'); done(); } catch (e) {} a.remove(); }
  });
});
</script>
</body></html>
"@
$out = "$Dir\大肥鱼工作台.html"
[IO.File]::WriteAllText($out, $html, (New-Object Text.UTF8Encoding($false)))
Write-Output "工作台：$out（$(@($rows | Where-Object { $_[0] -eq 'bad' }).Count) 个红灯）"
if (-not $NoOpen) { Start-Process $out }
