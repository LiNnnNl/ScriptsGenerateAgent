# 二、具体文件解读

> 本文保留早期整理内容供追溯；本次最终实现与用户确认的冲突决策以 [script_contract.md](script_contract.md) 为准。特别是动作前 state、数字秒时长、空库处理和独立镜头文件，不使用下面旧示例覆盖新合同。

## （1）script.json 剧本文件

```jsonc
[
  {
    "scene information": {
      "who": ["角色A", "角色B"], // 本幕出场角色，角色名需要和 actors_profile.json 对应
      "where": "SpaceStation", // 拍摄场景 ID，多幕剧本中每一幕可以使用不同场景
      "what": "本幕核心事件的一句话概述"
    },

    "initial position": [
      { "character": "角色A", "position": "Position 1" },
      { "character": "角色B", "position": "Position 2" }
      // 每个角色必须占用不同的 Position，不能多人共用同一个 Position
    ],

    "scene": [
      // =========================
      // 1. 说话事件
      // =========================
      {
        "event_index": 0, // 本幕内的事件索引，从 0 开始，按顺序递增
        "speaker": "角色A", // 说话人，一条事件目前只能有一个说话角色
        "content": "台词内容",
        "emotion": "sad", // 可选：normal、happy、sad、angry、surprised、disgusted、fear

        "current position": [
          { "character": "角色A", "position": "Position 1" },
          { "character": "角色B", "position": "Position 2" }
          // 记录这一事件开始时，当前所有在场角色的位置
        ],

        "actions": [
          {
            "character": "角色A",
            "state": "standing", // 角色当前状态，例如 standing、sitting
            "action": "Standing Angry 1", // 动作名称，需要从动作资源库中选择
            "motion_detail": "Body leans forward slightly, hands tense while speaking",
            // 英文动作细节描述，用于补充角色表演方式

            // 动作完成后触发交互，可选
            "then-interact": {
              "target": "Dice1", // 场景里可交互物体的名字
              "action": "Roll", // 该物体 Animator 的 Trigger 名，或自定义交互动作名
              "delay": 0.2 // 可选，交互触发后的等待时间
            }
          }
        ],

        // 事件级交互：该句台词播放完成后触发，可选
        "then-interact": {
          "target": "Timer1",
          "action": "start",
          "delay": 0.1
        },

        "confidence": 0.7, // 情绪判断置信度，范围为 0～1
        "reason": "情绪判断原因",
        "shot_description": "角色A位于前景说话，角色B在后景倾听。"
        // 镜头画面描述；具体 shot、shot_type、camera 等镜头参数保存在 camera_script.json
      },

      // =========================
      // 2. 基础移动事件
      // =========================
      {
        "event_index": 1,

        "current position": [
          { "character": "角色A", "position": "Position 1" },
          { "character": "角色B", "position": "Position 2" }
          // 移动事件中记录的是角色移动前的位置
        ],

        "move": [
          {
            "character": "角色A",
            "destination": "Position 3",

            // 角色移动到目的地后触发交互，可选
            "then-interact": {
              "target": "Door_Outside",
              "action": "Open",
              "delay": 0.2
            }
          }
          // move 可以包含多个对象，表示多个角色在同一事件中移动
        ],

        "emotion": "normal",
        "confidence": 0,
        "reason": "空台词",
        "shot_description": "角色A从左侧走向场景中央。"
        // 基础移动事件不需要 speaker、content 和 actions，走路动作由系统自动驱动
      },

      // =========================
      // 3. 边走边说事件
      // =========================
      {
        "event_index": 2,
        "speaker": "角色A", // 边走边说时必须填写真实角色名，不能填写 default
        "content": "跟紧一点，我们没多少时间。",
        "emotion": "normal",

        "current position": [
          { "character": "角色A", "position": "Position 3" },
          { "character": "角色B", "position": "Position 2" }
          // 仍然记录移动开始前的位置
        ],

        "move": [
          {
            "character": "角色A",
            "destination": "Position 4"
          }
        ],

        "confidence": 0.7,
        "reason": "情绪判断原因",
        "shot_description": "角色A边走边回头提醒角色B。"
        // 边走边说时，speaker 和 content 写在事件顶层，不要放进 move 或 actions
      }
    ]
  }
]
```

## （2）position_plan.json 点位规划文件

```jsonc
{
  "where": "SpaceStation", // 场景 ID，需要和剧本 scene information.where 对应

  "groups": [
    // 多人同框、对话或互动使用 groups
    {
      "group_id": "G1", // 分组编号
      "layout": "two_person", // 站位布局，从 LayoutLib.json 中选择
      "region": "高层主仓", // 角色所在区域，需要和 scene_info 中的 Region 对应

      "positions": [
        {
          "position_id": "Position 1", // 剧本中的逻辑 Position 编号
          "character": "角色A"
        },
        {
          "position_id": "Position 2",
          "character": "角色B"
        }
      ],

      "lookat": {
        "mode": "center" // 朝向模式，可选 center 或 target
      }
    }
  ],

  "singles": [
    // 没有和其他角色组成一组的单人站位使用 singles
    {
      "position_id": "Position 3",
      "character": "角色C",
      "region": "高层主仓",
      "neartarget": "Ipad", // 希望靠近的场景锚点名称
      "lookat": "center" // 角色朝向，可填写 center、其他 Position 或场景锚点
    }
  ]
}
```

`Position 1`、`Position 2` 等只是剧本里的逻辑站位编号，本身不带坐标。真实坐标由摄影指导根据场景锚点和 `LayoutLib.json` 自动计算，不再直接写在 `position_plan.json` 中。

## （3）position_detail.json 点位详情文件

```jsonc
{
  "where": "SpaceStation", // 场景 ID

  "groups": [
    // position_plan 中的多人分组会在这里展开成逐个 Position
    {
      "position_id": "Position 1",
      "group_id": "G1",
      "region": "高层主仓",
      "character": "角色A",
      "layout": "two_person",
      "lookat": "center"
    },
    {
      "position_id": "Position 2",
      "group_id": "G1",
      "region": "高层主仓",
      "character": "角色B",
      "layout": "two_person",
      "lookat": "center"
    }
  ],

  "singles": [
    {
      "position_id": "Position 3",
      "character": "角色C",
      "region": "高层主仓",
      "neartarget": "Ipad",
      "lookat": "center"
    }
  ]
}
```

规范字段名是 `singles`。部分旧的中间产物里出现过 `signals`，属于历史拼写，当前格式统一按 `singles` 使用。

## （4）camera_script.json 镜头文件

```jsonc
{
  "scenes": [
    {
      "scene_index": 0, // 镜头幕数，对应主剧本数组中的幕索引

      "events": [
        // 以一个 {} 为一条镜头事件，根据 event_index 和主剧本事件一一对应
        {
          "event_index": 0, // 事件索引，对应主剧本中的同一个 event_index
          "shot": "character", // 镜头类型，可选 character 或 scene
          // character：以角色为拍摄目标；scene：使用场景中预先设置的摄像机

          "target": "角色A", // 主要拍摄目标；移动事件通常取第一个移动角色
          "target_position": "Position 1", // target 在当前事件开始时所在的 Position

          "shot_type": "中近景",
          // 景别类型，从 CameraLib.json 中选择
          // 当前可选：全景、中景、中近景、近景、第一人称镜头、肩后镜头、侧跟镜头、环绕镜头、仰拍镜头、俯拍镜头

          "shot_blend": "cut",
          // 切镜方式，当前最终输出可选：cut、blend、easein

          "follow": 0, // 是否跟随目标，1 为跟随，0 为不跟随
          "camera": null,
          // character 镜头通常填 null；scene 镜头填写场景摄像机编号

          "shot_description": "角色A位于前景说话，角色B在后景倾听。",
          // 画面描述，由摄影指导生成；最终文件中必须是非空字符串

          "motion_enabled": true, // 是否启用镜头运动
          "motion_preset": "dialogue_push",
          // 运镜预设名称，对应 CameraLib.json 中当前景别的默认 MotionPreset
          // 不启用运镜时填写 none

          "play_motion_on_activate": true, // 激活镜头后是否立即开始运镜
          "motion_start_delay": 0.0, // 当前事件开始多少秒后启动运镜
          "motion_reset_on_replay": true // 重播时是否重置镜头位置
        },

        // scene 镜头示例
        {
          "event_index": 1,
          "shot": "scene",
          "target": "角色A",
          "target_position": "Position 1",
          "shot_type": "全景",
          "shot_blend": "blend",
          "follow": 1,
          "camera": 1, // scene 镜头使用场景摄像机编号
          "shot_description": "角色A从左侧走向场景中央。",
          "motion_enabled": false,
          "motion_preset": "none"
          // motion_enabled 为 false 时，不需要填写后面的三个运镜控制字段
        }
      ]
    }
  ]
}
```

旧格式里的 `motion_sequence` 数组当前已经不再输出，现在改为单个 `motion_preset`，具体运镜参数由 `CameraLib.json` 中的预设决定。

## （5）actors_profile.json 演员文件

这个文件影响最大的是 `name`、`gameobject_name` 和 `gender`，需要和剧本角色及 Unity 模型对应，否则下游加载角色时会报错。

```jsonc
[
  {
    "name": "角色A", // 角色姓名，需要和剧本中的 who、speaker、character 完全对应
    "age": 27, // 角色年龄；没有明确年龄时可以填 null
    "gender": "女", // 当前没有强制限定为 male/female，通常沿用角色资源中的值
    "gameobject_name": "F02_WithCamera", // 对应 Unity Hierarchy 面板中的模型名字

    "appearance": {
      // 角色外表，用于剧本生成阶段选角和角色参考
      "height": "175cm",
      "body_type": "身形修长匀称，姿态沉稳克制，举止中带有明显的守护者气质",
      "hair": "黑色长发高束，发型整洁利落，整体气质温雅而坚定",
      "face": "东方面容，神情平静柔和，目光中带有安抚人心的力量"
    },

    "acting_style": "语调平和冷静，措辞简洁但极具分量；在危急时刻会展现出不容置疑的决断力。",
    // 角色表演和语言风格，可供后续语音及表演阶段参考

    "traits": ["沉稳可靠", "守护欲强", "意志坚定"],
    "background": "来自中国的辐能者与治疗者型特工，擅长利用生命与能量相关能力支援队友、稳定战局。"
  }
]
```

角色名需要保持以下位置完全一致：

```text
actors_profile[].name
= script[].scene information.who[]
= script[].scene[].speaker
= script[].scene[].actions[].character
= script[].scene[].move[].character
```
