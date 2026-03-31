# Phase 4 更新说明

> 本文档记录在 `AUTOGEN_REFACTOR_REPORT.md` 基础上新增和修改的内容。
> 改造日期：2026-03-31

---

## 一、变更概览

| 类型 | 文件 | 变更内容 |
|------|------|---------|
| 修改 | `backend/src/autogen_agents.py` | DirectorAgent 提示词重构；新增 PositionAgent |
| 修改 | `backend/src/autogen_tools.py` | 新增抽象位置跳过逻辑 |
| 修改 | `backend/src/autogen_pipeline.py` | 循环上限统一为 3；新增 Stage ④ 和 Stage ④b |
| 新增 | `backend/src/position_agent_wrapper.py` | subprocess 封装层 |
| 新增 | `backend/resources/scene_exports/SpaceStation.json` | SpaceStation 场景 Unity 导出数据 |
| 新增 | `backend/resources/position_templates/SpaceStation.json` | SpaceStation 点位坐标模板 |
| 修改 | `frontend/js/config.js` | API BASE_URL 修复 |
| 修改 | `backend/.env` / `autogen_agents.py` | 模型切换为火山引擎 doubao |

---

## 二、核心新增：PositionAgent（走位职责剥离）

### 2.1 问题与动机

原始报告中，`DirectorAgent` 的 system_message 包含完整的场景点位列表（`scene.valid_positions`）和镜头分组信息（`camera_groups`），AI 需同时承担**编剧**和**走位**两个职责：

- 剧情创作受技术点位约束干扰，演出效果服从于现有点位布局
- 点位信息增大了 DirectorAgent 的 Prompt，分散注意力
- 未来新增点位时无法灵活扩展

### 2.2 改造后的架构

```
改造前：
  DirectorAgent（接收真实点位 + 镜头组 → 直接输出真实点位 ID）

改造后：
  DirectorAgent（纯编剧，输出 "Position 1/2/3" 抽象编号 + position_descriptions）
       ↓
  PositionAgent（读取 scenes_resource.json，将抽象编号映射为真实点位 ID）
       ↓ 映射失败 → POSITION_UNRESOLVED → DirectorAgent 修改戏剧意图 → 重试（≤3 轮）
       ↓ 映射成功 → 更新 draft_script
  position_agent_standalone.py（生成 3D 坐标 position.json，缺少资源文件时静默跳过）
```

---

## 三、`autogen_agents.py` 修改详情

### 3.1 DirectorAgent 提示词——移除点位信息

**改造前**（scene_info 部分）：
```python
scene_info = f"### {scene.name} (ID: {scene.id})\n"
scene_info += f"- 描述: {scene.description}\n"
scene_info += "- 可用点位:\n"
for pos in scene.valid_positions:
    scene_info += f"  - {pos['id']}: {pos['description']}\n"
# + camera_groups 信息
```

**改造后**（只保留名称和描述，点位信息完全移除）：
```python
# 2. 场景信息（不暴露具体点位，由 PositionAgent 处理映射）
scene_info = f"## 场景信息\n\n### {scene.name} (ID: {scene.id})\n"
scene_info += f"- 描述: {scene.description}\n\n"
```

### 3.2 DirectorAgent 提示词——走位规则重写

**改造前**（要求 AI 直接使用真实点位 ID）：
```
2. **走位决策**：
   - 只能使用"可用点位"列表中的 ID
   - 同一对白片段内所有角色必须属于同一 camera_group
```

**改造后**（AI 只负责演出意图，不接触技术约束）：
```python
"2. **走位设计（以演出效果为唯一标准）**:\n"
"   - 根据演出需要决定角色站位，依次命名为 Position 1、Position 2...\n"
"   - 在顶层 `position_descriptions` 字段中用自然语言描述每个位置的戏剧意图\n"
"   - 例：\"Position 1\": \"近窗俯瞰，背靠星空，适合独白或凝望\"\n"
"   - 位置映射将由专门的位置代理处理，你只需专注于演出效果\n\n"
```

**DirectorAgent 输出 JSON 格式变化**——每个场景对象新增顶层字段：
```json
[
  {
    "position_descriptions": {
      "Position 1": "近窗俯瞰，背靠星空，适合独白或凝望",
      "Position 2": "记录台旁，可坐可站，适合工作状态或低调观察"
    },
    "scene information": { ... },
    "initial position": [
      {"character": "温迪", "position": "Position 1"}
    ],
    "scene": [ ... ]
  }
]
```

### 3.3 新增 PositionAgent

`build_position_agent_system_message(scene)` 将场景的完整点位列表和镜头分组注入 PositionAgent：

```python
def build_position_agent_system_message(scene: Scene) -> str:
    positions_info = ""
    for pos in scene.valid_positions:
        sittable = " [可坐]" if pos.get('is_sittable', False) else ""
        group_tag = f" [镜头组{pos['camera_group']}]" if pos.get('camera_group') else ""
        positions_info += f"- **{pos['id']}**{sittable}{group_tag}: {pos['description']}\n"

    camera_groups_info = ""
    if scene.camera_groups:
        camera_groups_info = "\n#### 镜头分组（同一对白片段内所有角色必须属于同一镜头组）:\n"
        for group in scene.camera_groups:
            pos_list = ", ".join(group['position_ids'])
            camera_groups_info += f"- **{group['id']}组 - {group['name']}**: {pos_list}\n"

    return (
        "你是位置映射专家。你的任务是把剧本中的抽象站位（Position 1/2/3...）"
        "映射到真实场景中已有的点位。\n\n"
        f"## 当前场景：{scene.name} (ID: {scene.id})\n\n"
        "### 可用真实点位:\n"
        + positions_info + camera_groups_info
        + "\n\n## 你的工作步骤:\n\n"
        "1. 读取剧本每个场景对象顶层的 `position_descriptions` 字段，了解每个抽象位置的戏剧意图\n"
        "2. 对照上方可用真实点位，为每个抽象位置选择最匹配戏剧意图的真实点位 ID\n"
        "3. **确保同一对白片段**中所有角色的映射点位属于同一镜头组\n"
        "4. 将剧本中所有 `\"Position N\"` 替换为真实点位 ID\n"
        "5. 删除每个场景对象中的 `position_descriptions` 字段\n"
        "6. 输出修改后的完整剧本 JSON\n\n"
        "## 无法映射时的处理:\n\n"
        "在输出 JSON **之前**声明：\n"
        "POSITION_UNRESOLVED: Position X → 原因描述\n"
    )


def create_position_agent(scene: Scene, model: Optional[str] = None) -> AssistantAgent:
    return AssistantAgent(
        name="PositionAgent",
        model_client=make_model_client(model),
        system_message=build_position_agent_system_message(scene),
    )
```

### 3.4 默认模型变更

```python
# 改造前
model_name = model or os.getenv("MODEL", "deepseek-v3-241226")

# 改造后（匹配 .env 中的火山引擎 ARK 模型）
model_name = model or os.getenv("MODEL", "doubao-seed-2-0-lite-260215")
```

---

## 四、`autogen_tools.py` 修改详情

新增抽象位置识别函数，阻止验证阶段对尚未映射的占位符报错：

```python
def _is_abstract_position(pos_id: str) -> bool:
    """检查是否是抽象位置占位符（如 'Position 1'），PositionAgent 处理后会替换为真实点位 ID"""
    return bool(re.match(r'^Position\s+\d+$', pos_id or '', re.IGNORECASE))
```

**移动目标检查** 跳过抽象占位符（原来会报 error）：
```python
# 改造前：不在点位列表 → 报错
if dest and not scene.get_position(dest):
    errors.append(...)

# 改造后：抽象占位符跳过，PositionAgent 处理后才验证
if dest and not scene.get_position(dest) and not _is_abstract_position(dest):
    errors.append(...)
```

**camera_group 一致性检查** 在有抽象位置时整体跳过（否则会误报）：
```python
all_positions = [p.get("position", "") for p in segment.get("current position", [])]
has_abstract = any(_is_abstract_position(p) for p in all_positions)
if not has_abstract:
    _check_camera_group_consistency(segment, scene_idx, seg_idx, scene, errors)
```

---

## 五、`autogen_pipeline.py` 修改详情

### 5.1 循环上限统一调整

原始报告中审查循环为 2 轮、修复循环为 1 次，本次统一调整：

```python
# 改造前
MAX_REVIEW_ROUNDS = 2
MAX_FIX_ROUNDS = 1

# 改造后
MAX_REVIEW_ROUNDS = 3
MAX_FIX_ROUNDS = 3
MAX_POSITION_FIX_ROUNDS = 3   # 新增
```

### 5.2 Agent 初始化——新增 PositionAgent

```python
director = create_director_agent(characters, scene, resource_loader, required_character_count)
critic = create_critic_agent()
dialogue = create_dialogue_agent()
validator = create_validation_agent(resource_loader, scene) if model_supports_tools else None
position_agent = create_position_agent(scene)   # 新增

bridge.put_event({'type': 'log', 'level': 'success',
    'message': '✅ Agents 初始化完成（导演、批评家、对白专家、技术验证、位置映射）'})
```

### 5.3 新增阶段④：PositionAgent 位置映射

在原有 Stage ③（技术验证）之后、Stage ⑤（输出）之前插入：

```python
# ════════════════════════════════════════════════
# 阶段④：PositionAgent 位置映射
# ════════════════════════════════════════════════
for pos_round in range(MAX_POSITION_FIX_ROUNDS):
    mapping_prompt = (
        f"请将以下剧本中的抽象位置（Position 1/2/3...）映射到真实点位：\n\n"
        f"```json\n{json.dumps(draft_script, ensure_ascii=False, indent=2)}\n```"
    )
    mapped_script = None
    unresolved = []

    async for event in position_agent.on_messages_stream(
        [TextMessage(content=mapping_prompt, source="user")],
        cancellation_token=CancellationToken()
    ):
        if hasattr(event, 'chat_message') and event.chat_message:
            pos_raw_content = event.chat_message.content
            # 提取 POSITION_UNRESOLVED 声明这个wenwen
            unresolved = re.findall(r'POSITION_UNRESOLVED:\s*(.+)', pos_raw_content)
            mapped_script = _extract_json_from_text(pos_raw_content)

    if mapped_script and not unresolved:
        draft_script = mapped_script
        bridge.put_event({'type': 'log', 'level': 'success',
                          'message': '✅ [PositionAgent] 位置映射完成'})
        break

    if unresolved and pos_round < MAX_POSITION_FIX_ROUNDS - 1:
        # 反馈给 DirectorAgent：调整无法映射的位置的戏剧意图描述
        fix_prompt = (
            "以下站位在场景中找不到合理匹配，请修改剧本，"
            "调整这些位置的 position_descriptions（换用场景实际存在的空间特征描述）：\n\n"
            + "\n".join(f"- {u}" for u in unresolved)
            + f"\n\n当前剧本：\n```json\n{json.dumps(draft_script, ensure_ascii=False, indent=2)}\n```"
        )
        async for event in director.on_messages_stream(...):
            ...  # DirectorAgent 修改 position_descriptions，重试映射
```

### 5.4 新增阶段④b：坐标生成（可选）

调用 `position_agent_standalone.py` 生成 3D 坐标文件，缺少资源文件时静默跳过：

```python
# ════════════════════════════════════════════════
# 阶段④b：坐标生成（需要 scene_exports + position_templates 资源文件）
# ════════════════════════════════════════════════
position_filename = None
timestamp = int(time.time())
output_dir = Path('outputs')
output_dir.mkdir(exist_ok=True)
temp_script_path = None
try:
    # 将当前剧本写入临时文件（standalone 脚本只接受文件路径输入）
    temp_script_path = output_dir / f"_temp_script_{timestamp}.json"
    with open(temp_script_path, 'w', encoding='utf-8') as _f:
        json.dump(draft_script, _f, ensure_ascii=False, indent=2)

    # 在线程池中运行同步 subprocess，不阻塞 asyncio 事件循环
    pos_result = await asyncio.get_event_loop().run_in_executor(
        None, run_position_agent,
        str(temp_script_path), scene.id,
        str(output_dir), f"position_{timestamp}.json",
    )

    if pos_result.get("ok"):
        position_filename = f"position_{timestamp}.json"
    elif pos_result.get("skip"):
        # 缺少 scene_exports / position_templates，静默跳过
        bridge.put_event({'type': 'log', 'level': 'info',
            'message': f'⏭️  [PositionAgent] 跳过坐标生成（{pos_result.get("error")}）'})
    else:
        bridge.put_event({'type': 'log', 'level': 'warning',
            'message': f'⚠️  [PositionAgent] 坐标生成失败：{pos_result.get("error")}'})
finally:
    if temp_script_path and temp_script_path.exists():
        temp_script_path.unlink(missing_ok=True)  # 清理临时文件
```

### 5.5 成功事件新增 `position_filename` 字段

```python
# 改造前
bridge.put_event({
    'type': 'success',
    'filename': filename,
    'actors_profile_filename': actors_profile_filename,
    'warnings': ...
})

# 改造后（新增 position_filename，null 表示未生成，正常情况）
bridge.put_event({
    'type': 'success',
    'filename': filename,
    'actors_profile_filename': actors_profile_filename,
    'position_filename': position_filename,   # 新增
    'warnings': ...
})
```

---

## 六、新增文件：`position_agent_wrapper.py`

封装 `position_agent_standalone.py` 的 subprocess 调用，检查资源文件是否存在：

```python
_BACKEND_DIR = Path(__file__).parent.parent
RESOURCES_DIR = _BACKEND_DIR / "resources"
STANDALONE_PATH = _BACKEND_DIR / "position_agent_standalone.py"


def run_position_agent(script_path, scene_id, output_dir, output_filename) -> dict:
    scene_export  = RESOURCES_DIR / "scene_exports"      / f"{scene_id}.json"
    template_path = RESOURCES_DIR / "position_templates" / f"{scene_id}.json"

    # 缺少任一资源文件 → 静默跳过，不影响剧本生成流程
    if not scene_export.exists() or not template_path.exists():
        return {"ok": False, "skip": True,
                "error": f"缺少场景资源文件（scene_exports/{scene_id}.json 或 position_templates/{scene_id}.json）"}

    cmd = [
        sys.executable, str(STANDALONE_PATH),
        "--deepseek-api-key", os.getenv("API_KEY", ""),
        "--api-url",          os.getenv("BASE_URL", "").rstrip("/") + "/chat/completions",
        "--model",            os.getenv("MODEL", "deepseek-chat"),
        "--scene-export-path",        str(scene_export),
        "--script-file-path",         script_path,
        "--positions-template-path",  str(template_path),
        "--output-path",              str(Path(output_dir) / output_filename),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    stdout = result.stdout.strip()
    if stdout:
        try:
            return json.loads(stdout)   # standalone 成功时输出 {"ok": true, "output_path": "..."}
        except json.JSONDecodeError:
            pass
    return {"ok": False,
            "error": result.stderr.strip() or f"position_agent_standalone 退出码 {result.returncode}"}
```

> **注意**：`--api-url` 在原始版本中直接传 `BASE_URL`（如 `https://ark.cn-beijing.volces.com/api/coding/v3`），导致 standalone 脚本请求时收到 404。已修复为自动拼接 `/chat/completions`。

---

## 七、资源文件说明

`position_agent_standalone.py` 需要两个外部文件才能运行坐标生成阶段：

| 文件路径 | 来源 | 格式概述 |
|---------|------|---------|
| `backend/resources/scene_exports/{scene_id}.json` | Unity 导出 | 场景区域列表（regions）、每区域内的物体名称和 3D 坐标 |
| `backend/resources/position_templates/{scene_id}.json` | 预配置模板 | 每个点位（Position N）对应的 region、neartarget、looktarget |

**position template 格式**（`position_agent_standalone.py` 的输入和输出格式相同）：

```json
{
  "where": "Space Station",
  "center": [],
  "positions": {
    "Position 7": {
      "fixed_angle": [],
      "position": [],
      "sit_angle": [],
      "region": "太空舱1楼观察室",
      "neartarget": "Crate_01",
      "looktarget": ""
    },
    "Position 26": {
      "fixed_angle": [],
      "position": [],
      "sit_angle": [],
      "region": "太空舱2楼观察室",
      "neartarget": "Fence_Short_01",
      "looktarget": "巡逻单元 A09"
    }
  }
}
```

**填写规则**：
- `region` 和 `neartarget`：必填（AI 根据场景结构和戏剧意图选择）
- `looktarget`：仅当角色需要注视特定目标时填写
- `position`、`sit_angle`、`fixed_angle`：保持空数组，由游戏引擎在运行时计算

当前已提供 `SpaceStation` 场景的两个资源文件。其他场景需要 Unity 团队导出后放入对应目录，放入前坐标生成阶段自动跳过。

---

## 八、前端配置修复

`frontend/js/config.js` 中 `BASE_URL` 原本取当前页面的 origin（前端用 `python -m http.server 8080` 运行在 8080，API 请求也会打到 8080 导致 404）：

```javascript
// 改造前（动态取当前页面 origin，导致开发时 API 请求打到静态服务器）
BASE_URL: window.location.port
  ? `${window.location.protocol}//${window.location.hostname}:${window.location.port}`
  : `${window.location.protocol}//${window.location.hostname}`,

// 改造后（固定指向 Flask 后端端口）
BASE_URL: `${window.location.protocol}//${window.location.hostname}:5000`,
```

---

## 九、完整流水线（更新后）

```
① DirectorAgent
   输出：含 position_descriptions 的剧本 JSON（Position 1/2/3 抽象编号）
       ↓
② CriticAgent + DialogueAgent 审查循环（≤ 3 轮）
       ↓ 有问题 → DirectorAgent 修改
③ Python 技术约束验证（≤ 3 次自动修复）
   注：抽象位置在此阶段跳过点位合法性检查
       ↓
④ PositionAgent 位置映射（≤ 3 轮）
   输入：含 position_descriptions 的剧本
   输出：Position N → 真实点位 ID，删除 position_descriptions
       ↓ POSITION_UNRESOLVED → DirectorAgent 修改戏剧意图 → 重试
④b position_agent_standalone.py 坐标生成（可选）
   输入：剧本 + scene_exports/{id}.json + position_templates/{id}.json
   输出：position_{timestamp}.json（region / neartarget / looktarget）
       ↓ 缺少资源文件 → 静默跳过
⑤ OutputAgent
   输出：script_{timestamp}.json + actors_profile_{timestamp}.json
   成功事件新增 position_filename 字段
```

---

## 十、前端日志示例（新增阶段）

```
[info]    🤖 初始化多 Agent 系统...
[success] ✅ Agents 初始化完成（导演、批评家、对白专家、技术验证、位置映射）
...（① ② ③ 阶段同原始报告）...
[info]    📍 [PositionAgent] 开始位置映射...
[success] ✅ [PositionAgent] 位置映射完成
[info]    ⏭️  [PositionAgent] 跳过坐标生成（缺少场景资源文件）
          ← 或 →
[success] ✅ [PositionAgent] 坐标文件生成完成：position_1743420000.json
[info]    💾 正在生成最终 JSON 并保存文件...
[success] ✅ 已生成角色档案：3 位演员
```
