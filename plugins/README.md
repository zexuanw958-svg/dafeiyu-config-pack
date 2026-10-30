额外插件放这里（AstrBot 插件的 zip 包，或带 metadata.yaml 的插件文件夹）。导入工具会一起装上；同名的 .json 可以写这个插件的设置。

同名 .json 里写了 `"only_with_voice": "3"` 的（豆包语音插件 `astrbot_plugin_seed_audio_tts`、唱歌插件 `astrbot_plugin_dafeiyu_sing`），只在导入时语音选了 3 才装。

- `astrbot_plugin_breeze_online_tts`：Breeze 在线语音（voice 5 时安装），复用 AstrBot ElevenLabs 接口；密钥文件、整条短回复、限长、失败保留文字。
