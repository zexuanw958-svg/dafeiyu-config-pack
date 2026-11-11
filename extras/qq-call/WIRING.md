# 让聊天机器人“给我打电话”

这是一个可选的 AstrBot 工具示例：主人在 QQ 里明确说“给我打电话”，模型调用 `call_owner`，插件启动通话 agent 并执行 `place_call.py --call`。

## 流程

```text
主人：给我打电话
  -> AstrBot 调用 call_owner
  -> 检查 sender_id == owner_qq
  -> 启动 call_agent.py（如果尚未运行）
  -> 执行 place_call.py --config config.json --call
  -> QQ 聊天窗口临时前置，点电话图标
  -> 主人手机响，接听
  -> call_agent.py 在通话里用 TTS 回复
```

## 安装

1. 先按上一级 README 配好 `extras/qq-call`，手动测试 `call_agent.py` 和 `place_call.py` 都能用。
2. 把本目录 `astrbot_plugin_qq_call/` 复制到 AstrBot 的 `data/plugins/astrbot_plugin_qq_call/`。
3. 在 AstrBot 插件配置里填：
   - `owner_qq`：唯一允许触发的 QQ 号；
   - `qq_call_dir`：`extras/qq-call` 的绝对路径；
   - `python_exe`：该目录 `.venv\Scripts\python.exe`；
   - `config_file`：`config.json`；
   - 如果用的不是 `call_agent.py`，改 `agent_script` / `agent_args`。
4. 重载插件，确认工具列表出现 `call_owner`。

## 使用

主人私聊或群里 @ 机器人说：

> 小咲，给我打个电话

模型会调用工具；只有 `owner_qq` 能触发。其他人说“给我打电话”只会被拒绝。

## 注意

- 必须明确请求才拨；不要在提示词里让她“主动打电话”。
- `place_call.py` 需要对应联系人的聊天窗口存在；正在聊天时通常已经存在。
- 这是 UI 自动化，QQ 更新/缩放变化可能导致按钮坐标失效，先跑 `place_call.py` 的 dry-run。
- 通话里有没有声音取决于 `call_agent.py` 的 TTS/音频配置，和这个拨号工具是两件事。
- 不要把真实 QQ 号、联系人、key 提交到仓库。
