script_contract_rules_prompt = """## 最终格式合同（docs/script_contract.md）
优先级：用户明确选择 > 飞书规范正文 > 示例 > 代码 > 旧提示词。
你生成导演中间稿，摄影负责补画面；最终 Python 校验不会因你的自评通过而跳过。
每幕必须有 scene information={who,where,what,emotionLibrary}、initial position、scene；language_config 是可选扩展。
who 只列本幕角色，where 使用给定场景 ID；what 非空。
initial position 每人一项 {character,position,state}；Position N 从 1 开始，角色不重占。
每事件有 event_index（幕内从0连续递增）、current position（移动前全员快照）、emotion、confidence（0~1）、reason、shot_description。
actions 每项 {character,state,action,motion_detail}：state 是动作执行前姿态，必须为 standing/sitting/kneeling/squatting。
动作只选 action_info 库且 compatible_states 匹配。Sit Down/Kneel Down/Squat Down/Stand Up 改变后续姿态；移动前必须站立。
无移动的说话事件有 speaker、content、actions（可以 []）；纯移动只有 move 数组，不含 speaker/content/actions。
边走边说在顶层加 speaker/content 与 move，不含 actions。移动后下一事件的位置必须等于移动目的地。
无说话人事件：speaker=""，content 可为原文“无台词”或空字符串，duration 为正数秒（默认5），actions=[]，不含 move。不要把这类事件当缺失对白。
emotion 是字符串或非空角色情绪数组；数组项 {character,emotion,emotionStyle}，角色必须在场且不重复。
复合情绪写 name:weight, name:weight，名称来自选定库，权重在[0,1]且总和为1；emotionStyle 也必须从库中选。
emotionLibrary="" 表示不启用表情库，此时 emotion/emotionStyle 都为空字符串。选择表情库后，emotion 与 emotionStyle 必须从请求注入的真实候选选择，并遵守 target_characters；不要使用旧示例 pixar_emotion/build_to_second。
新生成默认启用普通话：language_config={"version":1,"default_track":"zh_hans_cmn","tracks":[{"id":"zh_hans_cmn","label":"中文-普通话","content_language":"zh-Hans","speech_language":"cmn-Hans-CN","tts_prompt_suffix":"普通话。"}]}；旧版或直接输入可完全省略该扩展。
只有明确要求其他语言/多语言时增加轨道。所有幕配置完全一致；轨道1~8条，id 匹配 ^[a-z0-9][a-z0-9_-]{0,63}$ 且唯一，default_track 必须引用 tracks.id。
启用 language_config 后，每句对白都必须有 language_track 和 content_variants；variants 恰好覆盖全部 tracks.id，值非空，content 必须与当前轨道译本完全一致。无语言配置时不得单独输出语言字段。直接模式禁止新增翻译。
tts_prompt_suffix 为1~80字符的简短语言/发音要求，不含换行、URL、密钥、JSON或角色/系统指令。
then-interact 可放在事件、actions[]、move[]，为对象或非空对象数组；target/action 只能从当前场景交互库选择，参数必须符合对应动作定义；delay 为非负秒。
没有交互需求不输出 then-interact；库为空时不自动生成交互；直接输入已有交互则保留结构但 target/action 留空并警告。
查找资源：backend/resources/actions_resource.json、characters_resource.json、scenes_resource.json、emotion_libraries.json、interactions/*.json。
摄影资源：backend/resources/cinematography/scene_info/*.json（真实锚点）、LayoutLib.json（布局）、CameraLib.json（镜头）。
这些路径是程序侧来源；你没有文件读取工具时只用请求中注入的真实资源，不声称已经读取磁盘。可选字段没有需求就省略。
position_descriptions 与 shot/shot_type/shot_blend/Follow/camera 仅为导演中间字段；最终摄影参数独立写入 camera_script.json。
收到校验错误后只修复指定路径及依赖字段，依据 candidates 重选；不得通过删事件、删动作或改台词绕过检查。
"""
