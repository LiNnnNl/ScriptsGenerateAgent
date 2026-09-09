# 最终剧本合同

规则依据：用户提供的飞书正文、`multilingual-script-json.md`、`pixar_emotion_curve_templates.json` 及本次明确选择。冲突优先级为用户选择 > 专项文档 > 飞书总览正文 > 示例 > 代码 > 旧提示词。本文描述最终导出，不把导演或摄影中间字段混入主剧本。实现：`backend/src/script_contract.py`；镜头结构：`backend/src/schema.py`。

## 字段与选择规则

完整示例见 [examples/script_all_fields.json](examples/script_all_fields.json)：以真实直接生成结果为基础补充事件级交互，覆盖全部主剧本字段。互斥事件字段分开放在不同事件，空库占位不是可执行交互资源；未替换为虚构动作或情绪。

顶层是非空数组，每项为一幕，数组长度等于请求 act_count。对象拒绝未知字段；可选意味着省略，不用 null 代替（camera 明确允许 null）。数字不接受布尔、NaN、Infinity。

| 路径 | 必需性 / 类型 | 内容与规则 |
|---|---|---|
| scene information | 必需 / 对象 | who、where、what、emotionLibrary 必需；language_config 可选 |
| └ who | 必需 / 字符串数组 | 本幕角色，不重复；跨幕汇总满足指定角色及总人数 |
| └ where | 必需 / 字符串 | scenes_resource.json 中真实场景 ID，由按幕场景分配写入 |
| └ what | 必需 / 非空字符串 | 本幕剧情概括 |
| └ emotionLibrary | 必需 / 字符串 | 空字符串表示禁用；启用时为 emotion_libraries.json 的 libraries 键，并检查适用角色 |
| └ language_config | 可选 / 对象 | 缺失时按旧版 speaker+content；启用后所有幕必须完全一致 |
| └ tracks[] | language_config 内必需 | 1~8 条；id 匹配 `^[a-z0-9][a-z0-9_-]{0,63}$` 且唯一；default_track 必须引用它 |
| └ tracks[].tts_prompt_suffix | 必需 / 1~80字符 | 仅简短语言/发音要求；禁止换行、URL、密钥、JSON、角色或系统指令 |
| initial position[] | 必需 / 数组 | character、position、state；恰好覆盖 who，同一快照不重占 |
| scene | 必需 / 非空数组 | 每项为事件，见下表 |
| event_index | 必需 / 整数 | 每幕从 0 连续递增 |
| current position[] | 必需 / 数组 | character、position，恰好覆盖 who；是事件开始、移动前的全员快照 |
| emotion | 必需 | 情绪字符串或非空数组；数组项为 character、emotion、emotionStyle，角色在场且不重复 |
| confidence / reason | 必需 | 0~1 数字 / 非空字符串；补默认值不意味着模型真实置信度 |
| shot_description | 必需 / 字符串 | 中间稿可空，最终必须有非空画面描述 |
| speaker / content | 条件必需 / 字符串 | 说话人为 who 成员、台词非空；无说话人事件 speaker=""，content 保留原文或“无台词” |
| actions[] | 非移动事件必需 | 可空；每项 character、state、action 必需，motion_detail 可选字符串，then-interact 可选 |
| move[] | 移动事件必需 | 非空；每项 character、destination，可选 then-interact；不与 actions 同时存在 |
| duration | 无说话人事件必需 | 正数秒，默认 5；旧输入 "3s" 可转换为 3 |
| language_track | 启用 language_config 后的说话事件必需 | 引用当前幕 tracks[].id；未启用语言配置时不得单独出现 |
| content_variants | 启用 language_config 后的说话事件必需 | 必须恰好覆盖全部 tracks.id 且译文非空；content 等于当前轨道译文 |
| then-interact | 可选 | 可在事件、actions[]、move[]；对象或非空对象数组 |

事件类型互斥：普通说话有 speaker/content/actions；纯移动只有 move（无 speaker/content/actions）；边走边说有 move/speaker/content（无 actions）；无说话人事件无 move，有空 speaker、字符串 content、duration、空 actions。

## 固定集合及资源

| 字段 | 允许来源 | 当前情况 / 需要准备 |
|---|---|---|
| initial position[].state、actions[].state | standing / sitting / kneeling / squatting | 已具备；动作 state 表示执行前姿态 |
| actions[].action | actions_resource.json，且符合 compatible_states | 已具备，按用户要求不整理动画资源 |
| position、destination | `^Position [1-9][0-9]*$` | 逻辑槽，不当作坐标；摄影输出须覆盖初始位置和移动目的地 |
| who/character/speaker | 本次剧本角色引用 | 已具备，不能引入未声明角色 |
| actors_profile.gameobject_name | characters_resource.json | 已具备真实模型名称 |
| emotionLibrary / emotion / emotionStyle | emotion_libraries.json | 已登记 `pixar_cartoon`：78 个模板、8 个切换样式；当前仅声明适用于阿福、陈阿嫲、林阿公 |
| then-interact.target/action/参数枚举 | interactions/<scene>.json | 目录已有，仅 .gitkeep；规划格式见下节 |
| camera_script.shot | character / scene / object | 已具备结构支持；object 需目标库 |
| shot_type / motion_preset | CameraLib.json 的景别及其 DefaultMotionPreset | 已有；关闭运镜固定 none，不输出 motion_sequence |
| camera_script.shot_blend / follow | cut / blend / easein；0 / 1 | 已有；摄影中间八种 blend 名称由程序归一化 |
| object 的 target/target_anchor | camera_targets/<scene>.json 的 objects/id/anchors | 尚未具备；不等同于坐标锚点或交互库 |
| region / neartarget | cinematography/scene_info/<where>.json | 已有场景的真实锚点；缺失场景问题按用户决定暂缓 |
| layout / lookat | LayoutLib.json；center/target 模式或目标引用 | 已有，检查人数范围、所属组/区域引用 |
| language_config.version | 1 | 已具备；轨道 ID 是自定义引用，不是全球固定库 |

动作按数组顺序推进姿态：Sit Down→sitting，Kneel Down→kneeling，Squat Down→squatting，Stand Up→standing；移动前必须 standing。移动结果写入下一事件快照，不覆盖本事件移动前快照。

选择 `pixar_cartoon` 后，情绪名必须使用模板完整键，例如 `sad_soft:0.25, crying_strong:0.75`；`emotionStyle` 只能从 `default/steady/quick_reaction/snap_reaction/slow_morph/delayed_realize/reaction_then_control/conflict` 选择。权重在 [0,1] 且总和为 1。`pixar_emotion`、`sad`、`crying`、`build_to_second` 都不是该实际库的合法值。未启用表情库时 emotion 与 emotionStyle 使用空字符串。

新生成默认启用下列单轨普通话配置；旧剧本或直接输入可以完全省略多语言扩展：

```json
{"version":1,"default_track":"zh_hans_cmn","tracks":[{"id":"zh_hans_cmn","label":"中文-普通话","content_language":"zh-Hans","speech_language":"cmn-Hans-CN","tts_prompt_suffix":"普通话。"}]}
```

## 可交互对象库规划（不创建虚构资源）

每个场景一个文件 `backend/resources/interactions/<scene>.json`。对象 id 必须与 Unity 注册标识一致；动作 name 必须是对象实际支持的能力。参数目前支持 number/integer/boolean/string，可定义 required、min、max、enum。delay 为非负秒，不放进 parameters 再定义。

下面只是结构示例，不能直接作为生产资源；先核实对象和能力再注册：

```json
{"scene":"Auditorium","objects":[{"id":"EXAMPLE_DOOR","actions":[{"name":"open","parameters":{"speed":{"type":"number","required":false,"min":0.1,"max":2}}}]}]}
```

表情生成端索引已由用户提供的 Pixar 曲线清单整理到 `backend/resources/emotion_libraries.json`；曲线参数和 CSV 仍以 Unity 资产为权威，不复制到提示词。物体镜头目录结构：`{"scene":"Auditorium","objects":[{"id":"EXAMPLE_DOOR","anchors":["center"]}]}`。

没有需求就不生成交互、多语言、物体镜头。启用多语言后，每句必须一次性具备全部已发布轨道；生成失败不能发布半条轨道。空库时直接输入已有交互会保留结构，但 target/action 清空并警告；其他交互参数只能在真实库就绪后验证能力，不应执行占位交互。

生成端当前没有 TTS 服务能力表和角色声音绑定，因此只能校验剧本文本合同，不能证明某个 `speech_language` 可播。正式启用多语种配音前还需提供：支持的文字/发音代码、默认轨道、每角色每轨道的 voice/provider/model、服务模型能力表、文本/声音/服务回退策略，以及是否记录实际 provider/model/voice。

## 发布门禁与兼容调整

- 主流程在技术验证、补全后及摄影后使用共享合同；导演最多修复三次，重复同类错误中止。修复不得改对白、事件数量或删动作绕过检查。
- 先写 outputs/.pending/<run>，剧本、镜头、演员及全部幕位置文件校验通过后才移动到 outputs，并发 success；失败保留暂存诊断，不发布成功结果。
- Word 剧本导出复用主合同；编辑器下载先请求 `/api/validate_script`，不再直接下载未经校验的编辑结果。
- 最终主剧本剥离 shot/shot_type/Follow/camera 等摄影中间参数；独立 camera_script.scenes[].shot_index 对应幕索引。多幕位置文件为 `{"scenes":[...]}`，单幕保留旧对象形态。
- 文档示例语法错误仅修正 JSON 语法；state 采用原文动作前状态；无说话人按用户选择允许“无台词”，数字时长；运镜按正文单预设，舍弃矛盾的 motion_sequence 示例。
- 校验能证明结构、取值与引用一致性，不能证明动画素材兼容、Unity 场景可达性、配音质量或故事美学。Unity 对接项见 unity_contract_changes.md。
