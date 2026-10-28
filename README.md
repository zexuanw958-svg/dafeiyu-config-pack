# 大肥鱼配置包

把你已经接上 QQ 的 AstrBot 调成「大肥鱼」：她有自己的性格，会发表情包、发语音，看得懂你发的图（群里 @ 她才看），开了看视频还能看懂你发的视频，记得你说过的事，隔一阵会主动找你，心情变了会自己改 QQ 签名，主人让她发空间、改签名她也会照做。

这个包里只有我们写的设置和人格。插件和表情包都是导入时从原作者那里下载的，包里不带别人的东西。

## 从零装：交给 AI 帮手（测试版）

还没装 AstrBot、NapCat 的，可以让 AI 帮手从零装好整套，包括这个配置包：`skill/dafeiyu-install` 是写给 AI 帮手看的安装手册（Skill）。它先给电脑做个体检，问你几个问题，按你的选择装，哪一项下不动就先跳过，不耽误聊天。现在是测试版，只在 DeepSeek Harness 桌面版上从头测过。

要准备：Windows 10/11 电脑；DeepSeek Harness 桌面版；一个充了值的 DeepSeek API key；一个 QQ 小号（千万别用大号，第三方程序登 QQ 有被限制的风险）和登着小号的手机。

1. 装好 DeepSeek Harness 桌面版（WorkBuddy 也行，还没从头测过）。
2. 新开一个对话，把下面这句整句粘进去发出：
   > 帮我装一个叫 dafeiyu-install 的技能，装好直接用它帮我装大肥鱼：从 https://gh.dpik.top/https://github.com/zexuanw958-svg/dafeiyu-config-pack/releases/latest/download/dafeiyu-install.zip 下载压缩包（下不动就去掉开头的 https://gh.dpik.top/ 再试），解压后把里面的 dafeiyu-install 文件夹放进你读技能的文件夹（DeepSeek Harness 是 %USERPROFILE%\.agents\skills\，WorkBuddy 是 %USERPROFILE%\.workbuddy\skills\，没有就新建；里面已经有 dafeiyu-install 的，先把旧的挪到别处）。放好后读一遍它的 SKILL.md，照着开始。
3. 它会自己下载、装好 Skill，接着给电脑做体检、问你几个问题。中途弹出「允许」就点「允许一次」。要你亲手做的只有几下：把 key 粘进它打开的记事本、手机 QQ 扫码登小号、Windows 弹窗时点一下、最后用大号给她发第一句话。

装完桌面上有「大肥鱼工作台」：看她在不在线、一键修、补装功能、备份，打游戏前点「打游戏模式」腾出显卡。Skill 出了新版，工作台「更新」页会提醒，点「一键更新」就换上。卡住了就在工作台点「复制求助信息」，粘给 AI 帮手或者发给我们。

## 用之前（已经装好 AstrBot 的）

先按视频把这几步做完：装好 AstrBot 和 NapCat，QQ 小号扫码登上，接好 DeepSeek，在 QQ 上发消息她能回。

## 导入

1. 打开 AstrBot（后台网页能打开就行）。
2. 双击 `导入大肥鱼.bat`。
3. 按提示回答几个问题：
   - 后台地址：一般会自动填好，直接回车。
   - 后台账号和密码：就是你登录 AstrBot 网页用的那个。
   - 你的 QQ 号：填你自己的大号。她只把这个号当主人。
   - 记忆：选 1 用本机 Ollama（免费，要先装 Ollama），选 2 用硅基流动（要一个硅基流动的 key），选 0 先不装。
   - 语音：直接回车就是 3，用豆包 Seed Audio（要火山引擎豆包语音的 APP ID 和 Access Token，按语音时长约 1 元/分钟，整条回复合成一条语音，要等 15–35 秒）；选 4 用本机的 Breeze（免费，声音想要什么样就做什么样，要先装好，见下面「本机语音 Breeze」）；选 1 用微软 Edge 的免费女声，选 2 用小米 MiMo 克隆音（要 MiMo 的 key），选 0 先不开。
4. 等它跑完，最后会列出每一项有没有成功。第一次要下载插件和几百张表情包，几分钟。
5. 去 QQ 上给她发一句「在吗」。之前跟她聊过的话，先发 `/reset` 清掉旧对话。

哪一项打叉了，按提示处理完再双击一次就行，装好的会自动跳过。表情包是从 GitHub 下的，下不动时工具会自己换几个加速地址再试；都不行就过一阵再双击一次。

## 本机语音 Breeze

Breeze TTS 2 是开源的语音模型，在你自己的显卡上算，不花钱。声音可以用一句话描述出来（「十几岁的小女生，声音甜甜的、有点奶」），也可以拿一段录音克隆。想把她改成你自己的角色，声音也能跟着换。

要求：Windows，显卡显存 3 GB 以上，N 卡、A 卡、Intel 核显都行；显存 6 GB 以上用效果更好的大模型（下载 3.3 GB），不到 6 GB 用小模型（2.4 GB）。没有独立显卡也能跑，但一句话要等十几秒以上。

1. 双击 `装Breeze语音.bat`。它会看你的显卡挑模型、下载程序和模型，在桌面放一个「启动Breeze语音」，再问你要不要开机自动启动。装的路径别带中文和空格。
2. 双击 `做声音.bat`，选 1 写一句描述，它出三个候选给你听，挑一个存下来；或者选 2，拖一段 5–15 秒、只有一个人说话、没有背景音乐的录音进去，再把录音里说的原话一字不差打出来。存下来的声音叫 `dafeiyu`。
3. 双击 `导入大肥鱼.bat`，语音选 4。

用的时候 Breeze 得开着：任务栏里那个最小化的黑窗口别关，关了就双击桌面的「启动Breeze语音」。不满意声音，再双击一次 `做声音.bat` 做一个新的，会把旧的换掉，已经导入过的不用再导入。Breeze 自带一个英文网页 `http://127.0.0.1:8137`，也能做声音，存的时候名字填 `dafeiyu` 就行。

Breeze 的模型只许研究和非商业使用：自己玩没问题，用它做出来的声音别拿去商用。

## 让 AI 帮手代你导入

给 DeepSeek Harness 这类 AI 帮手看的。别让它双击 `导入大肥鱼.bat`：bat 是一问一答，最后还停在「按任意键」，AI 帮手会卡住。改用回答文件，一次跑完：

1. 写一个 `回答.json`。密码、QQ 号、key 不用抄进来，写成 `名字_file` 指向存它的文本文件。文本文件只有一行的，直接读那一行；一行一项写成 `名字: 值` 的（英文冒号），再加一个 `名字_key`，填那一行冒号前面的字，英文一律小写。

   ```json
   {
     "base_url": "http://127.0.0.1:6185",
     "username_file": "D:\\大肥鱼\\后台.txt", "username_key": "账号",
     "password_file": "D:\\大肥鱼\\后台.txt", "password_key": "密码",
     "owner_qq_file": "D:\\大肥鱼\\大号QQ.txt",
     "memory": "0",
     "voice": "3",
     "volc_app_id_file": "D:\\大肥鱼\\豆包语音.txt", "volc_app_id_key": "app id",
     "volc_access_token_file": "D:\\大肥鱼\\豆包语音.txt", "volc_access_token_key": "access token"
   }
   ```

   `base_url` 填后台网页的地址。记忆选 2 要加 `siliconflow_key_file`，语音选 2 要加 `mimo_key_file`；语音选 4 不用 key，但要先装好 Breeze、做好声音（做声音是一问一答，让人自己双击 `做声音.bat`）。重跑时每一项都按这个文件来：记忆、语音要填现在在用的那个，填 0 会把已经开着的记忆或语音关掉。
2. 在配置包文件夹里，用 AstrBot 自带的 Python 跑：`%USERPROFILE%\.astrbot_launcher\instances\<实例文件夹>\venv\Scripts\python.exe -X utf8 tools\import_dafeiyu.py --answers 回答.json`。启动器「高级」里改过数据目录的，把 `%USERPROFILE%\.astrbot_launcher` 换成那个目录。
3. 看最后的结果列表，有打叉的照那一行的提示处理，再跑一次。

## 不满意，自己改

改完再双击一次 `导入大肥鱼.bat`，新设置就生效了。在 QQ 上发 `/reset` 让她用新人格重新开始。

**性格和说话方式**：改 `persona/大肥鱼.md`。这就是一段写给她的说明，用大白话改就行。比如嫌她太毒舌，把「有点傲娇，嘴硬心软」那条改掉；想让她一次多说几句，改「一次回复通常一到三条」。`{OWNER_QQ}` 别删，导入时会换成你的 QQ 号。

**表情包发得太多或太少**：改 `settings/pack.json` 里 `meme_manager` 那段的 `generation.emotion.probability`。35 大约三条回复一张图，调大发得多，调小发得少。

**私聊发表情包、群里一张不发**：2026 年 10 月 27 日之前下载的配置包有这个问题。群里的回复是群聊插件代发的，表情包插件原来的设置只给普通聊天配图。重新下载配置包，再双击一次 `导入大肥鱼.bat` 就好。不想重新导入的话，在 AstrBot 后台打开 meme_manager 的插件配置，把「表情附加触发范围」从 `only_chat_llm` 改成 `chat_and_plugin_llm`，保存即可。

**两套表情包**：默认蓝色大肥鱼和 DeepSeek 二创两套一起用，发图时从两套里一起挑。只想用一套，把 `settings/pack.json` 里 `meme_packs` 那套的 `use` 改成 `false`，再导入一次。

**语音太多或太少**：改 `settings/pack.json` 里 `voice` 的 `trigger_probability`。0.33 是大约三条一次语音，1 是每条都发语音，0.1 是偶尔一次。

**换个声音**：Edge 的声音在 `voice.edge.voice`，比如换成 `zh-CN-XiaoxiaoNeural`。用 MiMo 克隆音的话，把一段 5–10 秒、只有一个人说话的干净录音放进 `voice` 文件夹，重新导入时选 2。用豆包的话，声音是靠一句话描述出来的，改 `voice.doubao.persona`（比如「二十出头的女生，说话直爽利落」），重新导入时选 3。用 Breeze 的话，再双击一次 `做声音.bat` 就行。

**换成你自己的角色**：她不一定非得是大肥鱼。把 `persona/大肥鱼.md` 改成你的角色（名字、性格、说话习惯、跟你的关系），用 `做声音.bat` 给她配一个合适的声音，表情包不搭的话按上面「两套表情包」关掉，再导入一次。

**看视频**（默认不装）：DeepSeek 只能看图，这个插件用 ffmpeg 从视频里均匀截几张画面（大约每 5 秒一张、最多 6 张、只看前 3 分钟）交给她看，再把视频里说的话转成文字：装了本机语音识别（sherpa-onnx + SenseVoice，免费、在你电脑上算；`install-asr.ps1 -Plus` 再加装 Fun-ASR-Nano 增强版，难懂的方言和歌词认得更准，但慢一些）就用它，没装但记忆选了硅基流动的，用同一把 key 转写，都没有就只看画面。私聊都看，群里 @ 她才看。一两分钟的视频大约一两分钱。要先装 ffmpeg 和本机语音识别（Skill 里的 `install-ffmpeg.ps1`、`install-asr.ps1`），导入时回答文件里写 `"video": "1"`、`"ffmpeg_path": "ffmpeg.exe 的完整路径"` 和 `"asr_dir": "语音识别所在文件夹"`（不写的话插件会去 ffmpeg 旁边找 `asr` 文件夹）。截的画面只给她看一次、不存进聊天记录，所以过后追问画面细节她会说没看清；想让她记住，在后台「插件 → 大肥鱼看视频 → 配置」里打开「把截的画面留在聊天记录里」（之后每轮多花一点钱）。

**唱歌**（2.0 起默认不装，效果不稳定，不算正式功能）：想试的话，导入时回答文件里写 `"sing": "1"`、语音选 3（豆包），装好后跟她说「给我唱首歌」「唱个生日歌」，她先回一句，再发一条唱歌的语音，要等十几到四十秒。用的是语音那套豆包凭证，一首两三毛。豆包唱的是她自己编的小调，不照原曲的调子。有 N 卡、想唱准原曲调子的，把 `settings/pack.json` 里 `sing.backend` 改成 `soulx`，再按 `plugins/astrbot_plugin_dafeiyu_sing/README.md` 开唱歌服务。

**签名**：跟她说「把签名改成……」「换个签名」，她就去改；被夸了、受委屈了、你过生日，她也可能自己改一条，一天最多自己改两次（次数在后台「插件 → 大肥鱼改签名 → 配置」里改）。只认你：别人叫她改，她会拒绝。不想让她自己改，在后台「插件 → 大肥鱼改签名 → 配置」里关掉「主人夸她、骂她、说生日时，提醒她顺手改签名」，再把 `persona/大肥鱼.md` 里「你的 QQ 个性签名归你自己管」那条删掉。

**不想让她主动找你**：`settings/pack.json` 里 `astrbot_plugin_proactive_chat` 那段，把 `friend_settings.enable` 改成 `false`。免打扰时间在 `quiet_hours`，`1-7` 就是凌晨 1 点到 7 点不打扰。

**群聊里插话太多**：`astrbot_plugin_group_chat_plus` 那段，`initial_probability` 是她没被叫到时插话的概率，0.02 就是很少插话。

这些也都能在 AstrBot 后台网页里改：人格在「人格设定」，插件设置在「插件」里点对应插件的「配置」。后台改过的项，再运行导入工具会被这个包里的值盖回去，所以要么只在后台改，要么只改这个包。

## 包里有什么

| 文件 | 干什么 |
|---|---|
| `导入大肥鱼.bat` | 双击运行的入口 |
| `装Breeze语音.bat`、`做声音.bat` | 装本机语音 Breeze、给她做声音（语音选 4 才用），脚本在 `tools/breeze/` |
| `persona/大肥鱼.md` | 她的人格 |
| `settings/pack.json` | 装哪些插件、每个插件怎么设置、记忆和语音用什么 |
| `tools/import_dafeiyu.py` | 导入工具本体，通过 AstrBot 后台接口改设置 |
| `plugins/` | 我们写的五个插件：豆包语音（语音选 3 时才装）、看视频、唱歌（这两个默认不装）、改签名、回复防串台，导入工具一起装 |
| `voice/` | MiMo 克隆音用的参考录音；做 Breeze 声音时的候选和试听存在 `voice/breeze/` |

会装的插件：群聊插话（group_chat_plus）、长期记忆（LivingMemory）、表情包（meme_manager）、QQ 空间（qzone）、QQ 资料（qqprofile）、主动聊天（proactive_chat）。都从 AstrBot 插件市场下载，版权归各自作者。另外五个是配置包自带的：豆包语音（seed_audio_tts）、看视频（dafeiyu_video，默认不装）、唱歌（dafeiyu_sing，默认不装）、改签名（dafeiyu_signature）、回复防串台（dafeiyu_clean，DeepSeek 偶尔会把 `<system_reminder>`、`DSML` 这类内部标签当成回复吐出来，它在发出去之前删掉，聊天记录里也一起清掉）。表情包从 meme_manager 的资源广场下载（蓝色大肥鱼表情包、DeepSeek 二创表情包），图片版权归各权利人。

## 安全

- 她只认你填的那个 QQ 号是主人。发空间、改签名、改头像只听主人的。
- 导入工具不会打开 AstrBot 的电脑操作能力。
- 你的密码和 key 只在运行时用一下，不会写进这个包里的任何文件。

## 许可

这个包里我们写的代码、人格和设置用 MIT 许可，见 `LICENSE`。导入时下载的 AstrBot 插件和表情包不在这个包里，版权和许可归各自作者。Breeze 的程序（[Breeze-TTS-2.cpp](https://github.com/HoppouAI/Breeze-TTS-2.cpp)，Apache-2.0）和模型也是安装时才下载，模型只许研究和非商业使用。
