# QQ 原生通话语音网关（可选）

让 Windows 上登录的 QQ 接到来电后，把对方的声音转成文字，交给任意 OpenAI 兼容模型，再用 TTS 把回复送回通话。

> 这是**轮次式 MVP**：对方说完一句，助手回一句。暂不支持随时打断、主动拨打电话、群语音或视频。
>
> 本目录是可选扩展，配置包根目录的导入脚本不会自动安装它。它不包含任何人设、声音样本或私人配置。

## 它怎么工作

```text
QQ 来电
  -> QQ 扬声器（WASAPI loopback） -> VAD -> 本地 ASR
  -> LLM
  -> TTS -> 虚拟声卡 -> QQ 麦克风 -> 对方在通话里听到
```

- 来电接听：默认手动；可选用 `auto_answer.py` 按白名单自动接听。
- 声音采集：抓 QQ 扬声器的 loopback，不需要另一根虚拟线。
- 回复播放：TTS 播放到 VB-CABLE 的播放端，QQ 麦克风选它的录音端。

## 环境要求

- Windows 10/11
- 已登录的 QQ NT（建议只用小号测试）
- [VB-CABLE](https://vb-audio.com/Cable/)（一根就够）
- FFmpeg（PATH 里或配置路径）
- Python 3.10+
- 本地 ASR：sherpa-onnx + SenseVoice ONNX 模型和 tokens
  - 模型下载：<https://github.com/k2-fsa/sherpa-onnx/releases>
  - 需要 `sherpa-onnx-offline.exe`、`model.int8.onnx`、`tokens.txt`
- 一个 OpenAI 兼容 LLM（DeepSeek、OpenAI 等）
- 三种 TTS 任选

## 安装

```powershell
cd extras\qq-call
.\install.ps1
copy config.example.json config.json
```

然后编辑 `config.json`：

- `llm.base_url` / `llm.api_key` / `llm.model`
- `asr.sherpa_exe` / `asr.model` / `asr.tokens`
- `ffmpeg`：ffmpeg.exe 路径，PATH 里有就留 `ffmpeg`
- `audio.virtual_speaker`：VB-CABLE 的**播放**端名称
- `audio.loopback_speaker`：QQ 实际使用的**扬声器**名称；留空用系统默认
- `tts.provider`：`edge_tts`、`breeze_online` 或 `breeze_local`

## 音频设置（关键）

1. 安装 VB-CABLE。
2. 打开 QQ 设置 → 音视频 → 麦克风，**明确选择**虚拟麦克风的录音端（例如 `CABLE Output (VB-Audio Cable A)`）。只改 Windows 默认有时不够，QQ 会缓存旧设备。
3. `config.json` 的 `audio.virtual_speaker` 填 VB-CABLE 的播放端（例如 `CABLE Input (VB-Audio Cable A)`）。
4. QQ 的扬声器可以保持你平时的输出；程序会 loopback 抓它。
5. 建议通话时戴耳机，避免对方手机外放把助手自己的声音又收回去。

如果 Windows 默认录音也要临时切到虚拟麦克风，可用：

```powershell
.\set_default_mic.ps1 -DeviceName "CABLE Output (VB-Audio Cable A)"
```

需要 `AudioDeviceCmdlets.dll`，从
<https://github.com/frgnca/AudioDeviceCmdlets/releases> 下载后放在同目录。
测完请把默认录音切回原来的设备。

## 运行

手动接听：

```powershell
.\.venv\Scripts\python.exe call_agent.py
```

然后打电话给机器人 QQ，手动接听，说一句，等它回复。

先验证两个子环节：

```powershell
.\.venv\Scripts\python.exe call_agent.py --test-tts
.\.venv\Scripts\python.exe call_agent.py --test-llm
```

### 可选：白名单自动接听

默认关闭。编辑 `config.json`：

```json
"auto_answer": {
  "enabled": true,
  "allowed_callers": ["你的 QQ 备注"],
  "button_name": "接听",
  "poll_seconds": 0.3
}
```

然后另开一个窗口：

```powershell
.\.venv\Scripts\python.exe auto_answer.py
```

只有来电窗口文本里包含白名单名字才会点“接听”。`allowed_callers` 为空时程序直接退出，不会接任何来电。

### 可选：主动拨号 POC

QQ 没有主动拨号 API，只能用 UI 自动化点聊天窗口右上角的电话图标。

先在 `config.json` 填：

```json
"outgoing_call": {
  "contact_title": "对方的 QQ 备注",
  "button_offset_from_right": 285,
  "button_offset_y": 84,
  "call_window_title": "语音通话",
  "timeout_seconds": 10
}
```

先 dry-run，只打印坐标不点击：

```powershell
.\.venv\Scripts\python.exe place_call.py --config config.json
```

确认坐标无误、对方同意接听后：

```powershell
.\.venv\Scripts\python.exe place_call.py --config config.json --call
```

它会临时把聊天窗口提到最前，点完电话后恢复原来的前台窗口，不会让 QQ 一直置顶。坐标是相对窗口右上角算的；QQ 更新或缩放变化时可能需要微调。**只对明确同意的人使用，不要自动群呼或骚扰。**

## 三种 TTS

### 1. edge_tts（默认，最快）

- 约 1–2 秒，免费，无需 key
- 声音是微软 Edge 的通用中文女声，不是自定义音色
- 配置：`tts.provider = "edge_tts"`，`tts.edge_tts.voice`

### 2. breeze_online

- 使用 Breeze 在线 TTS；需要你自己的 Breeze key 和 voice_id
- 本仓库**不附带任何声音**；`voice_id` 留空时程序会报错
- 延迟通常 4–9 秒，适合声音优先
- 配置：`tts.provider = "breeze_online"`，填 `base_url/api_key/voice_id/model`

### 3. breeze_local

- 适合有 NVIDIA GPU 的机器；本机 Breeze 通常 2–3 秒
- 本仓库不带 Breeze 模型，也不绑定某个本地发行版的 API
- 需要你自己把本地 Breeze 服务包一层 HTTP：
  - POST `endpoint`
  - JSON：`{"text": "...", "voice": "..."}`
  - 返回音频字节（mp3/wav 均可，程序会用 ffmpeg 转 wav）
- 配置：`tts.provider = "breeze_local"`，填 `endpoint/voice/timeout`

## 已知限制

- 轮次式，不能随时打断；打断需要额外的 VAD/TTS 中断状态机
- 只支持**对方打进来**；不支持主动拨出
- 只支持私聊语音；群语音/视频未验证
- QQ 更新 UI 后自动接听可能失效

> 主动拨号和“主人说给我打电话”的接线示例见 [WIRING.md](WIRING.md) 与 `astrbot_plugin_qq_call/`。
- 使用 UI 自动化和虚拟声卡，存在账号风控风险；不要用主账号
- 通话音频只在本地处理；LLM/TTS 请求会发到对应服务商

## 隐私

- 本目录不包含真实 QQ 号、API key、人格、声音样本或聊天记录
- 运行时的 `config.json`、录音、wav/mp3 已在 `.gitignore` 中排除
- 不要把 `config.json` 或通话录音提交到 GitHub
