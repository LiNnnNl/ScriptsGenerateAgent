# Unity 对接清单（本次未修改 Unity）

核对对象为 `D:/Code/XMU_FILM_code` 的源码。本次只实现生成端，不把 Python 校验通过当作 Unity 播放验收。

| 项目 | 当前 Unity 依据 | 需要对接 |
|---|---|---|
| 命名情绪库、复合权重、emotionStyle | Scripts/ScriptExecute/EventExecutionModule.cs 的 TryParseEmotionAssignment 读取 character/emotion 并将整体当名称；用户提供的 Pixar 文件定义了 78 个模板和 8 个切换样式 | 读取幕 emotionLibrary；按逗号拆分复合权重并分别查模板；实现 `default/steady/quick_reaction/snap_reaction/slow_morph/delayed_realize/reaction_then_control/conflict`。不要将整串权重当单一名称 |
| 语言轨道 | Scripts 下未找到 language_config/content_variants 的消费代码 | language_config 缺失时走旧逻辑；存在时要求所有幕一致、每句译本完整，再按 language_track 选文本和发音。配音请求传 speech_language/tts_prompt_suffix；缓存键包含轨道 |
| 幕索引 | Scripts/ScriptExecute/EventExecutionModule.cs、CameraControlModule.cs 使用 scene_index；Camera/CameraCreator.cs 亦引用旧键 | 读 shot_index，迁移期兼容 scene_index；按幕+event_index 对齐，不能仅依赖数组顺序 |
| 多幕位置文件 | Scripts/PositionStage 中现有位置生成/加载流程 | 新多幕文件外层 scenes 数组，逐幕按 where 选数据；保留单幕旧对象兼容。需要验证每幕切换和移动目的地加载 |
| then-interact | Scripts/Interact/InteractionManager.cs 与 EventExecutionModule.cs 已有事件、动作和移动后的交互入口 | 不是重做交互系统；从 Unity 已注册对象导出 id、支持 action 和参数约束，与生成端目录一致；空 target/action 应跳过并警告，不执行占位交互 |
| 动作前 state、无说话人时长 | 既有动作及事件执行模块 | state 是前置状态而非瞬间改为后置状态；speaker 为空不调用 TTS，即使 content="无台词"；duration 按数值秒等待 |
| 物体镜头 | 已有 object 路径及目标逻辑 | 导出可拍摄对象/锚点库；与 camera_targets 共享真实标识。空库输出空目标只表示待配置，不能直接拍摄 |

## 新字段示例输入

下列为协议片段，不是可直接导入的完整剧本。表情值来自用户提供的真实 `pixar_cartoon` 索引，但该文件的 targetCharacters 只声明阿福、陈阿嫲、林阿公。

```json
{
  "scene information": {
    "who": ["阿福"], "where": "Auditorium", "what": "告别",
    "emotionLibrary": "pixar_cartoon",
    "language_config": {
      "version": 1, "default_track": "zh_hans_cmn",
      "tracks": [
        {"id":"zh_hans_cmn","label":"中文-普通话","content_language":"zh-Hans","speech_language":"cmn-Hans-CN","tts_prompt_suffix":"普通话。"},
        {"id":"en_us","label":"English","content_language":"en","speech_language":"en-US","tts_prompt_suffix":"英语。"}
      ]
    }
  },
  "initial position": [{"character":"阿福","position":"Position 1","state":"standing"}],
  "scene": [{
    "event_index":0,"speaker":"阿福","content":"再见。",
    "language_track":"zh_hans_cmn","content_variants":{"zh_hans_cmn":"再见。","en_us":"Goodbye."},
    "emotion":[{"character":"阿福","emotion":"sad_soft:0.25, crying_strong:0.75","emotionStyle":"conflict"}],
    "confidence":0.8,"reason":"告别时情绪逐渐增强。","shot_description":"角色甲望向门口。",
    "actions":[],"current position":[{"character":"阿福","position":"Position 1"}]
  }]
}
```

无说话人事件示例：

```json
{"event_index":1,"speaker":"","content":"无台词","duration":3,"actions":[],"emotion":"","confidence":0,"reason":"环境停顿。","shot_description":"礼堂走道空旷。","current position":[{"character":"阿福","position":"Position 1"}]}
```

交互能力注册后可在事件、动作或移动项加 `"then-interact":{"target":"EXAMPLE_DOOR","action":"open","delay":0.2,"speed":0.5}`；尚未注册时不生成此行为。TTS 上线还需 Unity/服务端提供角色声音绑定与模型语言能力表。完整实际生成文件和校验报告保存在 `test_outputs/contract_acceptance/`。
