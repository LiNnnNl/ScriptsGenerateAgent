# 场景脚本JSON格式规范文档

## 1. 文件整体结构

```json
[
  {
    "scene information": { ... },
    "scene": [ ... ]
  }
]
```

根结构是一个**数组**，包含一个或多个场景对象。

---

## 2. Scene Information (场景元信息)

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `who` | Array<string> | 是 | 参与该场景的所有角色名称列表 |
| `where` | string | 是 | 场景发生的地点 |
| `what` | string | 是 | 场景概述，简要描述场景的核心事件 |

### 示例

```json
{
  "scene information": {
    "who": [
      "胡一号",
      "澜二号",
      "守三号",
      "弦四号",
      "韩知远",
      "林曦",
      "周可宁"
    ],
    "where": "Space Station – Isolation Facility 1F",
    "what": "一楼三十人类隔置于玻璃舱中,机器人之间围绕"是否应该救人"出现首次重大分裂,并展开秘密讨论。"
  }
}
```

---

## 3. Scene (场景序列)

场景序列是一个**数组**，包含多个场景片段。每个片段可以是以下类型之一：

### 3.1 对白场景 (Dialogue Scene)

#### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `speaker` | string | 是 | 说话者名称，或"default"表示旁白 |
| `content` | string | 是 | 对白/旁白内容 |
| `shot` | string | 是 | 镜头类型: "scene"(场景镜头) 或 "character"(角色镜头) |
| `camera` | number | 否 | 摄像机编号 |
| `shot_anchors` | Array<string> | 否 | 镜头锚点，如["Front"]表示正面 |
| `actions` | Array<Action> | 是 | 角色动作列表 |
| `current position` | Array<Position> | 是 | 当前所有角色的位置 |
| `motion_description` | string | 否 | 整体运镜/氛围描述 |

#### Action 对象结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `character` | string | 执行动作的角色名 |
| `state` | string | 角色状态，如"standing"、"sitting" |
| `action` | string | 具体动作名称 |
| `motion_detail` | string | 动作细节描述 |

#### Position 对象结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `character` | string | 角色名 |
| `position` | string | 位置标识，如"Position 7" |

#### 示例 1: 角色对白

```json
{
  "speaker": "胡一号",
  "content": "倒计时剩余四十七小时。若不采取干预,他们将进入终端处置区,流程不可逆。",
  "shot": "character",
  "shot_anchors": ["Front"],
  "actions": [
    {
      "character": "胡一号",
      "state": "standing",
      "action": "Standing Talking 6",
      "motion_detail": "Hu Tao gestures playfully while proposing her idea with a mischievous smile"
    },
    {
      "character": "澜二号",
      "state": "standing",
      "action": "Standing Thinking",
      "motion_detail": ""
    }
  ],
  "current position": [
    {
      "character": "胡一号",
      "position": "Position 7"
    },
    {
      "character": "澜二号",
      "position": "Position 8"
    },
    {
      "character": "守三号",
      "position": "Position 10"
    }
  ],
  "motion_description": "胡一号语气平稳,却像对现实的直接宣判。"
}
```

#### 示例 2: 旁白叙述

```json
{
  "speaker": "default",
  "content": "在人类文明崩溃后的第五十年,感染扩散失控,幸存者被送往轨道隔离站并进入自动处置系统。",
  "shot": "scene",
  "camera": 1,
  "actions": [
    {
      "character": "胡一号",
      "state": "standing",
      "action": "Standing Thinking",
      "motion_detail": ""
    },
    {
      "character": "韩知远",
      "state": "standing",
      "action": "Standing Puzzled",
      "motion_detail": ""
    }
  ],
  "current position": [
    {
      "character": "胡一号",
      "position": "Position 7"
    },
    {
      "character": "韩知远",
      "position": "Position 18"
    }
  ],
  "motion_description": "太空舱外部冰冷寂静,像在看守最后的生命残影。"
}
```

---

### 3.2 移动场景 (Movement Scene)

#### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `move` | Array<Movement> | 是 | 角色移动列表 |
| `shot` | string | 是 | 镜头类型 |
| `camera` | number | 否 | 摄像机编号 |
| `current position` | Array<Position> | 是 | 移动前的角色位置 |

#### Movement 对象结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `character` | string | 移动的角色名 |
| `destination` | string | 目标位置 |

#### 示例 1: 单个角色移动

```json
{
  "move": [
    {
      "character": "守三号",
      "destination": "Position 17"
    }
  ],
  "shot": "scene",
  "camera": 4,
  "current position": [
    {
      "character": "胡一号",
      "position": "Position 7"
    },
    {
      "character": "守三号",
      "position": "Position 10"
    }
  ]
}
```

#### 示例 2: 多个角色同时移动

```json
{
  "move": [
    {
      "character": "胡一号",
      "destination": "Position 7"
    },
    {
      "character": "澜二号",
      "destination": "Position 8"
    },
    {
      "character": "弦四号",
      "destination": "Position 9"
    }
  ],
  "shot": "scene",
  "camera": 4,
  "current position": [
    {
      "character": "胡一号",
      "position": "Position 11"
    },
    {
      "character": "澜二号",
      "position": "Position 12"
    },
    {
      "character": "弦四号",
      "position": "Position 13"
    }
  ]
}
```

---

### 3.3 纯场景描述 (Pure Scene Description)

用于过渡或氛围营造，不包含对白。

#### 示例

```json
{
  "speaker": "default",
  "content": "守三号离开巡检区后,一楼走廊陷入压抑的静止。剩下的三台机器人第一次切换到非公开频道——系统不允许的频道。",
  "shot": "scene",
  "camera": 4,
  "actions": [],
  "current position": [
    {
      "character": "胡一号",
      "position": "Position 7"
    },
    {
      "character": "守三号",
      "position": "Position 17"
    }
  ],
  "motion_description": "空气像拉紧的金属线,暗示这场对话不该存在。"
}
```

---

## 4. 常用动作库参考

### Standing 系列
- `Standing Thinking` - 站立思考
- `Standing Speech 1/2/3/4` - 站立演讲(不同姿态)
- `Standing Talking 1-8` - 站立交谈(不同姿态)
- `Standing Puzzled` - 站立困惑
- `Standing Angry 1/2/3/4` - 站立愤怒
- `Standing Agree 1/2` - 站立同意
- `Standing happy` - 站立开心
- `Standing crying` - 站立哭泣
- `Standing depressed` - 站立沮丧
- `Standing shaking head` - 站立摇头
- `Standing Arguing 1/2` - 站立争论

---

## 5. 镜头类型说明

| 类型 | 说明 | 适用场景 |
|------|------|----------|
| `scene` | 场景镜头 | 展示整体环境、多人场景 |
| `character` | 角色镜头 | 聚焦单个或少数角色的特写 |

---

## 6. 完整场景示例

```json
[
  {
    "scene information": {
      "who": ["艾丽丝", "鲍勃", "查理"],
      "where": "会议室A",
      "what": "三人讨论项目方案,出现分歧"
    },
    "scene": [
      {
        "speaker": "default",
        "content": "下午三点,阳光透过百叶窗洒在会议桌上。",
        "shot": "scene",
        "camera": 1,
        "actions": [
          {
            "character": "艾丽丝",
            "state": "sitting",
            "action": "Sitting Reading",
            "motion_detail": ""
          }
        ],
        "current position": [
          {
            "character": "艾丽丝",
            "position": "Position 1"
          }
        ],
        "motion_description": "宁静的下午,即将被打破。"
      },
      {
        "move": [
          {
            "character": "鲍勃",
            "destination": "Position 2"
          },
          {
            "character": "查理",
            "destination": "Position 3"
          }
        ],
        "shot": "scene",
        "camera": 2,
        "current position": [
          {
            "character": "鲍勃",
            "position": "Position 5"
          },
          {
            "character": "查理",
            "position": "Position 6"
          }
        ]
      },
      {
        "speaker": "艾丽丝",
        "content": "我认为我们应该采用方案A,风险更低。",
        "shot": "character",
        "shot_anchors": ["Front"],
        "actions": [
          {
            "character": "艾丽丝",
            "state": "sitting",
            "action": "Sitting Speech 1",
            "motion_detail": "自信地陈述观点"
          },
          {
            "character": "鲍勃",
            "state": "sitting",
            "action": "Sitting Thinking",
            "motion_detail": ""
          }
        ],
        "current position": [
          {
            "character": "艾丽丝",
            "position": "Position 1"
          },
          {
            "character": "鲍勃",
            "position": "Position 2"
          },
          {
            "character": "查理",
            "position": "Position 3"
          }
        ],
        "motion_description": "艾丽丝的声音打破了沉默。"
      }
    ]
  }
]
```

---

## 7. 最佳实践建议

1. **位置一致性**: 确保角色位置在场景间的连续性
2. **动作合理性**: 选择符合角色状态的动作
3. **镜头节奏**: 合理搭配scene和character镜头
4. **氛围描述**: 使用motion_description增强场景感染力
5. **对白自然**: content应符合角色性格和场景氛围

---

## 8. 注意事项

- 所有角色名必须在`scene information.who`中预先声明
- `current position`应包含所有在场角色
- 移动场景的destination必须是有效的Position标识
- speaker为"default"时表示旁白,不应出现在角色列表中
- 同一场景中的camera编号应保持一致性