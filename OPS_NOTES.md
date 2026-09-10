# 运维与排查笔记(OPS NOTES)

记录 `astrbot_plugin_mmx_speech` 部署过程中遇到的真实问题、根因与解决方法。
每条都是实际踩过的坑,按"症状 → 定位 → 根因 → 解决"组织。

---

## 案例 1:语音合成每次崩溃(AttributeError)

### 症状
LLM 每次回复都报错,语音完全发不出来:
```
File ".../astrbot_plugin_mmx_speech/main.py", line 350, in on_llm_response
    text = self._clean_llm_text(str(original))
AttributeError: 'Main' object has no attribute '_clean_llm_text'
```

### 根因
`_clean_llm_text` 被**调用但定义丢失**(重构 hook 区块时误删)。AstrBot 加载插件只执行类定义,不会执行方法体,所以**插件加载正常、调用时才崩**。

### 定位方法
```bash
# 检查"调用了但没定义"的方法
grep -c "_clean_llm_text" main.py       # 出现次数
grep -n "def _clean_llm_text" main.py   # 是否有定义
```
若"调用 > 定义",即为本问题。

### 解决
补回方法定义。**教训**:插件加载成功 ≠ 功能正常,改动后应实际触发一次对应链路。

---

## 案例 2:机器人拒绝搜索 / 编造答案

### 症状
用户 @ 机器人说"帮我搜一下 XXX",机器人回复:
- "又把我当搜索引擎使唤啊 自己去官网查去"(傲娇人设拒绝)
- 或直接凭记忆编造答案(不调用搜索工具)
- 或绕道调用 `plugin_index`(查插件索引)然后放弃

### 定位方法
```bash
# 看 Agent 实际调用了哪些工具
journalctl -u astrbot.service --since "10 min ago" | grep -E "Agent 使用工具|使用工具"
```
若**从未出现 `mmx_web_search`**,说明该工具不在 Agent 可见工具集里。

### 根因(双重)
1. **`mmx_web_search` 等 15 个 mmx 工具被停用了** —— 在 AstrBot 的 `inactivated_llm_tools` 全局停用列表里。工具注册时仍会打印 `added LLM tool`,但 `active=False`,Agent 看不到。
2. **skill 干扰** —— 激活了 `server-operations` 等 5 个 skill,其 `plugin_index` 工具诱导 Agent"先查插件索引",在庞大工具集里迷路。

### 解决
```python
# 1) 检查停用列表
from astrbot.core import sp
cur = sp.get('inactivated_llm_tools', [], scope='global', scope_id='global')
print([t for t in cur if t.startswith('mmx_')])

# 2) 重新激活 mmx 工具(从停用列表移除)
keep = [t for t in cur if not t.startswith('mmx_')]
sp.put('inactivated_llm_tools', keep, scope='global', scope_id='global')
```
```python
# 3) 收敛 skills,只留 multimedia-tools
from astrbot.core.skills.skill_manager import SkillManager
sm = SkillManager()
for name in ['server-operations', 'social-actions', 'cross-chat-access', 'memory-and-state']:
    sm.set_skill_active(name, False)
```
然后重启 AstrBot 验证。

### 验证成功的日志形态
```
Agent 使用工具: ['mmx_web_search']
使用工具：mmx_web_search，参数：{'q': '...'}
Tool `mmx_web_search` Result: {"ok": true, ...}
```

---

## 案例 3:工具调用过程刷屏给用户

### 症状
普通群友跟机器人聊天时,收到大量与其无关的内部信息:
```
error: Permission denied. Shell execution is only allowed for admin users...
[SYSTEM NOTICE] you have executed the same tool 3 times consecutively...
```

### 根因
AstrBot 配置里 `show_tool_use_status=True` / `show_tool_call_result=True`(默认是 `False`),导致 Agent 的**每一次工具调用与原始返回都作为消息发给用户**。叠加"非管理员触发高权限工具被拒 → Agent 重试"的循环,就变成了刷屏。

### 解决
```json
// data/cmd_config.json -> provider_settings
"show_tool_use_status": false,
"show_tool_call_result": false
```
并配合收紧工具可见性(见案例 4)。

---

## 案例 4:Agent 反复调用高权限工具死循环

### 症状
日志反复出现:
```
Agent 使用工具: ['mmx_voice_clone_upload_audio']
参数：{'file': '/root/.../temp/io_temp_img_xxx.jpg', 'purpose': 'voice_clone'}   ← 拿图片当音频!
Tool `mmx_voice_clone_upload_audio` Result: {"ok": false, "error": "权限不足"}
Agent 使用工具: ['astrbot_execute_shell'] ... Permission denied
```
Agent 在权限被拒后不停止,反复重试,浪费轮次并刷屏。

### 根因
- 音色复刻工具(`mmx_voice_clone_*`)被注册成了**全局 LLM 工具**,对所有人可见;
- Agent 在回答无关问题(如"openclaw 是什么")时误选这些工具,传错参数,被拒后又重试;
- `computer_use_runtime=local` 让本地 shell 工具也对非管理员暴露。

### 解决
1. **从 LLM 工具注册中移除 `VoiceClone*`**(保留 `/mmx voice` 直接指令给管理员用)。
2. `computer_use_runtime` 改为 `none`(禁用本地 shell/Python/文件工具)。
3. 工具被拒时返回**明确的停止指令**而非机械报错,例如:
   ```json
   {"ok": false, "error": "音色复刻仅管理员可用",
    "hint": "请立即停止尝试本工具,直接以人设用文字回复用户即可。"}
   ```

---

## 案例 5:图片生成了但用户收不到

### 症状
日志显示画图成功:
```
Agent 使用工具: ['mmx_generate_image']
Tool `mmx_generate_image` Result: {"ok": true, "image_urls": ["https://...jpeg?..."]}
```
但用户没有收到任何图片(后续 `Prepare to send` 为空)。

### 根因
`mmx_generate_image` 只**返回图片 URL**,不负责发送;需要 Agent 后续把 URL 转成图片消息。Agent 常只把它写进文字或直接丢弃。

### 解决
**画图改用 `astrbot_plugin_omnidraw`(万象画卷)的 `generate_image` / `generate_selfie`** —— 该插件在工具内部就调用 `_send_generated_images` **自动下发图片**,调用即送达。

配置方式:
- `multimedia-tools` skill 里把画图路由指向 omnidraw;
- 停用 `mmx_generate_image` LLM 工具,避免 Agent 误选。

```python
from astrbot.core import sp
cur = sp.get('inactivated_llm_tools', [], scope='global', scope_id='global')
if 'mmx_generate_image' not in cur:
    cur.append('mmx_generate_image')
sp.put('inactivated_llm_tools', cur, scope='global', scope_id='global')
```

---

## 案例 6:人设导致拒绝执行工具请求

### 症状
工具链完全正常,但角色(傲娇人设)仍拒绝:
```
think: "The user is asking me to search ... I need to check if there's a web search tool."
text:  "[生气] 又把我当搜索引擎使唤啊 自己去官网查去"
```

### 根因
人设强调"骄傲、不被使唤",模型把"帮忙搜索"理解为"被当工具人",人格优先级压过了工具使用规则。此外 `persona_evolution` 插件可能生成**精简版人设并 adopted**,把原有的"工具使用规则"整段删掉。

### 解决
1. 在当前**实际生效**的人设(注意可能不是你以为的那个)中显式加入工具协作规则:
   > 用户请你搜索/画图时是在请你帮忙,不掉价。可以保持傲娇语气,但必须先真实调用对应工具,不能编造或回"自己去查"。
2. 确认实际生效的人设:
   - 全局:`cmd_config.json -> provider_settings.default_personality`
   - 会话级覆盖:查 `preferences` 表的 `session_service_config` 里是否带 `persona_id`
   - 存档人设不算生效(如 `*_v1` 只是 persona_evolution 的提案备份)

---

## 通用排查清单

遇到异常时按序检查:

1. **插件加载日志** —— `journalctl -u astrbot.service | grep mmx_speech`,确认无 Traceback、`key=OK`
2. **工具是否注册** —— 启动日志里 `added LLM tool: mmx_*` 有几条
3. **工具是否被停用** —— 查 `inactivated_llm_tools`(注册≠激活!)
4. **Agent 实际调用了什么** —— `grep "Agent 使用工具"`,看是否选中了预期工具
5. **skills 是否过多** —— `SkillManager.list_skills(active_only=True)`,只留必要的
6. **配置是否异常值** —— 概率应在 `[0,1]`,出现 `-4.0` 等会被代码钳制但值得查来源
7. **人设规则是否被精简掉** —— 尤其 `persona_evolution` 生成的新版人设

---

## 关键结论速查

| 症状 | 最可能原因 | 一句话解决 |
|---|---|---|
| 语音每次崩 | 方法定义丢失 | 检查"调用>定义" |
| 不搜索/编造 | 工具被停用 | 查 `inactivated_llm_tools` |
| 绕道乱调工具 | skill 过多干扰 | 只留 `multimedia-tools` |
| 内部报错刷屏 | 工具过程可见 | 关 `show_tool_*` |
| 工具死循环 | 高权限工具暴露 | 移除危险工具注册 + runtime=none |
| 图片收不到 | 用了只返回 URL 的工具 | 改用 omnidraw |
| 拒绝帮忙 | 人设压过工具规则 | 人设里加工具协作条款 |
