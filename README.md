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

## 部署与周边治理(重要)

本插件依赖 AstrBot 的 Agent 工具链与若干周边插件。若语音/搜索/画图行为异常,通常是**周边配置**而非本插件问题。以下是已验证的推荐配置:

### 1. 工具可见性(AstrBot 全局配置)

在 `data/cmd_config.json` 的 `provider_settings` 中:

| 配置项 | 推荐值 | 作用 |
|---|---|---|
| `show_tool_use_status` | `false` | **不把工具调用过程发给用户**(否则用户会看到"权限不足"等内部报错) |
| `show_tool_call_result` | `false` | 不把工具原始返回发给用户 |
| `computer_use_runtime` | `none` | 禁用本地 shell/Python/文件工具,避免普通用户触发高权限能力(副作用:管理员也无法使用本地 Agent 工具) |
| `computer_use_require_admin` | `true` | 若必须开 `local` runtime,至少限制为管理员 |

### 2. 危险工具不暴露给非管理员

`astrbot_plugin_mmx_cli_tool` 中的 **音色复刻 LLM 工具(`VoiceClone*`)已不再注册为 Agent 工具**——避免 Agent 在普通聊天中误调用(曾出现拿图片当音频上传、权限不足后反复重试的死循环)。

复刻能力保留在 **`/mmx voice clone|list|use|delete` 直接指令**(管理员可用),功能不受影响。

### 3. Skills 收敛(减少工具选择干扰)

AstrBot 的 `data/skills/` 下激活过多 skill 会向 system prompt 注入大量工具描述,导致 Agent 选错工具(例如:搜索请求被绕道去查插件索引)。推荐**只保留 `multimedia-tools`**,停用其余:

```
data/skills/
├── multimedia-tools    ✅ 保留(联网搜索/画图/看图/语音/视频/音乐路由)
├── cross-chat-access   ❌ 停用
├── memory-and-state    ❌ 停用
├── server-operations   ❌ 停用(其 plugin_index/server_exec 易诱导 Agent 绕路)
└── social-actions      ❌ 停用
```

### 4. 画图请走 omnidraw(万象画卷)

`astrbot_plugin_omnidraw` 的 `generate_image` / `generate_selfie` **自带图片下发**,调用后会把图直接发给用户;
而 `mmx_generate_image` 只返回 CDN 链接,Agent 需额外转发,容易出现"生成了但用户收不到图"。

推荐:`multimedia-tools` skill 里把画图路由指向 omnidraw,并停用 `mmx_generate_image` 这个 LLM 工具。

### 5. 人设里声明工具协作规则

若机器人使用傲娇/高冷等人设(如"芙宁娜"),需在人设 system prompt 中显式声明:

> 用户请你搜索/查资料/画图时是在请你帮忙,不是把你当工具人。可以保持语气傲娇,但**必须先真实调用对应工具**,不能编造答案或回"自己去查"。

否则模型可能出于人设"拒绝被使唤"而不调用工具(曾出现:被要求搜索时回"自己去官网查")。

### 6. 常见故障排查

详见 [OPS_NOTES.md](./OPS_NOTES.md)(含:语音崩溃、搜索不生效、图片不发、工具死循环等真实案例与定位方法)。

## License

MIT
