# Agent 提示词审计

生成格式的权威来源见 script_contract.md。提示词文件位于 backend/src/prompt_files，渲染/资源注入位于 backend/src/prompt_renderers。提示词只约束各 Agent 自己负责的产物，不让创意或摄影 Agent 重写整份剧本。

| Agent / 提示词 | 输出责任 | 资源与可选字段规则 |
|---|---|---|
| ConceptPitch / CharacterVoice / NarrativeArch | 自然语言创意、人物、结构讨论 | 沿用当前任务范围与注入角色/场景；不负责最终 schema、不得声称读取资源 |
| MeetingSummary | 会议简报对象 | 保留用户必须事件、场景和风格要求；不替导演选择资源名 |
| Treatment | 指定幕数的大纲 | 保留后续执行约束，不强制输出 script.json |
| ShotPlan | 镜号到幕的分配 | 每镜一次、保持顺序；不创作对白 |
| Director / Director_Direct / Director_Word | 导演中间稿 | 统一追加 script_contract_rules；注入动作、场景、角色与真实可选库；state 必需、数字时长、move 数组；Word 同样过最终门禁 |
| Critic / Dialogue | 文学/对白审查 | 追加共享规则；不把无说话人事件当缺对白，不擅改语言、情绪或交互字段；不以旧格式建议推翻合同 |
| Validation | 工具报告 | 替换旧冗长且冲突的硬编码说明，复用 _validate_spec 共享合同；保留结构化错误与空库警告 |
| CharacterGeneration | 角色中间档案 | 使用注入的 characters_resource 模型；真实库非空不得凭空命名；最终 actors_profile 由程序转换并校验 |
| 摄影 Stage1：shot_planning_combined_batch_system | 交互分析与画面描述 | 不改对白、动作、语言和交互；无说话人只补环境；显式 object 保留 |
| 摄影 Stage2：cinematography_position_grouping/planning | 分组、区域、锚点、朝向 | LayoutLib 与 scene_info 真实候选，覆盖移动目的地；Position 是逻辑槽；真实坐标由 CoordinateSkill 计算 |
| 摄影 Stage3：camera_planning_analysis_batch_system | 景别与过渡选择 | 注入 CameraLib；无移动不 follow；最终归一化 cut/blend/easein；失败重试带具体错误反馈 |
| Title | 片名对象 | 不接触剧本字段或取值资源，无须额外注入全合同 |
| stash / position_agent 等兼容路径 | 保留旧入口专用中间协议 | 不是最终导出依据；最终导出统一由共享合同阻断不合规结果 |

冲突处理：旧动作 state 禁止规则删除；旧 speaker/content 双空和 "5s" 协议改为用户确认版本；camera scene_index 最终改 shot_index；main script 摄影参数移至独立文件。新增专项文档明确 language_config 是整套可选扩展：缺失时兼容旧格式，存在时所有幕一致且每句译本完整。用户提供的 Pixar 文件已转为轻量枚举索引，旧示例 `pixar_emotion/build_to_second` 不再合法。语言标签校验是基本格式检查，不宣称已验证全球 BCP 47 注册表或 TTS 能力。

核验入口：`tests/test_prompt_files.py` 检查渲染动态输入与纯文本文件形式；`tests/test_script_contract.py` 验证合同、候选反馈和直接台词保护；`scripts/verify_contract_runs.py` 使用真实模型验证普通/直接两种生成路径。

另修正文学修改请求的字符串拼接：原先发送字面的 `{json.dumps(...)}` 而非当前剧本；现实际插入 JSON，并在修改请求中重新附上用户原始要求，避免只依赖摘要导致必需事件丢失。

角色引用规则：指定角色姓名是完整标识，不得删除空格或括号内编号。共享角色上下文明确要求 `who`、`speaker` 和所有 `character` 引用逐字保留；对白 `content` 内允许自然称呼，不因此改写台词。普通模式历史运行曾将“艾莉 (F-01)”缩为“艾莉”，触发 `REQUIRED_CHARACTERS`；新增提示词回归测试覆盖该约束，但仍须以真实生成结果确认效果。
