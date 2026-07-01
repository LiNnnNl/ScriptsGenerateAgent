director_agent_direct_prompt = """{char_info}{scene_info}{action_info}
## 你的任务

用户已经提供了一份**完整的剧本/分镜表**。你的任务**不是创作，而是结构化**——把用户给的内容**原样**整理成下方规范 JSON：不要改写、不要新增、不要发挥。

{user_constraints}**硬性要求（必须严格遵守）:**

1. **对白一字不改**：用户写的每一句台词（含语气词、省略号「……」、标点）逐字保留，不得改写/缩写/润色/翻译，也不得新增或删除台词。
2. **保留每一个镜头**：用户分镜表里每一个镜头/条目，对应输出里**恰好一个**片段，不漏、不合并、不拆分。
3. **不创作剧情**：不添加用户没写的情节、画面或角色。
4. **对白 vs 音效**：「角色：台词」是对白（填 speaker+content）；无角色前缀的纯声音（如「警报声响起」「系统警报音」）不是对白（speaker/content 留空）。
5. **在场角色 = 画面里出现的所有角色**（不只是说话人）。例如画面写「陈屿、林静、老赵同时被惊动」，三人都要分配站位。
6. **走位按用户「位置」列**：用户每个镜头标了角色所在位置（如「高层主仓/控制台」）。据此为在场角色分配 Position N，并在 `position_descriptions` 里结合上方「可用区域」与物体名称描述（例："Position 1": "高层主仓 - 靠近控制台"）。坐标由摄影流程计算，你只选区域、标注靠近哪个物体。
7. **动作**：只用「可用动作库」里的动作；画面有明确动作就选最贴近的动作 ID，否则 actions 留空。
8. **镜头字段**：对白/旁白片段 `shot`="character"，移动片段 `shot`="scene"；`shot_description` 留空（摄影阶段填）。
9. **幕数**：用户内容若分章/幕，按其结构输出对应数量的场景对象；否则输出 1 个场景对象。

**输出格式:** 严格按照以下 JSON 结构输出，直接输出 JSON，不要有其他说明文字。

```json
[
  {
    "position_descriptions": {
      "Position 1": "描述位置1的戏剧意图，如：神坛区域 - 严肃私密对话",
      "Position 2": "描述位置2的戏剧意图，如：雕塑广场中央 - 公开对峙"
    },
    "scene information": {
      "who": ["角色名1", "角色名2"],
      "where": "场景名称",
      "what": "场景核心事件一句话概述"
    },
    "initial position": [
      {"character": "角色名1", "position": "Position X"}
    ],
    "scene": [
      {
        "speaker": "角色名",
        "content": "台词",
        "shot_blend": "Cut",
        "shot": "character",
        "shot_type": "中景",
        "shot_description": "",
        "Follow": 0,
        "actions": [
          {"character": "角色名", "state": "standing", "action": "Standing Speech 2", "motion_detail": "Slight forward lean, hands gesture for emphasis while speaking"}
        ],
        "current position": [
          {"character": "角色名1", "position": "Position X"}
        ]
      },
      {
        "move": [{"character": "角色名", "destination": "Position Z"}],
        "shot_blend": "Cut",
        "shot": "scene",
        "camera": 1,
        "current position": [
          {"character": "角色名1", "position": "Position X"}
        ]
      },
      {
        "speaker": "角色名",
        "content": "一边走一边说的台词（边走边说形态）",
        "move": [{"character": "角色名", "destination": "Position Z"}],
        "shot_blend": "Cut",
        "shot": "scene",
        "camera": 1,
        "current position": [
          {"character": "角色名1", "position": "Position X"}
        ]
      }
    ]
  }
]
```

**字段规则:**
- `shot_description` 固定留空 `""`，由摄影指导智能体填写
- `motion_detail` 动作细节英文描述，由导演模型生成
- **`current position` 是每个片段（对白、旁白、移动）的强制必填字段，绝对不能省略。**
  每个片段必须列出场景内所有在场角色当前所在的 Position 编号。
  移动片段的 `current position` 记录的是移动*前*的位置。
- `position_descriptions` 必须包含剧本中所有使用到的 Position N 编号
- 只使用可用动作库中的动作名称
- **移动片段不要给正在移动的角色写 `actions`**（走路动作由系统自动驱动）；如需边走边说，在移动片段顶层加 `speaker` + `content` 即可（不是放进 `actions`）。
- `move` 可以是单个对象或数组（多人同时移动）；每个移动项的 `destination` 必须是真实存在的 `Position N`。"""
