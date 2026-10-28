# 基础：工作目录、AstrBot、DeepSeek、白名单、第一次对话

主路线是**命令行装 AstrBot，不用启动器**；安装用时看网速。观众电脑上已经有启动器装的 AstrBot（看过 Vol.04 视频的大多是这种），走本文末尾「已经用启动器装过」。

下面的命令里，`<S>` 是本 Skill 的 `scripts` 文件夹，`<工作目录>` 是第 1 步问 ② 里他挑的文件夹（比如 `D:\dafeiyu`），`<py>` 是 `<工作目录>\uv\tools\astrbot\Scripts\python.exe`（装完 AstrBot 才有）。PowerShell 脚本都这样跑：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<S>\install-astrbot.ps1" -Dir <工作目录>
```

脚本都能重复跑，做过的步骤会跳过。脚本输出里不会有 key、密码、QQ 号；它报错了先看最后几行，再查 `坑.md`。

## 2.1 工作目录和秘密文件

```
<工作目录>\            他挑的文件夹，比如 D:\dafeiyu（最好全英文、不带空格）
  private\              只放秘密，你只读不打印
    deepseek-key.txt    一行，sk- 开头                    ← 观众用记事本粘
    owner-qq.txt        一行，他的大号                    ← 观众用记事本粘（QQ 号也算隐私，不在对话里说）
    group.txt           群号（可选，几个群就一行一个）    ← 观众用记事本粘
    backend.txt         三行 url: / username: / password: ← install-astrbot.ps1 生成
    napcat-ws-token.txt 一行，随机串                      ← install-astrbot.ps1 生成
    bot-qq.txt          小号                              ← connect-napcat.ps1 写
    volc.txt            两行 app id: / access token:      ← 选豆包时观众粘
    sf-key.txt          一行                              ← 选硅基流动时观众粘
  uv\                   uv、Python、AstrBot 程序（都在这，不进用户目录）
  astrbot\              AstrBot 的数据（data\cmd_config.json、插件、聊天记录）
  NapCat\               NapCat 和它自带的 QQ
  downloads\            安装包
  start-astrbot.cmd / start-napcat.cmd   启动脚本（脚本生成）
```

- 观众已经照 Vol.04 视频建过 `D:\大肥鱼\deepseek-key.txt`、`大号QQ.txt` 的：把内容复制进 `private\` 对应文件（用 `Copy-Item`，别读出来再写），不用他再粘。
- 要他粘的：先建空文件，`notepad <文件>` 打开，跟他说「把 key 粘进去，Ctrl+S 保存，关掉，回我一句好了」。然后你核：文件存在、一行、长度合理、key 以 `sk-` 开头。只报这几样。

**失败先查**：记事本存成了「另存为」别的名字；粘的时候带了空格或换行（脚本读的时候会去掉首尾空白）；key 是网页上已经打码的那串（DeepSeek 只在新建时显示一次完整 key，看不到就让他新建一把）。

## 2.2 装 AstrBot（命令行）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<S>\install-astrbot.ps1" -Dir <工作目录>
```

它做的事：
1. 先从阿里云 PyPI 下 uv（Python 的安装工具）的 Windows wheel（约 15 MB），核对索引里的 SHA256，解出 uv、uvx 放进 `<工作目录>\uv\bin`。失败再下 GitHub zip，按 gh.dpik.top → edgeone.gh-proxy.com → hk.gh-proxy.org → gh-proxy.org → GitHub 依次试。**不用** `irm https://astral.sh/uv/install.ps1 | iex`，它在一些环境里会静默退出。
2. 先用 `uv python install 3.12 --no-bin` 单独装受管 Python（约 21 MB），按 npmmirror → 南大镜像 → 上面的 GitHub 加速列表依次试，不在用户 bin 目录生成入口。再用 `uv tool install astrbot --python 3.12` 装 AstrBot，包源按阿里云 → 清华 → 腾讯 → 华为 → PyPI 官方依次试。观众电脑上没有 Python 也行，有也不用它。uv、Python、AstrBot 全放工作目录里，用户名带中文也不受影响。
3. `astrbot init -y`，然后把 `data\cmd_config.json` 的 `dashboard.host` 改成 `127.0.0.1`（默认 `0.0.0.0`，局域网里别人也能打开后台，还会弹防火墙框）。
4. 随机生成后台密码（**必须带大写字母**，只有小写加数字会被拒），写进 `private\backend.txt`，用 `astrbot conf set dashboard.username / dashboard.password` 在第一次正式启动前设好。这样不用去日志里找初始密码（初始密码只在第一次启动时打印一次）。
5. 写 `start-astrbot.cmd`（设 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，不然日志里的表情符号会让它在中文 Windows 上报 GBK 编码错误），在观众的桌面会话里启动，等 6185 端口能打开。

**成功**：最后一行是「AstrBot 后台起来了：http://127.0.0.1:6185」。用时看观众的网速和依赖安装进度。
**失败先查**：
- 「下载失败」：先看卡在 uv、Python 还是 AstrBot 依赖。uv 会先试阿里云，失败才走 GitHub 加速列表；国内源和备用地址都失败，再查网络环境。公共下载器会保留未下完的文件，重跑可续传；校验失败或地址不支持续传时会重下。
- 「AstrBot 没装上」：看最后几行。Python 没下好就查 npmmirror、南大和 GitHub 备用地址；Python 已装好、卡在某个依赖包，脚本会依次试五个 PyPI 源，全部失败再查网络。
- 「150 秒没起来」：脚本会打出 `astrbot\run.log` 最后 15 行。端口 6185 被占了就加 `-Port 6186` 重跑（之后 `backend.txt` 的 url 也是新端口）。

## 3 接 DeepSeek

```powershell
& "<py>" -X utf8 "<S>\astr_setup.py" --dir <工作目录> deepseek
```

它先查 key 能不能用（`api.deepseek.com/user/balance`，只报「可用」和「余额是否大于 0」），然后建 DeepSeek 提供商（`api_base` `https://api.deepseek.com/v1`）和 `deepseek-flash` 模型（输入支持文字和图片），测试一次，设成默认聊天模型和看图模型，关掉「操作电脑」（`computer_use_runtime = none`）。

**成功**：「模型测试：通过」和「默认模型、看图模型都设成 deepseek/deepseek-flash」。

**紧接着建机器人平台**（别等到第 5 步，装 NapCat 之前就要有，不然 NapCat 登上了也没处连）：

```powershell
& "<py>" -X utf8 "<S>\astr_setup.py" --dir <工作目录> bot
```

`bot` 建 aiocqhttp 平台「大肥鱼」，反向 WS 听 `127.0.0.1:6199`，token 用 `private\napcat-ws-token.txt`（NapCat 那边 connect-napcat.ps1 会填同一个）。**成功**：「机器人平台「大肥鱼」（等 NapCat 连 127.0.0.1:6199）： 好了」。
**失败先查**：「key 不对」让他重新建一把粘进去；「余额不够」让他去 DeepSeek 开放平台充值（停下来叫人，你不替他付钱）；「后台登录失败」见坑 6（他在网页上改过密码）。

## 5 白名单、管理员

```powershell
& "<py>" -X utf8 "<S>\astr_setup.py" --dir <工作目录> whitelist
```

（平台「大肥鱼」第 3 步已经建了；漏了的话 `connect-napcat.ps1` 会自己补。）

- `whitelist`：白名单只放大号（`大肥鱼:FriendMessage:<大号>`），有 `group.txt` 再把里面的群都加上（一行一个，空格、逗号隔开也认）；大号设成管理员。要先有 `owner-qq.txt`，没有就先让他粘。
- `group-reply [mention|quote|both|off]`：她在群里回谁时怎么标出来。`mention` 先 @ 那个人，`quote` 引用对方那句，`both` 都要，`off` 都不要；只管群聊，私聊不 @；她回的是语音时 QQ 带不了 @ 和引用。AstrBot 默认都不要，导入配置包也不动它。不带参数只看现在是哪种。工作台「群里插话」下面也能改。
- 随时可以 `astr_setup.py --dir <工作目录> status` 看摘要。

**成功**：`status` 显示默认模型、看图模型是 deepseek-flash，操作电脑 `none`，白名单开着，平台「大肥鱼」开着。

## 6 第一次对话

让他用大号私聊小号「在吗」。你看日志：`& "<py>" -X utf8 "<S>\astr_setup.py" --dir <工作目录> logs 40`（QQ 号会打码）。

**成功**：他收到回复。
**失败先查**（按顺序）：6199 有没有连接（`Get-NetTCPConnection -LocalPort 6199 -State Established`，没有就是 NapCat 没连上，回协议端文件）→ 日志里有没有收到消息（没收到是协议端问题，收到了没回是白名单或模型问题）→ 白名单 id 前缀是不是平台 id「大肥鱼」→ `deepseek` 子命令重跑一次看模型测试。

## 已经用启动器装过（Vol.04 视频的装法）

体检里 AstrBot 实例是 `launcher` 的，就不另装命令行版，在他的实例上接着做。它的默认人格不是大肥鱼的（体检 `origin` 是 `own` 或 `stock`），先照 SKILL.md 第 1 步 B 问他整个变成大肥鱼、只拿几样还是另装一套，别直接跑 core；选「只拿几样」的，下面只做建工作目录、写 `backend.txt` 这一条，`deepseek`、`bot`、`whitelist` 都不跑：

- 实例在 `<启动器数据目录>\instances\<uuid>\`，数据目录默认 `%USERPROFILE%\.astrbot_launcher`，改过的话记在 `%APPDATA%\com.github.raven95676.astrbot-launcher\.data-dir.json`。配置是 `core\data\cmd_config.json`。
- 后台端口不一定是 6185，启动器会另给（见过 18607、21233）：看实例目录下的 python 进程在听哪个端口。没在跑就让他在启动器里点启动。
- `astr_setup.py` 只认 `<工作目录>\private\backend.txt`：建好工作目录，把 `url: http://127.0.0.1:<端口>`、他的后台账号、密码写进去（让他用记事本粘密码）。他忘了密码：让他在启动器里停实例，你用实例自带的 python 跑 `astrbot conf set dashboard.password`（在实例的 `core` 目录下；待实测），或者让他在启动器里重置。
- 然后 `deepseek`、`bot`、`whitelist` 照常跑（`<py>` 换成实例的 `venv\Scripts\python.exe`，脚本本身只用标准库）。
- 后台只听本机：让他在启动器里停实例，你把 `cmd_config.json` 的 `dashboard.host` 改成 `127.0.0.1`（`astr_setup.py dashboard-local` 只认工作目录里的 AstrBot，启动器版手动改），再启动。
- 部署时报 403、Python 组件下不动：飞书 11.3 的解法（开关「中国大陆一键加速」、清空虚拟环境、重试）。都不行就跟他商量改走命令行版，旧实例只停不删。
- 开机自启：启动器装的大脑不归 `finish.ps1` 管，要他自己在启动器最左边「高级」→「通用」里打开「开机自启动」「自启动后最小化到托盘」「退出时保留实例运行状态」，「关闭窗口时」保持「最小化到系统托盘」（启动器一退出，实例跟着停）。第 8 步 `finish.ps1` 会查、会打出这段，细节在 `收尾-自启和掉线.md`。10-28 群友那台就是漏了这步，重启后她不回来。
