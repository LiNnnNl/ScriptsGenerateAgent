# Phase 4：PositionAgent 集成

## 背景

原架构中，DirectorAgent 直接读取 `scenes_resource.json` 中的真实点位 ID（如 `Pos_Window_01`），在生成剧本时同时承担编剧和走位两个职责。这导致：

- DirectorAgent 的 prompt 包含大量技术点位信息，干扰创作决策
- 走位受限于已有点位，演出效果服从于技术约束
- 无法在未来引入新点位时灵活扩展

本次改造将走位职责从 DirectorAgent 中剥离，由新增的 **PositionAgent** 专门负责点位映射。

---

## 架构变化

### 改造前

```
DirectorAgent（读取 scenes_resource.json，直接输出真实点位 ID）
    ↓
CriticAgent + DialogueAgent（审查）
    ↓
ValidationAgent（技术验证，含点位合法性 + camera_group 检查）
    ↓
OutputAgent（保存文件）
```

### 改造后

```
DirectorAgent（纯编剧，只输出 "Position 1/2/3" + 自然语言描述）
    ↓
CriticAgent + DialogueAgent（审查，循环 ≤3 轮）
    ↓
ValidationAgent（技术验证，跳过抽象位置的点位检查）
    ↓
PositionAgent（读取 scenes_resource.json，将抽象位置映射为真实点位 ID，循环 ≤3 轮）
    ↓ 若映射失败 → DirectorAgent 修改 position_descriptions → 重试
    ↓
position_agent_standalone.py（可选，生成 3D 坐标文件，缺少资源文件时静默跳过）
    ↓
OutputAgent（保存文件）
```

---

## 职责分工

| Agent | 职责 | 参考资源 |
|-------|------|---------|
| DirectorAgent | 纯编剧：剧情、对白、动作、走位意图 | `actions_resource.json` |
| PositionAgent | 位置映射：将抽象编号映射为真实点位 ID | `scenes_resource.json` |
| position_agent_standalone.py | 坐标生成：生成真实 3D 坐标文件 | `scene_exports/{scene_id}.json`、`position_templates/{scene_id}.json` |

---

## 数据流

### DirectorAgent 新输出格式

在每个场景对象顶层新增 `position_descriptions` 字段：

```json
[
  {
    "position_descriptions": {
      "Position 1": "近窗俯瞰，背靠星空，适合独白或凝望",
      "Position 2": "记录台旁，可坐可站，适合工作状态或低调观察"
    },
    "scene information": {
      "who": ["温迪", "遐蝶"],
      "where": "空间站隔离舱",
      "what": "温迪在星空前向遐蝶坦白秘密"
    },
    "initial position": [
      {"character": "温迪", "position": "Position 1"},
      {"character": "遐蝶", "position": "Position 2"}
    ],
    "scene": [...]
  }
]
```

### PositionAgent 输出格式

将所有 `Position N` 替换为真实点位 ID，并删除 `position_descriptions` 字段：

```json
[
  {
    "scene information": {...},
    "initial position": [
      {"character": "温迪", "position": "Pos_Window_Panorama"},
      {"character": "遐蝶", "position": "Pos_Console_Side"}
    ],
    "scene": [...]
  }
]
```

### 无法映射时的协议

若 PositionAgent 找不到合理匹配，在 JSON 之前声明：

```
POSITION_UNRESOLVED: Position 1 → 需要户外悬崖，但场景只有室内环境
```

Pipeline 检测到 `POSITION_UNRESOLVED` 后，将问题反馈给 DirectorAgent，请其修改对应位置的戏剧意图描述，再重试映射。

---

## 修改的文件

### `backend/src/autogen_agents.py`

**`build_director_system_message` 变化：**
- 移除：场景信息中的"可用点位"列表
- 移除：镜头分组信息（camera_groups）
- 修改：点 2 从"走位决策"改为"走位设计（以演出效果为唯一标准）"
- 新增：`position_descriptions` 字段的格式说明和示例
- 修改：字段规则中移除 camera_group 约束，改为 `position_descriptions` 覆盖性要求

**新增函数：**
- `build_position_agent_system_message(scene)` — 构建 PositionAgent 的 system prompt，包含真实点位列表、镜头分组信息、映射步骤、POSITION_UNRESOLVED 协议
- `create_position_agent(scene, model)` — PositionAgent 工厂函数

---

### `backend/src/autogen_tools.py`

新增辅助函数：

```python
def _is_abstract_position(pos_id: str) -> bool:
    return bool(re.match(r'^Position\s+\d+$', pos_id or '', re.IGNORECASE))
```

验证逻辑调整：
- **移动目标检查**：目标为 `Position N` 格式时跳过，不报错（PositionAgent 处理后才有真实 ID）
- **camera_group 一致性检查**：片段中有任意抽象位置时整体跳过，避免误报

---

### `backend/src/autogen_pipeline.py`

**循环上限统一调整为 3：**

```python
MAX_REVIEW_ROUNDS = 3
MAX_FIX_ROUNDS = 3
MAX_POSITION_FIX_ROUNDS = 3
```

**新增阶段 ④：PositionAgent 位置映射**

```
for pos_round in range(MAX_POSITION_FIX_ROUNDS):
    PositionAgent 处理剧本 → 提取 POSITION_UNRESOLVED
    if 映射成功 → 更新 draft_script，退出循环
    if POSITION_UNRESOLVED → DirectorAgent 修改 position_descriptions → 重试
    if JSON 解析失败 → 重试或跳过
```

**新增阶段 ④b：坐标生成（可选）**

- 将当前剧本写入临时文件
- 调用 `run_position_agent()` 在线程池执行 subprocess
- 成功 → `position_{timestamp}.json` 写入 `outputs/`
- 缺少资源文件 → 静默跳过，日志提示

**成功事件新增字段：**

```json
{
  "type": "success",
  "filename": "script_123456.json",
  "actors_profile_filename": "actors_profile_123456.json",
  "position_filename": "position_123456.json",
  "warnings": []
}
```
`position_filename` 为 `null` 表示未生成坐标文件（正常情况，不影响剧本使用）。

---

### `backend/src/position_agent_wrapper.py`（新文件）

封装 `position_agent_standalone.py` 的 subprocess 调用：

```python
def run_position_agent(script_path, scene_id, output_dir, output_filename) -> dict
```

返回值：
- `{"ok": True, "output_path": "..."}` — 成功
- `{"ok": False, "skip": True, "error": "..."}` — 缺少资源文件，静默跳过
- `{"ok": False, "error": "..."}` — 执行失败

资源文件路径约定：
- `backend/resources/scene_exports/{scene_id}.json` — Unity 导出的场景数据
- `backend/resources/position_templates/{scene_id}.json` — 点位坐标模板

---

## 新增资源目录

```
backend/resources/
├── scene_exports/          ← Unity 团队提供，每个场景一个 JSON
│   └── .gitkeep
└── position_templates/     ← Unity 团队提供，每个场景一个 JSON
    └── .gitkeep
```

这两个目录当前为空。`position_agent_standalone.py` 所需文件由 Unity 团队导出后放入，放入前坐标生成阶段会自动跳过，不影响剧本生成流程。

---

## 各循环上限说明

| 阶段 | 上限 | 超限行为 |
|------|------|---------|
| 审查循环（CriticAgent + DialogueAgent）| 3 轮 | 强制进入验证阶段，带 warning 输出 |
| 技术验证修复（DirectorAgent）| 3 次 | 强制使用当前版本，errors 以 warning 形式输出 |
| 位置映射（PositionAgent ↔ DirectorAgent）| 3 轮 | 使用当前最优结果（可能保留部分抽象位置）继续 |

---

## 注意事项

1. **position_descriptions 字段生命周期**：DirectorAgent 输出时存在 → PositionAgent 处理后删除。`validate_json_spec` 目前允许该字段（非强制字段），无需修改规范文件。

2. **camera_group 验证时机**：Stage ③ 验证阶段位置尚为抽象，跳过 camera_group 检查。PositionAgent 在映射时主动保证同一对白片段的角色落在同一镜头组内，作为主要保障。

3. **standalone 脚本不改动**：`position_agent_standalone.py` 完全保持原样，wrapper 通过命令行参数覆盖 API Key，无需修改脚本内的配置。

4. **时间戳一致性**：`timestamp` 在 Stage ④b 之前统一生成，`script_{timestamp}.json`、`actors_profile_{timestamp}.json`、`position_{timestamp}.json` 三个文件共享同一时间戳，便于关联。
