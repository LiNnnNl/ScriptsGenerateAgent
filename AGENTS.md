# ScriptsGenerateAgent — 项目规则

> 仅放本项目专属约定。通用规则（语言、协作态度、编码风格、提交格式）见全局 `~/.Codex/AGENTS.md`，此处不重复。

## 项目概述

- **是什么**：多 Agent 驱动的剧本生成系统。用户给定场景/角色/创作灵感/幕数，经多个 LLM Agent 协作（创意会议 → 分场大纲 → 导演起草 → 文学审查 → 摄影指导），产出供下游 Unity 使用的结构化资产：剧本 JSON、镜头脚本、角色档案、站位与坐标。
- **两种模式**：正常生成（AI 从头脑风暴创作）/ 直接生成 `direct_mode`（用户粘剧本，只结构化不创作）。
- **核心流程**：`backend/src/autogen_pipeline.py: run_autogen_pipeline` 编排全程，经 NDJSON 流回传；摄影后处理在 `backend/src/cinematography/`（逐幕三阶段，算出角色坐标），摄影完成后由 `backend/src/performance/` 的动作/表情专项 Agent 按最终画面描述选择资源。
- **Agent 模块边界**：每个 AutoGen Agent 均位于 `backend/src/agents/<agent_name>/`，包内拥有自身提示词和创建入口；`autogen_agents.py` 只保留模型路由、工具/资源注入与 PositionAgent 旧接口兼容，Pipeline 的阶段顺序、重试和最终合同仍归总体框架。
- **可选人物模块**：请求字段 `enable_character_module` 只有显式 JSON `true` 才启用，前端默认关闭。关闭时跳过 CharacterVoiceAgent、CriticAgent、DialogueAgent、RevisionAgent，只运行 8 Agent 核心流程；开启时须先生成或导入人物档案。
- 技术栈：Flask API 后端 + 原生 JS 静态前端（无框架）。
- 后端入口：`backend/app.py`，跑 `uv run python backend/app.py`，服务在 `:5001`；提供 `/api/*`，也可在 `/script/*` 下托管前端静态文件，debug 热重载。
- 前端：`frontend/`（`index.html` + `js/{api,config,main,ui}.js` + `css/style.css`），无构建步骤；本地用静态服务打开，例如在 `frontend/` 下跑 `python3 -m http.server 8080`。
- git：远程 `LiNnnNl/ScriptsGenerateAgent`；实际功能分支与上游关系以 `git status -sb` / `git branch -vv` 为准。

> **完整架构 / 数据流 / 模块职责 / 多场景改造计划见 [`PROJECT_OVERVIEW.md`](./PROJECT_OVERVIEW.md)**（给人看的详细概述）。改了架构后两份文档都要同步更新。

## 验证命令（改完必跑）

- 前端 JS：`node --check frontend/js/<file>.js`
- 后端 Python：`python3 -m py_compile backend/src/<file>.py`
- 配置 JSON：`python3 -m json.tool <file>.json > /dev/null`

## 关键架构事实（动代码前必须记住）

- **位置与物体镜头权威数据源**：`backend/resources/cinematography/scene_info/*.json` 的 anchors/scene_markers 是**带真实 x/y/z 坐标的物品锚点**，为唯一权威；其中 `scene_markers.name` 同时作为该场景合法的物体镜头 `target`。`scenes_resource.json` 的 `valid_positions`（Position 1~N）是**无坐标的逻辑槽**，旧版，仅作导演点位菜单 + 同框约束 + 校验。
- **坐标全部由摄影算**：角色站位 x/y/z 由摄影 Stage2（`CinematographyPositionStage`，分组→规划 region+neartarget→`CoordinateSkill` 用锚点坐标 + `LayoutLib.json` 按人数选站位方式）计算得出。Position N 本身不带坐标。
- **摄影包边界**：`backend/src/cinematography/positioning/` 独立拥有点位 Stage2、坐标逻辑及 `rules/`；`backend/src/cinematography/camera/` 独立拥有镜头 Stage1/3 及 `rules/`。`cinematography/__init__.py` 只保留阶段编排、最终校验和发布，旧模块路径只作兼容导入。
- **表演包边界**：`backend/src/performance/action/` 与 `expression/` 分别拥有动作、表情的输入裁剪、提示词、资源校验和安全回填；上层 `performance/__init__.py` 只编排顺序。动作 Agent 只接收动作字段与自然语言剧情/画面描述，表情 Agent 只接收表情字段与相同依据；两者不得修改对白、镜头、站位或事件结构。
- **direct_mode（直接生成）**：用户粘已写好的剧本时，跳过头脑风暴/对白补写，由 `DirectorAgent_Direct` 做"结构化不创作"——对白逐字保留、保留每个镜头、按用户「位置」分配 Position N。实现在 `autogen_pipeline.py` 的 `_build_direct_draft` 与 `autogen_agents.py` 的 `build_director_system_message(..., direct_mode=True)`。
- **无说话人协议**：无 move 且 `speaker=""`；content 保留原文（允许“无台词”），`duration` 为正数秒默认 5，`actions=[]`。普通空镜的中间 shot=scene；摄影参数仅进入独立 camera_script。统一识别见 scene_segments.py，文学审查跳过、摄影不分配人物镜头。
- **最终合同**：`backend/src/script_contract.py` 强制字段、动作前 state、资源候选及跨文件引用。规则见 `docs/script_contract.md`。先暂存于 outputs/.pending，通过后才发布；空库名称留空并报 RESOURCE_LIBRARY_EMPTY，禁止假造资源。camera_script 使用 shot_index，多幕位置文件使用 scenes 数组。
- 1 幕 = 输出 JSON 数组的 1 个 scene_obj（`autogen_pipeline.py` 数组长度严格 = act_count）；`scene_pool` + `act_scenes` 支持逐幕场景，缺省时兼容单 `scene_id`。
- **导演精简中间稿**：Director 不输出 `event_index`、全员 `current position`、情绪置信度/理由、语言副本和空 `shot_description`；这些字段由 `_normalize_direct_scene` / `normalize_script` 确定性补齐。最终合同和非导演资源库不再注入 Director prompt。
- **Story IR 与有界生成**：正常模式先冻结带稳定 `event_id` 的精简 Story IR；编号分镜由代码冻结镜头边界和对白原文，再由 StoryIRAgent 识别同镜头内的对白/动作/移动组合；自由创作由 StoryIRAgent 按预分配 ID 分批生成。Director 每次只接收最多 8 个已冻结事件，输出必须与 ID 一一对应，合并后由代码移除临时 ID 并补运行时字段。TitleAgent、MeetingSummaryAgent、StoryIRAgent 和 ShotPlanAgent 使用 `SIMPLE_MODEL`；其余 Agent 使用 `MODEL`。方舟上的结构化 JSON Agent 默认 `thinking.type=disabled`（可用 `STRUCTURED_MODEL_THINKING` 覆盖）；每次调用清空上下文，文学返修最多返回 6 个内容补丁，合同返修只发送错误事件/幕元数据。
- **导演异常恢复**：正文为空但 reasoning channel 整体就是合法 JSON 时，将其作为候选结果继续同一套严格校验；`finish_reason=length` 前缀续写最多 3 次并严格解析拼接，禁止补括号冒充完整结果。两者都不是长剧本正常主流程。

## auto-mode / 改动风险三级约定

本项目统一遵守以下风险分级：

- 🟢 **可放手改（前端展示层）**：`frontend/**` 的 js/css/html。可逆、影响面小。
- 🟡 **改前先说清、git 写要确认（后端核心）**：`backend/**`（pipeline、cinematography 坐标逻辑、`backend/app.py`）；`git add/commit/push`、装依赖、`mv`/`rm` —— 一律先讲清改动再动手，提交/推送等用户确认。
- 🔴 **绝不自动改（不可再生的权威资源）**：`backend/resources/cinematography/**` —— 尤其 `scene_info/*.json`（真实坐标锚点）、`LayoutLib.json`、`CameraLib.json`。这些是离线产出、位置系统的命根子，**任何修改必须显式获得用户同意**。破坏性 git（`push --force`、推 main、`reset --hard`、`git clean`）一律禁止。

## 已知约束 / 陷阱

- 场景是否可进入摄影流程以对应 `scene_info/*.json` 是否存在为准；新增场景必须同时提供并人工确认权威锚点，不要硬编码可用场景数量。
- 直接模式里用户的「位置」是自由文本，可能对不上场景锚点 —— 约定当**偏好提示**喂摄影，摄影只从已有锚点挑、对不上优雅降级，不硬塞。
- 动作资源 `backend/resources/actions_resource.json` 每条只有 `description` + FBX 文件名，**无 gif/mp4/图片预览素材**；动作可视化预览类需求当前素材做不了，属素材生产问题。
