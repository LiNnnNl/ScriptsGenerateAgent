11# ScriptsGenerateAgent → AutoGen 多 Agent 架构改造报告

## 一、改造背景与动机

### 原有架构的痛点

原系统采用**单次 LLM 调用**的线性流水线：

```
用户请求 → 构建 6000-8000 字超大 Prompt → 单次 LLM 调用 → 验证 → 输出
```

存在三个核心问题：

| 问题 | 具体表现 |
|------|---------|
| **验证失败无法自动修复** | 检测到错误只打印警告，无重试机制，用户只能重新请求 |
| **超大 Prompt 导致 AI 遗漏约束** | 角色性格、场景点位、动作库、镜头分组规则全部混在一个 Prompt，AI 注意力分散 |
| **单一视角，无协作** | 剧情批评、对白打磨、技术验证全部依赖同一次 LLM 调用，质量参差不齐 |

---

## 二、改造目标

引入 **Microsoft AutoGen** 框架，将单次 LLM 调用重构为**多 Agent 协作流程**：

- 每个 Agent 只专注一件事（单一职责原则）
- 验证失败时自动反馈给 DirectorAgent 修复，无需用户重新请求
- 保持现有 Flask NDJSON 流式接口完全不变（**前端零改动**）
- 为未来接入摄影 Agent、音效 Agent 等新角色预留扩展点

---

## 三、改造后的整体架构

```
┌─────────────────────────── 用户请求 ────────────────────────────┐
│  POST /api/generate（Flask 接口，对外不变）                      │
│            ↓                                                     │
│  AutoGenStreamBridge（threading.Queue 跨线程桥接）               │
│            ↓  独立线程中运行 asyncio 事件循环                    │
├─────────────────────── AutoGen Pipeline ─────────────────────────┤
│                                                                  │
│  ① DirectorAgent ── 根据场景/角色/动作约束生成剧本 JSON 初稿     │
│            ↓                                                     │
│  ② CriticAgent  ── 审查叙事逻辑和角色行为 ──┐                   │
│     DialogueAgent ─ 审查对白质量和风格     ──┘ ≤ 2 轮修改循环   │
│            ↓（有问题则反馈给 DirectorAgent 修改）                │
│  ③ ValidationAgent ─ 调用 Python 工具验证全部技术约束            │
│            ↓（验证失败则让 DirectorAgent 修复，最多 1 次）       │
│  ④ OutputAgent ─── 纯 Python，生成最终 JSON 文件                │
│                                                                  │
├─────── 所有阶段的事件通过 bridge 实时推送到前端 ─────────────────┤
│  {"type": "thinking_chunk", "agent": "DirectorAgent", "text": …}│
│  {"type": "log", "level": "info", "message": "…"}               │
│  {"type": "success", "filename": "script_xxx.json"}              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 四、文件变动总览

### 新增文件（4 个）

| 文件 | 职责 |
|------|------|
| `backend/src/autogen_bridge.py` | asyncio ↔ Flask 同步流式桥接器 |
| `backend/src/autogen_tools.py` | 验证函数提取 + camera_group 新增检查 + FunctionTool 封装 |
| `backend/src/autogen_agents.py` | 4 个 Agent 的定义和 system_message 构建 |
| `backend/src/autogen_pipeline.py` | Pipeline 编排：4 阶段流程、审查循环、修复逻辑 |

### 修改文件（2 个）

| 文件 | 改动内容 |
|------|---------|
| `backend/requirements.txt` | 新增 `autogen-agentchat` 和 `autogen-ext[openai]` 依赖 |
| `backend/app.py` | `/api/generate` 端点内部从 300 行精简为 7 行，其余路由不动 |

### 未改动文件

| 文件 | 原因 |
|------|------|
| `backend/src/resource_loader.py` | 纯数据模型层，无框架依赖，直接复用 |
| `backend/src/json_generator.py` | 验证和转换函数被 autogen_tools.py 直接调用 |
| `backend/src/director_ai.py` | 保留作为非 AutoGen 模式的 fallback |
| 所有前端文件 | NDJSON 事件格式向后兼容，前端零改动 |

---

## 五、关键代码解析

### 5.1 `autogen_bridge.py` — 异步/同步桥接器

**解决的核心问题**：AutoGen 基于 `asyncio`（异步），而 Flask 流式响应是同步生成器，两者无法直接对接。

**解决方案**：用 `threading.Queue` 作为跨线程"邮箱"。AutoGen 在独立线程的 asyncio 事件循环中运行，每产生一个事件就往邮箱里放；Flask 线程从邮箱里取事件，逐行发给前端。

```python
class AutoGenStreamBridge:
    def __init__(self):
        self._queue = queue.Queue()
        self._SENTINEL = object()  # 唯一对象，用于标记流结束

    def run_in_thread(self, coroutine):
        """在独立线程中启动 asyncio 事件循环运行 AutoGen Pipeline"""
        def _runner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(coroutine)
            except Exception as e:
                self.put_event({'type': 'error', 'message': str(e)})
            finally:
                loop.close()
                self._queue.put(self._SENTINEL)  # 无论成功还是失败，都发送结束信号
        threading.Thread(target=_runner, daemon=True).start()

    def put_event(self, event_dict):
        """AutoGen Pipeline 调用此方法发送事件（线程安全）"""
        self._queue.put(json.dumps(event_dict, ensure_ascii=False) + '\n')

    def flask_generator(self):
        """Flask stream_with_context 消费此生成器，阻塞等待直到流结束"""
        while True:
            item = self._queue.get()
            if item is self._SENTINEL:  # 收到结束信号，退出
                break
            yield item
```

> **设计亮点**：`_SENTINEL` 是一个普通 `object()` 实例，Python 中每个 `object()` 是唯一的，只有 bridge 内部才能产生它，因此作为流结束信号绝对可靠，不会和任何真实事件混淆。

---

### 5.2 `autogen_tools.py` — 验证工具层

**解决的核心问题**：
1. 原有验证逻辑封装在 `DirectorAI` 类内部，无法独立调用，也无法作为 AutoGen 工具暴露给 Agent
2. 原逻辑**缺少 camera_group 分组一致性的代码层验证**（只在 Prompt 里口头要求 AI 遵守）

**新增的 camera_group 验证**：

同一镜头只能拍摄同一 camera_group 内的角色。若角色 A 在 group1 的点位、角色 B 在 group2 的点位，但他们出现在同一对白片段，就会导致镜头穿帮。原系统只靠 Prompt 约束 AI，缺乏代码兜底。

```python
def _check_camera_group_consistency(segment, scene_idx, seg_idx, scene, errors):
    # 找出该片段中所有参演角色
    action_chars = {a.get("character") for a in segment.get("actions", [])}

    # 查询每个角色当前站位所属的 camera_group
    current_pos_map = {
        p["character"]: p["position"]
        for p in segment.get("current position", [])
    }

    groups_seen = set()
    for char_name in action_chars:
        pos_id = current_pos_map.get(char_name)
        group = scene.get_group_for_position(pos_id)
        if group:
            groups_seen.add(group)

    # 出现超过 1 个不同镜头组 → 错误
    if len(groups_seen) > 1:
        errors.append(
            f"场景{scene_idx} 片段{seg_idx}: "
            f"对白中角色分属不同 camera_group（{groups_seen}），"
            f"同一镜头只能拍摄同组点位内的角色"
        )
```

**FunctionTool 封装**（让 ValidationAgent 可以主动调用）：

```python
def make_validation_tools(resource_loader, scene):
    from autogen_core.tools import FunctionTool

    def _validate_constraints(script_json_str: str) -> str:
        # AutoGen 工具要求输入输出均为字符串，在此做 JSON 序列化
        script = json.loads(script_json_str)
        result = validate_script_constraints(script, scene, resource_loader)
        return json.dumps(result, ensure_ascii=False)

    def _validate_spec(script_json_str: str) -> str:
        script = json.loads(script_json_str)
        return json.dumps(validate_json_spec(script), ensure_ascii=False)

    return [
        FunctionTool(_validate_constraints, description="验证点位/动作/camera_group 约束"),
        FunctionTool(_validate_spec,        description="验证 JSON 结构规范完整性"),
    ]
```

---

### 5.3 `autogen_agents.py` — Agent 定义

定义 4 个专业化 Agent，每个只接收与其职责相关的信息：

| Agent | 接收的信息 | 刻意不接收 | 职责 |
|-------|----------|-----------|------|
| **DirectorAgent** | 全量约束（点位/动作/镜头组）+ 角色性格 | — | 生成 / 修改剧本 JSON |
| **CriticAgent** | 仅 `speaker`、`content`、`scene_information` | 所有技术字段 | 叙事逻辑与角色行为审查 |
| **DialogueAgent** | 仅 `speaker`、`content` | 所有技术字段 | 对白质量与风格打磨 |
| **ValidationAgent** | 剧本 JSON 字符串 + 两个工具 | — | 调用工具执行技术验证 |

**CriticAgent 的 system_message**（核心设计：明确告知忽略技术字段）：

```python
def build_critic_system_message():
    return (
        "你是一位专业的剧本顾问，专注于叙事质量分析。\n\n"
        "你只需关注：\n"
        "- scene_information.what（场景核心事件）\n"
        "- speaker 和 content（对白内容）\n"
        "- 角色的整体行为是否与其性格相符\n\n"
        # 明确排除范围，避免 AI 越权
        "**请忽略** JSON 中的所有技术字段（position、action_id、"
        "camera_group 等），这不是你的职责。\n\n"
        # 强制结构化输出，避免自由文本导致解析失败
        '输出格式必须是：{"has_issues": bool, "issues": [...], '
        '"revision_instruction": "..."}'
    )
```

**ValidationAgent 的 system_message**（强制调用工具，禁止 AI 自行猜测）：

```python
def build_validation_system_message():
    return (
        "你是一位技术验证员，负责验证剧本的技术约束。\n\n"
        "收到剧本 JSON 字符串后，你必须严格按以下步骤执行：\n"
        "1. 调用 _validate_constraints 工具\n"
        "2. 调用 _validate_spec 工具\n"
        "3. 汇总两个工具的结果并输出\n\n"
        # 关键约束：防止 AI 产生幻觉式的"验证"
        "**禁止**自行判断技术约束，必须通过工具验证。"
    )
```

---

### 5.4 `autogen_pipeline.py` — Pipeline 编排

整个 AutoGen 流程的核心，一个 `async` 协程，编排 4 个阶段的执行顺序。

**阶段①：DirectorAgent 生成初稿**（含实时思考流）

```python
async for event in director.on_messages_stream(
    [TextMessage(content=user_prompt, source="user")],
    cancellation_token=CancellationToken()
):
    if hasattr(event, 'inner_messages'):
        for msg in (event.inner_messages or []):
            if isinstance(msg, ModelClientStreamingChunkEvent):
                # 把 AI 实时思考内容推送给前端
                bridge.put_event({
                    'type': 'thinking_chunk',
                    'agent': 'DirectorAgent',
                    'text': msg.content
                })
    elif hasattr(event, 'chat_message') and event.chat_message:
        # 生成完成，提取 JSON
        draft_script = _extract_json_from_text(event.chat_message.content)
```

**阶段②：审查循环**

```python
for review_round in range(MAX_REVIEW_ROUNDS):  # 最多循环 2 次
    # 关键优化：过滤掉技术字段，只把 speaker/content 传给审查 Agent
    # 避免大型 JSON 白白消耗 CriticAgent 的 Token
    filtered_script_str = _filter_script_for_review(draft_script)

    critic_feedback   = await _run_agent(critic,   filtered_script_str)
    dialogue_feedback = await _run_agent(dialogue, filtered_script_str)

    if not critic_feedback['has_issues'] and not dialogue_feedback['has_issues']:
        break  # 两位审查官都满意，退出循环

    # 汇总反馈，让 DirectorAgent 修改
    revision_prompt = f"请根据以下审查意见修改剧本：\n{合并反馈内容}"
    draft_script = await _run_director_revision(director, revision_prompt, draft_script)
```

**阶段③：技术验证 + 自动修复**

```python
for fix_round in range(MAX_FIX_ROUNDS + 1):  # 失败最多自动修复 1 次
    validation_result = await _run_validation_agent(validator, draft_json_str)

    if validation_result['valid']:
        break  # 验证通过，退出

    if fix_round >= MAX_FIX_ROUNDS:
        # 超过修复次数上限，带 warning 强制输出，不崩溃
        break

    # 精准修复 Prompt：只告诉 AI 哪里错了，不重传全量资源
    fix_prompt = (
        f"以下剧本存在技术约束错误，请仅修复这些错误，不要改动其他内容：\n"
        f"{错误列表}\n\n当前剧本：{原剧本JSON}"
    )
    draft_script = await _run_director_fix(director, fix_prompt)
```

---

### 5.5 `app.py` — 端点简化

`/api/generate` 端点内部从 **300 行手动流水线**精简为 **7 行**：

```python
# ── 改造前（300+ 行，手动编排每一个步骤）──
@app.route('/api/generate', methods=['POST'])
def generate_script():
    def generate():
        try:
            data = request.json
            scene = resource_loader.get_scene_by_id(data.get('scene_id'))
            # ... 构建角色、初始化 DirectorAI、调用 stream、验证、转换、保存 ...
            # ... 300 行逻辑 ...
        except Exception as e:
            yield json.dumps({'type': 'error', ...}) + '\n'
    return Response(stream_with_context(generate()), mimetype='application/x-ndjson')

# ── 改造后（7 行，Pipeline 接管所有逻辑）──
@app.route('/api/generate', methods=['POST'])
def generate_script():
    def generate():
        bridge = AutoGenStreamBridge()
        bridge.run_in_thread(
            run_autogen_pipeline(bridge, resource_loader, request.json)
        )
        yield from bridge.flask_generator()
    return Response(stream_with_context(generate()), mimetype='application/x-ndjson')
```

---

## 六、改造前后对比

| 维度 | 改造前 | 改造后 |
|------|-------|-------|
| LLM 调用次数 | 1 次 | 4–8 次（含审查和修复轮次） |
| 验证失败处理 | 打印 warning，继续输出 | 自动反馈 DirectorAgent 修复 |
| 单次 Prompt 大小 | 6000–8000 字（全部信息混合） | 各 Agent 各司其职，1000–3000 字 |
| camera_group 验证 | 仅 Prompt 口头要求，无代码兜底 | **新增**代码层验证，可精确定位错误片段 |
| 流程可观测性 | 只有 thinking / success / error | 每个 Agent 的步骤实时可见 |
| 扩展新 Agent | 需修改单个大类 | 在 pipeline 审查层添加新 Agent，不影响其他逻辑 |
| `app.py` 端点行数 | 300+ 行 | 7 行 |

---

## 七、前端日志展示示例

改造后，用户在前端日志面板将看到以下实时事件流：

```
[info]    🤖 初始化多 Agent 系统...
[success] ✅ Agents 初始化完成（导演、批评家、对白专家、技术验证）
[info]    🎬 [DirectorAgent] 开始生成剧本初稿...
            ← 实时 thinking_chunk 流（AI 思考过程）→
[success] ✅ [DirectorAgent] 剧本初稿生成完成
[info]    🔍 审查轮次 1/2：启动批评家与对白专家...
[info]    ✏️  [DirectorAgent] 根据审查意见修改剧本（轮次1）...
[success] ✅ 修改完成（轮次1）
[info]    🔧 [ValidationAgent] 开始技术约束验证...
[info]    🔍 [ValidationAgent] 正在执行技术验证...
[success] ✅ [ValidationAgent] 技术约束验证通过
[success] ✅ 已生成角色档案：3 位演员
[success] → 剧本生成完成，可下载
```

---

## 八、后续扩展方向

本次改造为以下功能预留了扩展点：

1. **新增专业 Agent**：在 `autogen_agents.py` 中定义新 Agent（如摄影指导 Agent 负责镜头设计），在 `autogen_pipeline.py` 的审查层并行调用，不影响现有 Agent
2. **多轮用户修改**：在 `app.py` 新增 `POST /api/refine` 端点，利用 Pipeline 的结构化 `context_variables` 支持用户自然语言迭代剧本
3. **接入外部 Agent 系统**：AutoGen 的 Agent 通信协议天然支持跨进程、跨服务的 Agent 接入，可与其他团队的 Agent 系统对接

---

*改造日期：2026-03-24*
*框架版本：autogen-agentchat >= 0.4.0 / autogen-ext[openai] >= 0.4.0*
