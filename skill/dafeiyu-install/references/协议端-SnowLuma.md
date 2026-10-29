# 协议端 B：SnowLuma（Agent 全装）

10-28 在干净账户上用脚本从下载装到连上 AstrBot（官方 v2.2.1，桌面 QQ 9.9.36-53644）。仓库 https://github.com/SnowLuma/SnowLuma ，文档 https://snowluma.github.io 。

## 它是什么、跟 NapCat 有什么不同

NapCat 自带一份单独的 QQ；SnowLuma 不带 QQ，它把自己「挂」进这台电脑上正常打开的桌面 QQ（官方 NT 版），对外提供跟 NapCat 一样的 OneBot 接口，AstrBot 那一侧完全一样。登录只能在桌面 QQ 的窗口里做（扫码或点「登录」）。

讲给观众听的话（问 ③ 第 1 项、他问「SnowLuma 是啥」时用；只转述作者和官方的话，不替它保证稳不稳）：

> SnowLuma 是 NapCat 那拨人做的新程序，NapCat 官方说它「更新更好用」，在掉线、风控的问题帖里也直接指向它。它挂在你电脑上正常打开的桌面 QQ 上，所以小号要登在这台电脑的桌面 QQ 里。QQ 自动升级以后，可能要等 SnowLuma 出新版才能接着用，工作台会提醒你。它还在早期版本，更新很勤。作者只许个人免费用。好不好用、稳不稳是作者说的，我们没法替它保证。

## 用户协议：先讲、他同意了再继续

SnowLuma 发行包里有 `EULA.md`（用户协议）和 `PRIVACY.md`（隐私政策）。它的网页后台第一次打开要先同意这两份才解锁；也可以在启动时用 `SNOWLUMA_ACCEPT_EULA=1`、`SNOWLUMA_ACCEPT_PRIVACY=1` 表示「运营者已阅读并同意」（官方发布说明里写的无人值守做法）。这里的运营者是观众本人，所以：

1. 装之前跟他说：「SnowLuma 有一份用户协议和一份隐私政策，要点是：只许个人非商业使用；不许改它、不许把它单独转发或打包给别人；它会处理你小号的聊天数据（都在你自己电脑上）。全文我装好后用记事本给你打开。你同意的话回『同意』，我就替你在它那边点同意；不同意就换 NapCat。」
2. 他回「同意」：装的时候加 `-AcceptAgreements`。装好后用 `notepad <工作目录>\SnowLuma\EULA.md` 和 `PRIVACY.md` 打开全文给他留着。
3. 他想自己看完再说：不加这个参数照样装，它登 QQ、收发消息不受影响（10-28 实测：没同意时只锁网页后台）。以后他自己打开 `http://127.0.0.1:5099` 点同意。

**他没亲口说同意，不加 `-AcceptAgreements`。**

## 4.1 装之前看体检

- `desktopQQ.installed` 为假：先跑 `install-qq.ps1 -Dir <工作目录>`（从 QQ 官网下官方安装包装上，见下面「没装桌面 QQ」）。
- `desktopQQ.running` 为真：这个 Windows 用户已经开着桌面 QQ，多半登着大号。SnowLuma 会挂上这个用户打开的每一个 QQ（大号也会被挂上，但我们只让小号连机器人，见 4.3）。跟他说清楚：「小号要在桌面 QQ 里另登一个，两个号同时开着；不想这样就换 NapCat」。
- 本 Skill 装的 NapCat 在跑、登着同一个小号：脚本加 `-StopNapCat` 先停掉它（同一个号不能同时接两个协议端，会互相挤下线）。
- 6199（AstrBot）要在听；5099（SnowLuma 后台）不能被占。

## 4.2 装：`install-snowluma.ps1`

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<S>\install-snowluma.ps1" -Dir <工作目录> [-AcceptAgreements] [-StopNapCat]
```

它做的事（都不改 SnowLuma 包里的文件）：

1. 查权限级别：在 Agent 沙盒里（低完整性）直接停，让他切完全访问。这就是 issue #563「一直注入失败」的原因。
2. 找桌面 QQ（注册表和默认位置），报版本。
3. 从 GitHub 下官方完整包 `SnowLuma-v2.2.1-win-x64.zip`（43 MB，自带运行环境；国内走加速地址），对官方公布的 SHA-256，原样解压到 `<工作目录>\SnowLuma`。已经是这个版本就跳过。
4. 写配置：`config\onebot.json`（全局）什么都不连；`config\runtime.json` 后台只听 127.0.0.1:5099、自动挂 QQ。
5. 后台密码：随机生成（它要求 10 位以上、有大小写和符号），存 `private\snowluma-webui.txt`，第一次启动时交给它（`SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD`）。不用去黑窗口里抄临时密码。
6. 写 `<工作目录>\start-snowluma.cmd`，用计划任务在他的桌面会话里、普通权限启动（跟双击打开的 QQ 同一个用户、同一个级别；日志第二行会写 `Node integrity: ... Medium`）。日志在 `SnowLuma\snowluma-run.log`，它自己的日志在 `SnowLuma\logs\`。
7. 这个用户没开桌面 QQ 就替他打开（同样走计划任务、普通权限）。

**成功**：最后一行叫他登小号。日志里有 `[Hook] pipe connected: PID=...`（已经挂上 QQ）。

**退出码 8**：解压失败、解出来的文件比压缩包里少，或者程序文件不见了。SnowLuma 要往 QQ 里挂东西，杀毒软件最容易盯上它。脚本会点名这台装了哪家杀毒；照 `杀毒软件.md` 把工作目录加进那家的信任区，再重跑本脚本（不用关杀毒，被拿走的文件重跑会重新解压，不用去隔离区恢复）。

然后停下来叫人：

- QQ 窗口里显示小号的头像和「登录」按钮（以前在这台电脑登过）：「点一下『登录』」。
- 显示二维码：「用登着小号的手机 QQ 扫这个码，再在手机上点『登录』」。
- 显示的是大号：「点头像旁边的小箭头，选『添加账号』，用小号的手机 QQ 扫码」。（多个号同时登的做法，待实测）

## 4.3 连：`connect-snowluma.ps1`

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<S>\connect-snowluma.ps1" -Dir <工作目录>
```

它等 SnowLuma 认出登录的号（`config\onebot_<号>.json` 出现），**排除大号**（`private\owner-qq.txt`，所以第 5 步先要大号再跑它），认出小号后存进 `private\bot-qq.txt`，只给小号那份配置加一个反向 WS 连 `ws://127.0.0.1:6199/ws`（token 跟 AstrBot 一样，读 `private\napcat-ws-token.txt`），然后重启 SnowLuma（不动 QQ），等 6199 上有连接。

**成功**：「SnowLuma 已经连上 AstrBot」；AstrBot 日志有「aiocqhttp(OneBot v11) 适配器已连接」。然后回 SKILL.md 第 6 步。

**失败先查**：
- 脚本退出码 7（「QQ 又是在它之前开的」）：小号在 SnowLuma 开起来之前就登上了，SnowLuma 看不到这次登录。让他在 QQ 里退出登录（或托盘右键退出 QQ 再打开），重新登一次小号，再跑一遍脚本（10-29 Z1 实测：DSH 自己排查了约 4 分钟才发现是这个）。
- 等不到号：日志里没有 `login detected`。QQ 窗口里真登上了没有；QQ 和 SnowLuma 是不是一个用管理员身份开、一个没有（要一样，脚本起的两个都是普通权限，他自己右键「以管理员身份运行」过 QQ 的会对不上）；QQ 是不是刚自动升级（见下面）。
- 登着不止一个号、又没有 `owner-qq.txt`：脚本会停下，让他把小号的号码粘进 `private\bot-qq.txt` 再跑。
- 6199 连不上：AstrBot 开着没、平台「大肥鱼」建了没（`astr_setup.py bot`）、两边 token 一致没。
- 日志里 `Client private-message reporting is unavailable`：只影响「在 QQ 窗口里手动发的消息要不要上报」，不影响她收发消息，不用管。
- 日志里读别的 Windows 用户目录被拒（`account watch failed ... EPERM`）：这台电脑上 QQ 的「聊天记录保存位置」被设成了别的账户的文件夹（全机共用的设置），不影响登录和收发（10-28 实测），不用管。
- 登录时手机 QQ 弹安全风险：同 NapCat，只能他在手机上恢复小号。

## QQ 自动升级

SnowLuma 的挂钩是跟 QQ 版本对齐的，官方文档写着「QQ 自动升到 SnowLuma 还不支持的新版本时可能挂不上」。表现：QQ 能登，但 `connect-snowluma.ps1` 等不到号，或者日志里 `auto-load retry exhausted`。办法：

1. 先升级 SnowLuma：`install-snowluma.ps1 -Dir <工作目录> -Version <最新版>`（只换程序，`config\` 里的登录信息、连接设置、后台密码都留着）。最新版看 https://github.com/SnowLuma/SnowLuma/releases 。
2. 最新版也挂不上：先换回 NapCat（让他在桌面 QQ 里退出小号，再跑 `install-napcat.ps1` + `connect-napcat.ps1`），等 SnowLuma 跟上再换回来。

不替他去关 QQ 的自动更新，也不改 hosts 挡 QQ 的更新地址（那是改网络设置）。

## 没装桌面 QQ

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<S>\install-qq.ps1" -Dir <工作目录>
```

它做的事：读 QQ 官网的下载配置拿最新版地址 → 跟官网下载按钮一样先向官网要签名地址（没签名的地址直接下会 403，10-28 实测）→ 下官方安装包（约 330 MB，腾讯自己的服务器）→ 确认是腾讯签名、签名有效 → 静默安装（`/S`）。工作目录不在系统盘时装到那个盘的 `\Tencent\QQNT`（`/D=`），在系统盘就用 QQ 默认位置。

停下来叫人：安装时 Windows 会弹「你要允许此应用对你的设备进行更改吗」，叫他点「是」（QQ 是装给整台电脑的）。他点了「否」脚本退出码 5，问他要不要再来一次。

10-28 在 ceshi 上实测了「拿地址、要签名、下载、验签名」（`-DownloadOnly`）；**静默安装和 `/D=` 换位置还没在干净电脑上跑过**（测试机上的桌面 QQ 是别的账户装给整台电脑的，不能动）。装完脚本会再找一遍 QQ，找不到就退出码 6，让他看开始菜单，实在不行请他自己去 im.qq.com 下载安装。

## 开机自启和掉线

`connect-snowluma.ps1` 连上以后在 `private\protocol.txt` 写 `snowluma`。`finish.ps1` 看到它就把 SnowLuma 加进开机自启（同时去掉 NapCat 的，两个都开会抢同一个小号），桌面放「大肥鱼掉线-双击重登」。桌面 QQ 自己要设成开机启动、自动登录小号：QQ 登录窗口勾「自动登录」，QQ 设置里打开开机自启（让他自己点，你告诉他在哪）。重启电脑后能不能自己登上，待实测。

「大肥鱼掉线-双击重登」（`tools\relogin-qq.ps1` 的 SnowLuma 分支，10-28 在 ceshi 上测过「在线时直接退出」）：AstrBot 没开先开；SnowLuma 没开先开；这个用户的桌面 QQ 没开就打开；等 30 秒没连上就叫他在 QQ 窗口里登小号；一分钟后还没连上会重启一次 SnowLuma（它会重新挂到开着的 QQ 上，不动 QQ）；10 分钟还不行，看日志像是 QQ 升级了就提示看工作台。

## 配置包能不能全用上

10-27 读源码：配置包要用的 `set_self_longnick`（改签名）、`get_cookies`（QQ 空间）、戳一戳、`get_record`（语音）、`upload_private_file`、`send_like`、`set_qq_profile` 它都有实现；v2.2.1 还加了 QQ 空间的读写。真能不能用，待实测。验收时把签名、说说、语音各试一次。
