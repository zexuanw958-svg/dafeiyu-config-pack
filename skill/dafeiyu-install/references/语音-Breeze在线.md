# 语音 D：Breeze 在线版（导入时 voice = 5）

模型在 BreezeBlue 服务器上跑，不用独立显卡、不下载语音模型。跟本机 Breeze TTS 2 是两条路：在线版按字扣积分，网页和 API 共用余额。

**10-30 Mac / AstrBot 4.29.3 实测**：机灵版 12 字符短句合成 4.22 秒，迷糊版 11 字符短句 3.84 秒，均生成可解码播放的 MP3；本轮 QQ 私聊、群聊未测。只把音频合成时间当合成时间，QQ 收到还要加模型想回复、网络和发送的时间；QQ 未测就说未测。

## 1 注册、可选兑码、建密钥（本人操作）

一次一句话带他做：

1. 打开 https://breezeblue.ai 注册或登录。
2. 愿意领赠送积分的，在积分兑换入口填 `ZEXUAN604`。兑换码在 2027-01-01 前可用，每个账号一次，到账起 30 天有效；本次活动曾兑到 2805 积分，具体到账以页面为准。跳过也能用。
3. 目前每天可领 400 积分，约能说 2000 个汉字；活动和每日领取以网页显示为准，不承诺一直不变。
4. 打开「API 密钥」建一把专用 key，建议设一个积分硬上限，先用免费积分测试。积分不足就先停用语音，不替他充值。
5. 建空文件 `<工作目录>\private\breeze-key.txt`，用记事本打开，请他粘一行 key、保存关掉。别让他往聊天里贴，也不回显、不截图、不写进答案 JSON 或日志。

计费：1 积分 = 10 个计费字符，一个汉字算 2 个，所以约五个汉字一积分；标点、英文和模型定价细节以官方页面为准。生成候选、试音也可能花积分，不自动重试计费请求。

## 2 先挑一个音色

- 机灵版大肥鱼：`voc_u3nxkd5578wc`（默认）。
- 迷糊版大肥鱼：`voc_8yud7hz7c9q2`。

先问他挑哪个，在网站试听。不要为了选音色两边同时打开 QQ 语音。

## 3 可选：一句话捏新声音

他想换成自己的角色、或者不喜欢现成两种时才做。先问想要的感觉，写一句短描述，例：「困困的年轻女生，声音软糯，说话慢一点，像刚睡醒和朋友聊天。」

优先带他在网站 Voice Design 页生成 1–3 个候选。Agent 用 API 时：

- 读私密文件加请求头 `xi-api-key`；Base URL `https://api.breeze.blue/v1`。密钥不能放进命令参数或输出；Python 从文件读再加请求头。
- `POST /voice-previews/design?output_format=mp3_24000_128`，JSON：`{"voice_description":"描述","text":"我在呀，今天想聊点什么？","model_id":"breeze-tts-2","language_code":"zh","preview_count":1}`。每次最多三个候选，要多出先问他。
- 返回 `previews` 里的 `generated_voice_id` 和 `audio_base_64`（按 `media_type` 解码保存试听音频），先放给他听；满意后才 `POST /voice-previews/{generated_voice_id}/save`，JSON：`{"voice_name":"我的角色","language_code":"zh"}`，拿返回的 `voice_id`。返回字段以当次官方文档为准；遇到变动先查，不猜。
- 只把保存后的 `voc_` 音色 ID 填进 `breeze_voice_id`。描述不是音色 ID，临时 `generated_voice_id` 也不能直接用来聊天。

## 4 接进 AstrBot

保留原有回答字段，增改这些（路径换成他的）：

```json
{
  "voice": "5",
  "breeze_api_key_file": "D:\\dafeiyu\\private\\breeze-key.txt",
  "breeze_voice_id": "voc_u3nxkd5578wc"
}
```

然后跑 `import-pack.ps1 -Dir <工作目录> -Only voice`。导入前自动存快照；只改语音，不改人设、记忆和其他功能。工具会装 `astrbot_plugin_breeze_online_tts`，复用 AstrBot 已有的 ElevenLabs 兼容实现（官方 HTTPS 地址、`breeze-tts-2`、`mp3_24000_128`），额外加密钥文件、限长、并发和整条语音保护。要求 AstrBot 有 `elevenlabs_tts_api`（4.29.3 已核对），旧版本没有时先引导升级 AstrBot。

**切换规则**：原来的豆包、本机 Breeze 提供商停用，不卸载、不删文件；本机 Breeze 程序不再承担语音，想释放显存时先告诉他再停，只清掉本 Skill 创建的 Breeze 自启入口，不改其他软件。切回豆包、本机 Breeze 或先不要时，导入工具会停用在线版。没测通时语音先关着，文字聊天照常。

**开关**：默认约三条回复一条语音，整条回复合成一次；超过 120 个字符（含标点）留文字。AstrBot 的语音总开关关闭，由提供商的 `breeze_online_reply_probability` 控制，避免再按段念一遍。工作台「发语音」的少、中、多会同步改两处概率。不要把总开关打开，否则可能重复合成。

**云端**：密钥经已有 SSH 传到宿主机 `/opt/dafeiyu/private/breeze-key.txt`（权限 600），答案填容器路径 `/dafeiyu/private/breeze-key.txt`。默认 AstrBot 不挂 private：按 `云端.md` C9 给 AstrBot 单独加这个 key 文件的只读挂载、重建该容器，`test -r` 通过再导入；不要挂整个 private。tools 容器已有 private 挂载。服务器不用显卡、不新开公网端口；本轮未上云实测。

## 5 怎么算成功、失败先查什么

- 导入结果 `voice=ok`；后台提供商试音生成非空可播放 MP3（试音也扣积分）。
- 在 QQ 私聊多聊几句，收到一条能播放的语音；群里 @ 她同样能收到，语音不能同时 @ 人。没测到 QQ 就只报 AstrBot 合成通过。
- 收到语音后问「你刚才那条语音说的啥」，她能接上；认知提醒由 `astrbot_plugin_dafeiyu_clean` 提供。
- 超过 120 字的一整条回复、没掷中语音、密钥失效、余额不足、限流、超时，都保留原文字；同一实例一次一个请求，忙时不排队，不自动重试。不要用长文实测浪费积分。
- 不出声音：先看 voice 导入结果和插件是否启用，确认密钥文件存在、不是空的、AstrBot 进程能读。云端尤其检查路径和挂载。
- 密钥/余额/权限出错：本人去「API 密钥」和积分页核对，检查 key 的积分上限是否用完；不打印 key。HTTP 429 是限流，降低「发语音」频率、稍后再试，具体并发以上线账户权益为准。
- 语音叠两套：确认只选中一个提供商，旧豆包停用，AstrBot 语音总开关关闭。仍重叠查别的 TTS 插件，不删除陌生配置。
- 网络错误：报告目标地址和错误类型，不擅自关代理或改直连。

以后说「换成 Breeze 在线版」就照这份做；想换音色只改 `breeze_voice_id` 后重导 voice，已有其他语音先按切换规则停掉。旧设置的快照留在 private，不外传。

官方依据：[API 目录](https://docs.breezeblue.ai/llms.txt)、[给 QQ 大肥鱼接语音](https://breezeblue.ai/zh/blog/give-dafeiyu-a-voice-in-qq)、[文字转语音](https://docs.breezeblue.ai/api-reference/text-to-speech/convert-text-to-speech.md)、[Voice Design](https://docs.breezeblue.ai/api-reference/voice-previews/create-design-previews.md)。
