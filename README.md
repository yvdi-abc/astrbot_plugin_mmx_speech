# astrbot_plugin_mmx_speech — MiniMax 语音播报

让 AstrBot 机器人用 **MiniMax T2A** 音色(默认 `FurinaVoice01` 等复刻音色)开口说话。

## 功能

| 功能 | 说明 |
|---|---|
| 🎙 **LLM 回复自动概率语音** | `auto_speak_mode=dual`(默认):**文字照常发**(与 chat_enhancer 兼容),按概率延迟约 2 秒叠一条语音;`voice_only`:接管发送,命中段纯语音/未命中段文字;`off`:关闭。默认概率 30%,可调。 |
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
- `voice_probability`: 自动语音概率 0~1(0=不自动加语音),默认 0.3
- `auto_speak_mode`: `dual`(默认,文字照发+语音叠加,兼容 chat_enhancer)/ `voice_only`(命中段纯语音,需关 chat_enhancer)/ `off`
- `dual_delay_seconds`: dual 模式语音延迟秒数(默认 2)
- `enabled_sessions` + `whitelist_mode`: 生效会话白名单/黑名单(填群号或私聊 QQ,留空=全部)
- `max_chars`: 单条语音字符上限(默认 1000)
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
/voice preview 今天天气不错  → 试听
```

## 与 chat_enhancer

默认 `auto_speak_mode=dual`:本插件**不接管文字发送**,chat_enhancer 照常分段/合并转发文字,语音按概率延迟约 2 秒独立追加,二者可共存。
仅当切到 `voice_only`(命中段纯语音)才需要关闭 chat_enhancer 的分段/转发。手动 `/speak` 与 `/voice` 指令始终不受影响。

## License

MIT
