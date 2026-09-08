# astrbot_plugin_mmx_speech — MiniMax 语音播报

让 AstrBot 机器人用 **MiniMax T2A** 音色(默认 `FurinaVoice01` 等复刻音色)开口说话。

## 功能

| 功能 | 说明 |
|---|---|
| 🎙 **LLM 回复自动概率语音** | LLM 回复后按概率把内容变成语音。**每段独立判定**:命中段只发语音(无文字),未命中段发文字。默认概率 **0(关闭)**,在 WebUI 调到 >0 即启用。 |
| 💬 **/speak 朗读引用** | 回复一条消息并输入 `/speak`,朗读该引用消息文本(纯语音)。也支持 `/speak <文本>` 直接朗读。 |
| 🎚 **音色复刻与管理** | `/voice clone <名字>`(附/引用一条 10s~5min 语音)复刻新音色并命名;`/voice list` 查看;`/voice use <名字>` 切换全局默认;`/voice delete <名字>` 删除;`/voice preview <文本>` 试听。 |
| 🛡 **会话白名单/黑名单** | 只允许指定群/私聊使用语音。 |
| 🔁 **失败回退** | TTS 失败自动回退发送文字,内容不丢。 |

## 依赖

- AstrBot ≥ 4.16,平台 **QQ(aiocqhttp)**
- MiniMax API Key(可自动继承 `astrbot_plugin_mmx_cli_tool` 插件已配置的 key)
- 系统 `ffmpeg`(复刻音色时把 QQ 语音转码用)

## 安装

把本目录放到 `AstrBot/data/plugins/` 下,重启 AstrBot。

## 配置

在 AstrBot WebUI 的插件配置中设置:

- `api_key`: MiniMax API Key;留空自动继承 mmx_cli_tool 插件配置
- `default_voice_id`: 默认音色 voice_id(默认 `FurinaVoice01`)
- `voice_probability`: 自动语音概率 0~1(0=关闭自动语音),默认 0
- `enabled_sessions` + `whitelist_mode`: 生效会话白名单/黑名单(填群号或私聊 QQ,留空=全部)
- `max_chars`: 单次语音字符上限(默认 1000)
- 分段相关:`split_chars` / `min_segment_len` / `max_segment_len`
- `fallback_to_text`: 语音失败回退文字

## 使用

```
回复一条消息 + /speak                    → 朗读引用内容
/speak 你好呀                             → 朗读文字
/voice clone 我的声音                     → 然后发/引用一段 ≥10s 语音
/voice list                               → 查看已复刻音色
/voice use 我的声音                       → 切换全局默认
/voice delete 我的声音                    → 删除
/voice preview 今天天气不错               → 试听
```

## 与 chat_enhancer

自动语音采用**接管发送**(模型 A):命中段纯语音、未命中段发文字。
若同时启用 `chat_enhancer` 的「消息分段/合并转发」会互相冲突,二选一即可。
手动 `/speak` 与 `/voice` 指令不受影响。

## License

MIT
