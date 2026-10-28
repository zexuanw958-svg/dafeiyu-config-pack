# 协议端 A：NapCat（默认）

NapCat 是帮机器人登 QQ 的程序。Windows 一键包自带一份 QQ，跟观众电脑上平时登大号的桌面 QQ 互不干扰。实测：NapCat 4.18.41 一键包，QQ 内核 9.9.33-52230（10-24 录屏、10-26、10-27 两单、10-27 干净账户命令行装）。

许可：NapCat 的 LICENSE 第 3 条不许商用。帮观众自己装、自己用没问题。

`<S>`、`<工作目录>`、`<py>` 的意思同《基础》那份。

## 4.1 装 NapCat、出二维码

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<S>\install-napcat.ps1" -Dir <工作目录>
```

它做的事：
1. 下 v4.18.41 的 `NapCat.Shell.Windows.OneKey.zip`（约 1 MB，走 GitHub 加速轮换），解压到 `<工作目录>\NapCat`。
2. 后台运行 `NapCatInstaller.exe`，往它的标准输入喂几个回车（它最后会停在「按任意键」）。它自己下 QQ（约 200 MB，先腾讯官方 `qqdl.gtimg.cn`，不行换 GitHub 上第三方仓库）和 NapCat 本体。实测 187–238 秒。**用 curl 下载、命令行运行的安装器不弹 SmartScreen**（文件没有网络下载标记），观众少点一处。杀毒软件会不会拦，没测。
3. 写 `start-napcat.cmd`，在观众的桌面会话里启动 `NapCatWinBootMain.exe`，等 `versions\<版本>\resources\app\napcat\cache\qrcode.png` 出现（实测 3 秒）。
4. 生成 `<工作目录>\扫码登录.html`（每 3 秒自动刷新二维码图片），用默认浏览器打开。

然后停下来叫人（先问手机，再说扫码；大多数人手机 QQ 上只登着大号，一扫就把大号登成了机器人）：

> 「手机在手边吗？先在手机 QQ 上切到小号：点左上角头像 → 设置 → 账号管理 → 切换到小号（手机上没登过小号的，点『添加账号』先登上）。切好了，用它扫这个码，再在手机上点『登录』，扫完回我一句。」

手机不在身边：跟他说「没关系，手机拿来了跟我说一声，我接着等，不用重装」。二维码过期了 NapCat 会自己换新码。

页面每 3 秒刷新一次；二维码本身两分钟过期、NapCat 自动换新码（别说成「每 3 秒换新码」）。页面在 `<工作目录>\扫码登录.html`，不在桌面。浏览器没弹出来的话，你自己 `Start-Process` 再打开一次，别让他去找文件。

**成功**：最后几行是「二维码出来了，已经用浏览器打开」。
**失败先查**：
- **退出码 8**：「NapCat 没装成」并且日志里有「移动versions目录失败：拒绝访问」或「解压失败」（上一期评论区点赞最多的卡点），或者启动程序装好又不见了：多半是杀毒软件把登 QQ 的程序当成可疑文件拿走了，脚本会点名这台装了哪家。**别叫他关杀毒**，照 `杀毒软件.md` 把工作目录加进那家的信任区（只有 Windows 自带杀毒的跑 `trust-folder.ps1`），再重跑本脚本；它开头会自己清掉上次没装成的 `NapCat.<数字>.Shell`，下好的 `QQ.exe` 和 zip 会复用，被拿走的文件不用去隔离区恢复。
- 下载卡住：GitHub 慢，重跑，下好的会跳过。
- 「90 秒没出二维码」：看脚本打出的日志。终端里的字符二维码扫不出来（坑 4），只用图片。

## 4.2 等扫码、接到 AstrBot

他说扫好了（或者你先跑着等，它会一直等）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<S>\connect-napcat.ps1" -Dir <工作目录>
```

它做的事：
1. 等 `config\onebot11_<号>.json` 出现（默认最多等 300 秒，`-WaitSeconds` 可改）。**登上的号跟 `private\owner-qq.txt` 里的大号一样，就退掉、清掉这次登录，退出码 7**（见下面「失败先查」）。是小号就写进 `private\bot-qq.txt`，**再等十几秒**。扫码后立刻重启 NapCat 会把刚完成的登录掐掉，之后快速登录报「登录态失效」又要重扫（10-27 实测踩过）。
2. 用 `napcat_cfg.py` 改 NapCat 配置：`onebot11_<小号>.json` 的 `network.websocketClients` 只留一条连 `ws://127.0.0.1:6199/ws`，token 和 AstrBot 平台一致；`webui.json` 的 `host` 改 `127.0.0.1`（默认 `::`，局域网都能打开）、`token` 换随机串存进 `private\napcat-webui-token.txt`、`autoLoginAccount` 填小号。
3. 改启动脚本为快速登录（`NapCatWinBootMain.exe <小号>`），重启 NapCat，等 6199 上出现连接（最多 90 秒）。

**成功**：「NapCat 已经连上 AstrBot（6199 有连接）」。接着去《基础》第 6 节让他用大号私聊小号。
**失败先查**：
- 退出码 7「刚才是用大号扫的码」：照脚本最后一行说的，让他在手机 QQ 上切到小号，回「好了」后重跑 `install-napcat.ps1` 出新码，再跑本脚本。所以第 5 步要先让他把大号粘进 `owner-qq.txt` 再跑本脚本。
- 等了 300 秒没登上（他手机不在手边、去找手机了）：不算装坏，他说「好了」再跑一次本脚本接着等；二维码过期了先重跑 `install-napcat.ps1`。
- 脚本报「登录失败（ErrCode N）」：**先问他手机 QQ 上弹了什么，原话发过来，别让他反复扫**。
  - ErrCode 3：二维码两分钟没扫、过期了（10-27 实测）。NapCat 会自己换新码，`扫码登录.html` 跟着刷新，脚本不当失败、接着等。
  - ErrCode 5：二维码被取消或失效，重跑 `install-napcat.ps1` 出新码。
  - ErrCode 10：扫码、手机上点了确认，7 秒后失败（10-27 测试机碰到，原因还没查清；同一个小号当晚再扫，报的就是下面的风控，多半是一回事）。
  - 脚本报「登录被 QQ 拒了（serverErrorCode 168）」，原话「你的账号近期存在安全风险，部分功能使用受限，请登录最新手机QQ并根据提示恢复账号使用」（10-27 测试机实测，扫码确认后 7 秒）：这是 QQ 风控，只能他在手机 QQ 上按提示恢复小号，恢复后重跑 `install-napcat.ps1` 出新码再扫（NapCat 报完这句不会自己出新码）。新注册、刚换设备登过的小号最容易碰到。恢复不了就换个号。
- 「快速登录没成功，NapCat 又出了二维码」：重跑 `install-napcat.ps1` 再扫一次。
- 「90 秒没连上 AstrBot」：AstrBot 开着没（6185 能不能打开）、平台建了没（`astr_setup.py bot`）、两边 token 一致没。手改过配置的，常见错是地址末尾少了 `/ws`、冒号写成中文。

## 重启 NapCat 只停它自己

要重启时只结束路径在 `<工作目录>\NapCat` 里的进程（`NapCatWinBootMain.exe` 和那里面的 `QQ.exe`），**别动他的桌面 QQ**（路径在 `Program Files` 里那个）。然后双击（或启动）`<工作目录>\start-napcat.cmd`。

## 别让二维码请求太密

不要每隔几秒开一个新的远程连接去取二维码图片。Windows 自带的 OpenSSH 9.5 有 MaxStartups 限制，连接太多会把人挡在门外（10-27 我们自己远程测试时碰到过）。观众本机跑不受这个影响；`扫码登录.html` 是浏览器自己刷新本地图片，也不受影响。

## NapCat 后台在哪（观众常问）

`http://127.0.0.1:6099/webui`，token 在 `private\napcat-webui-token.txt`。观众问「找不到后台」时，帮他用 Edge 打开 `http://127.0.0.1:6099/webui?token=<token>`（token 别念出来，也别出现在对话里）。
