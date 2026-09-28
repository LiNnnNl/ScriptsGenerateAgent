# ScriptsGenerateAgent — 项目概述

> 2026-09 最终格式更新：共享合同在 `backend/src/script_contract.py`，完整字段与冲突决策见 `docs/script_contract.md`。技术验证、补全后、摄影后和最终导出均检查；最终剧本、镜头、演员和多幕位置交叉校验通过，才从 `outputs/.pending/<run>` 发布。Word 导出及编辑器下载也复用合同。camera_script 的幕键为 shot_index；多幕位置文件使用 scenes 数组。下述历史多场景规划不改变此发布门禁。

> 面向**接手本项目的人**的完整架构与数据流说明。读完应能理解：系统做什么、一次生成在内部如何流转、关键数据模型、各模块职责，以及多场景能力的当前边界。
>
> 配套文档：`AGENTS.md`（项目规则与红线）、`README.md`（运行说明）、`docs/script_contract.md`（最终合同）、`docs/`（格式说明与历史设计稿）。
>
> **维护约定**：改了架构、数据流或最终合同后，同步更新本文件、`README.md` 与对应 `docs/` 文档；规划内容只放在 Roadmap，避免与现状混写。

---

## 1. 这是什么

一个**多 Agent 驱动的剧本生成系统**：用户给定场景、角色、创作灵感和幕数，系统通过多个 LLM Agent 协作（创意会议 → 分场大纲 → 导演起草 → 可选人物/对白审查 → 摄影指导），产出一套可供下游（Unity 引擎）使用的结构化剧本资产：剧本 JSON、镜头脚本、角色档案、角色站位与坐标。

两种生成模式：

- **正常生成模式**：用户给「创作灵感」，AI 从头脑风暴开始创作。
- **直接生成模式（direct_mode）**：用户粘贴已写好的剧本（JSON 或纯文本），系统跳过创作阶段，只做「结构化不创作」——逐字保留对白与镜头，补齐缺失字段。

---

## 2. 技术栈与运行

- **后端**：Python + Flask；多 Agent 基于 AutoGen（`RoundRobinGroupChat` / `AssistantAgent`）。入口 `backend/app.py`，跑 `uv run python backend/app.py`，服务在 `:5001`；本地开发时主要提供 `/api/*`，在反代 / Tunnel 的 `/script/*` 场景下也可直接托管前端静态文件，debug 热重载。
- **前端**：原生 HTML/JS/CSS，**无框架、无构建步骤**。`frontend/index.html` + `frontend/js/{config,api,main,ui}.js` + `frontend/css/style.css`；本地推荐 `python3 -m http.server 8080` 独立开发，也可由 Flask 在 `/script/*` 下统一托管。
- **LLM 调用**：通过 OpenAI 兼容接口。Director、创意、文学与摄影等复杂任务使用 `MODEL`；动作/表情专项可用 `PERFORMANCE_MODEL` 覆盖；TitleAgent、MeetingSummaryAgent、StoryIRAgent 和 ShotPlanAgent 使用 `SIMPLE_MODEL`；额度耗尽后使用 `FALLBACK_MODEL`。
- **Git**：远程 `LiNnnNl/ScriptsGenerateAgent`；功能分支按任务创建，实际分支与上游关系以 `git status -sb` / `git branch -vv` 为准。

### 验证命令（改完必跑）

| 类型 | 命令 |
|------|------|
| 前端 JS | `node --check frontend/js/<file>.js` |
| 后端 Python | `python3 -m py_compile backend/src/<file>.py` |
| 配置 JSON | `python3 -m json.tool <file>.json > /dev/null` |

---

## 3. 目录结构

```
ScriptsGenerateAgent/
├── backend/
│   ├── app.py                      # Flask 入口：/api 路由 + /script 下的前端静态托管
│   ├── requirements.txt
│   ├── resources/                  # 资源库（部分为不可再生的权威数据）
│   │   ├── scenes_resource.json        # 场景列表 + valid_positions（逻辑槽，旧版）
│   │   ├── characters_resource.json    # 角色库（含 gameobject_name → Unity 模型）
│   │   ├── actions_resource.json       # 动作库（description + FBX 文件名，无预览素材）
│   │   ├── cinematography/             # 🔴 摄影权威资源（坐标命根子）
│   │   │   ├── CameraLib.json              # 镜头库
│   │   │   ├── LayoutLib.json              # 站位布局库（按人数选站位方式）
│   │   │   └── scene_info/                 # 真实 x/y/z 锚点；存在该文件的场景才可进入摄影流程
│   │   ├── Images/ , position_templates/ , scene_exports/
│   ├── src/
│   │   ├── autogen_pipeline.py     # ⭐ 主流程编排（一次生成的全过程）
│   │   ├── autogen_agents.py       # 模型路由 + Agent 工厂 + PositionAgent 旧接口兼容
│   │   ├── agents/                 # 可独立维护、后续可拆仓库的 Agent 包
│   │   │   ├── title/                  # TitleAgent：工厂 + 系统/用户提示词
│   │   │   ├── concept_pitch/          # 创意概念顾问
│   │   │   ├── character_voice/        # 人物逻辑顾问
│   │   │   ├── narrative_arch/         # 叙事结构顾问
│   │   │   ├── meeting_summary/        # 创意会议摘要
│   │   │   ├── treatment/              # 分场大纲
│   │   │   ├── director/               # 导演起草（普通/直接模式）
│   │   │   ├── director_word/          # 可读 Word 分镜导演
│   │   │   ├── shot_plan/              # Word 长镜号分幕规划
│   │   │   ├── story_ir/               # 冻结剧情事件中间格式
│   │   │   ├── critic/                 # 叙事一致性审查
│   │   │   ├── dialogue/               # 对白质量审查
│   │   │   ├── revision/               # 文学审查后的对白局部返修
│   │   │   ├── contract_repair/        # 最终合同局部修复
│   │   │   └── position/                # 旧位置映射兼容 Agent（按要求保留）
│   │   ├── resource_loader.py      # 加载资源、scene_info（含文件名模糊匹配）
│   │   ├── json_generator.py       # 组装最终剧本 JSON
│   │   ├── schema.py               # Pydantic 校验（shot/position/camera_script）
│   │   ├── registry.py             # session 注册（产出文件索引）
│   │   ├── word_exporter.py        # 剧本导出为 Word
│   │   ├── performance/            # 动作/表情专项选择（独立负责人包）
│   │   │   ├── action/                 # 动作输入裁剪、规则、校验与回填
│   │   │   └── expression/             # 表情输入裁剪、规则、校验与回填
│   │   └── cinematography/         # 摄影三阶段后处理
│   │       ├── __init__.py             # run_cinematography_pipeline（入口，逐幕循环）
│   │       ├── camera/                 # 镜头负责人包：Stage1/3 实现 + rules/
│   │       ├── positioning/            # 点位负责人包：Stage2 + 坐标实现 + rules/
│   │       ├── *_planning_stage.py     # 旧导入路径兼容层
│   │       └── position_*.py           # 旧版 PositionAgent 兼容实现
│   └── outputs/                    # 每次生成的产物（按 timestamp 命名）
├── frontend/
│   ├── index.html
│   ├── js/{config,api,main,ui}.js
│   └── css/style.css
├── AGENTS.md                       # 项目规则与资源修改红线
├── PROJECT_OVERVIEW.md             # 本文件
├── README.md
└── docs/                           # 早期设计稿
```

---

## 4. 端到端数据流（一次生成）

静态前端发起 API 请求 → 后端 `run_autogen_pipeline` 编排 → 通过 NDJSON 流式回传日志/产物。

### 4.1 前端侧（`frontend/js/main.js`）

1. 页面加载：拉取场景/角色/动作/拍摄手法列表（`init` → `loadScenes` 等）。
2. 用户建立**场景池**并指定逐幕场景 → **设角色数** → 选风格/语言/模式 → 填**创作灵感或原始剧本** → 设**幕数**。
3. （可选，推荐）点 **GENERATE CAST**：调 `POST /api/generate_characters` 先生成角色档案，供预览/替换。
4. 点 **ACTION!**：调 `POST /api/generate`（NDJSON 流），`handleStreamData` 实时渲染日志与最终结果。

提交给 `/api/generate` 的关键字段：`custom_characters`、`scene_pool`、`act_scenes`、兼容字段 `scene_id`、`creative_idea`、`required_character_count`、`act_count`、`script_style_id`、`script_tone_id`、`dialogue_language`、`shot_style_reference`、`direct_mode`。

> 注：前端**始终传 `act_count`**（用户在 UI 设定），所以后端「按时长反推 act_count」的分支实际只在直接调 API 不传幕数时才会触发；时长真正影响的是「目标对白行数」。

### 4.2 后端主流程（`backend/src/autogen_pipeline.py: run_autogen_pipeline`）

Pipeline 持有阶段顺序、重试、模型路由、资源和最终合同；`backend/src/agents/<agent_name>/` 持有对应 Agent 的提示词、专属规则和创建入口。所有 `autogen_agents.py` 工厂现已委托到独立包；该文件保留原函数签名并集中处理模型选择、工具与资源注入，避免模块迁移改变现有调用方。

1. **解析参数**：从 `creative_idea` 用正则提取 `user_constraints`（「不要…/必须…」）、`fixed_dialogues`（「角色名: 对白」原样保留）、目标时长 → 推算目标对白行数。
2. **加载场景**：优先解析 `scene_pool`，缺省时回退 `scene_id`；预加载池中场景并按 `act_scenes` 构建 `act_scene_map`，无效或缺失的逐幕分配回退到池中第一个场景。
3. **构建角色**：有 `custom_characters` 则 `build_custom_characters`，否则交给 AI 自由创作。
4. **创意阶段**（direct_mode 整体跳过）：
   - **创意会议**：`RoundRobinGroupChat` 默认由 ConceptPitch / NarrativeArch 两位顾问轮流发言；`enable_character_module=true` 时加入 CharacterVoice。每位最多两轮，出现 `[AGREE]` 可提前终止。
   - **创意摘要**：`MeetingSummaryAgent` 将会议原文压缩为角色、冲突、幕目标、保留项和场景/风格约束；后续阶段不再接收会议全文。
   - **分场规划**：`TreatmentAgent` 把创意摘要转成分场大纲（数组长度恰好 = `act_count`）。
   - **Story IR 冻结**：代码先建立稳定 `event_id` 清单。用户输入编号分镜时，代码冻结镜头边界和对白原文，`StoryIRAgent` 只识别同镜头内的对白/动作/移动组合；自由创作时它只填充代码预分配的事件槽，每次最多 8 个事件。
   - **剧本起草**：`DirectorAgent` 每次只能看到并返回当前最多 8 个 Story IR 事件，ID、顺序、speaker/content 必须一一对应；代码合并后移除临时 ID，并确定性补 `event_index`、全员位置快照、默认情绪/理由、语言副本和空 `shot_description`。
   - **无状态与局部返修**：每次模型请求通过 AutoGen `on_reset` 清空历史；方舟上的 Story IR、Director 和局部修复 Agent 默认关闭深度思考；文学审查以 `act_index/event_index` 定位，返修最多返回 6 个对白补丁；合同修复只发送错误事件或幕元数据。正文为空但 reasoning channel 整体是合法 JSON 时可恢复为候选结果；`finish_reason=length` 前缀续写仅作异常恢复。
   - **可选人物模块**：前端默认关闭；开启后才运行 CharacterVoiceAgent、CriticAgent、DialogueAgent 和 RevisionAgent，并要求先生成或导入人物档案。关闭时走 8 Agent 核心流程。
   - direct_mode 分支：`DirectorAgent_Direct` 经 `_build_direct_draft` 把用户剧本结构化；JSON 输入仍直接解析，超过 8 条且具有明确逐行边界的对白/编号分镜按事件数和字符预算双重切批，以代码生成的 `source_event_id` 校验数量、顺序和原文后合并。自由格式文本不盲拆。
   - 导演 Word 模式：识别到超过 12 个 `S01` 式镜号时，`ShotPlanAgent` 先规划镜号到幕的连续归属，再按 6-8 镜头批量补全；每批经 NDJSON 回传预览，后端按镜号顺序合并。
5. **时长估算**：按对白字数 + 行数估算影片秒数。
6. **位置兜底**：`_extract_position_files` 从剧本直接抽 position_plan/detail（无 LLM，摄影未开启时的兜底；摄影默认开启故通常被覆盖）。
7. **摄影指导**（默认启用，`run_cinematography_pipeline`，详见 §5）：逐幕跑三阶段，产出 camera_script 与含坐标的 position_plan/detail，并回填镜头字段重写剧本。
8. **表演设计**（`run_performance_pipeline`）：ActionSelectionAgent 与 ExpressionSelectionAgent 分别按最多 8 镜头的窗口处理；代码只发送本专项字段、自然语言剧情描述、摄影生成的画面描述及合法资源候选，严格按 `act_index/event_index` 回填，不能改对白、镜头、站位或事件结构。不兼容现有角色的表情库不会调用模型，而是保持禁用。
9. **演员档案**：从最终剧本提取出现的角色，匹配 `characters_resource.json`（`gameobject_name` 必须来自资源库，缺失时 `_find_fallback_gameobject_name` 按名称/性别近似兜底）→ `actors_profile.json`。
10. **最终发布**：产物先进入 `outputs/.pending/<run>/`；统一合同和跨文件引用校验通过后原子移动到 `outputs/`。
11. **注册 session**：请求开始即登记 `running` 与输入快照；异常改为 `failed`，成功后写入标题、产物索引并发出 `success` 事件。

### 4.3 产出文件（`backend/outputs/`，`{ts}` = 时间戳）

| 文件 | 内容 |
|------|------|
| `script_{ts}.json` | 剧本主文件（scene_obj 数组，含 beats + 站位描述 + 镜头字段） |
| `camera_script_{ts}.json` | 镜头脚本（每个事件的镜头参数） |
| `actors_profile_{ts}.json` | 演员档案（name/gender/gameobject_name/appearance/traits…） |
| `position_plan_{ts}.json` | 站位规划（`where`/`groups`/`singles`，含 region/lookat） |
| `position_detail_{ts}.json` | 站位详情（更细的 neartarget 等） |
| `validation_{ts}.json` | 最终合同与跨文件引用的错误/警告报告 |
| `CinematographyStages/` | 摄影各阶段中间产物（调试用） |

position_plan/detail 在单幕时直接输出对象；多幕时统一输出 `{"scenes":[幕1, 幕2, ...]}`。

---

## 5. 摄影指导管线（`backend/src/cinematography/__init__.py`）

入口 `run_cinematography_pipeline(script, scene, resource_dir, output_dir, timestamp, act_scene_map=None, progress_callback=None)`，同步执行（在 executor 里跑）。

- 先从默认场景构建 `base_scene_info`（`get_scene_info_json`，含文件名模糊匹配），多场景时按场景 id 缓存各自的 scene_info。
- **逐幕循环**（`for scene_obj in script`）：
  - **Stage 1 `ShotPlanningStage`**：补镜头描述。
  - **Stage 2 `CinematographyPositionStage`**：按 `act_scene_map[幕序号]` 选择本幕锚点，分组 → 规划 `region` + `neartarget` → `CoordinateSkill` 用锚点坐标 + `LayoutLib`（按人数选站位方式）算出 x/y/z。产出 position_plan/detail；失败时禁止发布不完整结果。
  - **Stage 3 `CameraPlanningStage`**：生成镜头计划。
  - 冲突修复：备份/还原 move 节点的 `shot:"scene"`、归一化 `shot_blend` 为运行时的 `cut/blend/easein`、按脚本 `where` 覆盖 `scene_info.where`。
- 循环后构建 `camera_script` 并用 schema 校验；失败则对失败的幕重试 Stage 3 一次。
- 落盘 camera_script、position_plan、position_detail；多幕位置文件使用 `scenes` 数组完整保留每幕结果。

职责边界：`cinematography/camera/` 独立拥有 Stage 1/3 代码与全部镜头生成规则；`cinematography/positioning/` 独立拥有 Stage 2、坐标计算与全部点位生成规则。上层包只编排阶段、执行最终校验和发布，旧模块路径仅转发导入。

摄影完成后才运行 `performance/`，因此动作与表情 Agent 能使用最终 `shot_description`。`performance/action/` 与 `performance/expression/` 各自拥有规则和 Stage，可独立交给对应负责人；表情 Agent 额外接收 `resources/pixar_emotions_context.md`，代码按 `resources/pixar_emotions_with_styles.json` 严格校验情绪名、样式、权重和每个复合表情最多 3 项；共享层只提供逐镜头自然语言上下文和 JSON-only LLM 客户端。

---

## 6. 关键数据模型与概念

### 6.1 幕 = JSON 数组的一个元素

- 剧本输出是一个 JSON 数组，**数组长度严格 = `act_count`**，每个元素是一个 `scene_obj`（一幕）。
- `scene_obj` 关键字段：
  - `scene information`：本幕元信息（`who` 出场角色、`where` 场景名等）。
  - `initial position`：起始站位（`position` + `character` + `state`）；`state` 表示角色在剧情开始时的姿态（如 `standing` / `sitting`）。
  - `scene`：beats 数组（对白/动作/镜头节拍）。
  - `position_descriptions`：各 Position 的戏剧意图自然语言描述。
- `schema.py: validate_script_position_structure` 强制 `initial position.state` 非空，并要求同一 `initial position`、同一镜头的 `current position` 中不同人物的 Position 互不相同；违反时作为阻塞错误进入重试/自动修复流程。

### 6.2 Beat 类型（`backend/src/schema.py`）

- **character beat**：`shot="character"` + `shot_blend` + `shot_type` + `Follow(0/1)` + `motion_detail`（必填，英文动作细节）。
- **object beat**：`shot="object"` + `target` + `target_anchor` + 物体专用 `shot_type`；target 从当前场景 `scene_markers.name` 选择，不输出 `target_position`。
- **scene beat**：`shot` + `shot_blend` + `camera`。
- **无说话人事件**：非 move 且 speaker=""，content 保留原文或“无台词”，duration 为正数秒默认 5，actions=[]。普通空镜的中间 shot=scene；最终主剧本不含摄影参数，由独立 camera_script 承载。统一由 scene_segments.py 保护，文学审查及人物镜头分配跳过。
- **move**：角色移动。对齐下游 `ExecuteMoveEvent` 两种形态：①**基础移动**（只走不说）`{move:[{character, destination}]}`，move 可单对象或数组（多人同移），移动者**不写 action**（走路由系统驱动）；②**边走边说**——在 move 事件**顶层**加 `speaker`/`content`（+可选 emotion），说话人须为真实角色、非 default。落盘 `script` 的 move 事件镜头字段已剥离至 `camera_script`（沿用场景固定机位）。
- 合法值：`VALID_SHOT_TYPE`（全景/中景/中近景/近景/仰拍/俯拍…）、`VALID_SHOT_BLEND`（运行时归一为 `cut/blend/easein`）、`VALID_LAYOUTS`（two_person/L_shape/triangle/line/square/arc/cluster/layered）。

### 6.3 位置系统（最容易踩坑，务必理解）

| 数据源 | 性质 | 用途 |
|--------|------|------|
| `cinematography/scene_info/*.json` 的 anchors/scene_markers | **带真实 x/y/z 坐标的物品锚点**，唯一权威 | 摄影算角色坐标；scene_markers.name 也是物体镜头 target 候选 |
| `scenes_resource.json` 的 `valid_positions`（Position 1~N） | **无坐标的逻辑槽**，旧版 | 导演点位菜单 + 同框约束 + 校验 |

- **角色坐标全部由摄影 Stage2 计算**（region+neartarget → CoordinateSkill + LayoutLib）。`Position N` 本身不带坐标。
- 锚点是场景中**标志性物体（雕像/树/石柱…）的坐标，不是角色站立点**——导演只为站位选「区域名」，坐标交给摄影。
- 可用性由 `scene_info/*.json` 是否存在决定；前端禁用无锚点场景，后端再次校验。不要在代码或文档中硬编码可用场景数量。

---

## 7. HTTP 路由速查（`backend/app.py`）

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/` `/script` `/script/<asset>` | 前端静态页面与资源（供反代 / Tunnel 场景使用） |
| GET | `/api/health` | 部署探活 |
| GET | `/api/scenes` `/api/scenes/<style_tag>` | 场景列表 |
| GET | `/api/characters` `/api/characters/<style_tag>` | 角色列表 |
| POST | `/api/characters` | 新增角色 |
| GET | `/api/actions` `/api/shot_types` `/api/styles` | 动作/拍摄手法/画风列表 |
| POST | `/api/generate_characters` | AI 生成角色档案（参考整个场景池） |
| POST | `/api/generate` | **生成剧本（NDJSON 流式）** |
| POST | `/api/generate_director_word` | 导演 Word 模式（NDJSON 流式） |
| GET | `/api/script_content/<filename>` | 读剧本内容 |
| POST | `/api/validate_script` | 校验编辑后的剧本 |
| GET | `/api/character_image/<gameobject_name>` | 角色形象图 |
| GET | `/api/download/<filename>` | 下载产出文件 |
| GET | `/api/position_plan/<session_id>` | 读 position_plan |
| GET | `/api/download_session/<session_id>` | 下载完整会话 ZIP |
| GET | `/api/download_word/<filename>` | 导出并下载 Word |
| GET | `/api/history` ; PATCH `/api/history/<session_id>/label` | 历史记录 / 改版本名 |

---

## 8. 前端结构（`frontend/`）

- `config.js`：全局 `APP_STATE`（场景池、逐幕场景、角色、幕数、生成结果、历史 session 等）。
- `api.js`：`fetch` 封装（含 NDJSON 流式解析）。
- `main.js`：流程编排 + 事件监听 + 流数据处理（生成、直接模式、Word 模式、历史输入复填）。
- `ui.js`：DOM 渲染（场景池/逐幕分配、角色卡、剧本可读视图、生成状态历史面板、Position→锚点名映射等）。
- `local-projects.js`：浏览器本地项目文件、自动保存与版本快照；不支持 File System Access API 时退化为导入/下载。

---

## 9. 关键约定与已知陷阱

- **资源改动三级**（与 `AGENTS.md` 一致）：
  - 🟢 `frontend/**`：可放手改（展示层，可逆）。
  - 🟡 `backend/**`（pipeline、cinematography、`app.py`）+ git 写 + 装依赖 + `mv`/`rm`：先讲清再动，提交/推送等用户确认。
  - 🔴 `backend/resources/cinematography/**`（尤其 `scene_info/*.json`、`LayoutLib.json`、`CameraLib.json`）：离线产出的权威坐标，**任何修改必须显式获得用户同意**。破坏性 git（`push --force`、推 main、`reset --hard`、`git clean`）一律禁止。
- 多场景只允许选择存在 `scene_info` 的场景；新增场景时必须同时提供并人工确认权威锚点。
- 直接模式里用户的「位置」是自由文本，可能对不上锚点 → 当**偏好提示**喂摄影，摄影只从已有锚点挑、对不上优雅降级，不硬塞。
- 动作资源只有 `description` + FBX 文件名，**无 gif/mp4/图片预览**；动作可视化预览类需求当前素材做不了（属素材生产问题）。
- `gameobject_name` 必须来自 `characters_resource.json`；AI/自定义角色缺失时按名称/性别近似兜底。
- `outputs/`、`backend/outputs/`、日志、虚拟环境、`.codex-runtime/`、本机启动脚本和临时验收渲染不得提交；`backend/.env` 与密钥文件始终只留本地。

---

## 10. 多场景现状

- `scene_pool: [sceneId, ...]` 表示本次生成允许使用的场景；未传时兼容旧的 `scene_id`。
- `act_scenes: [sceneId, ...]` 按数组下标指定每幕场景；缺失、越界或不在池中的值回退到池中第一个场景。
- 前端只允许选择具备 `scene_info` 锚点的场景；后端仍会校验，避免绕过 UI 后生成无坐标产物。
- 创意会议和角色生成参考整个场景池；Director 按幕收到对应场景与区域；代码确定性覆盖每幕 `scene information.where`。
- 摄影通过 `act_scene_map` 按幕选择锚点并缓存 scene_info；多幕 position_plan/detail 用 `scenes` 数组保存全部幕。
- 不传新字段时保持单场景旧行为。当前角色池跨幕复用；“每幕独立换角色”尚未实现。

直接模式会按 `act_count` 整理输入，但用户自由文本中的位置仍只是偏好，最终只能落到所选场景已有锚点。

---

## 11. 待办（Roadmap）

### 11.1 move 支持多种移动方式（跑步 / 慢走 / 正常走）

> **依赖下游先支持**：当前下游 `XMU_FILM_code` 的移动速度硬编码（`CharacterBehaviorModule.MoveExecute` 里 `agent.speed = 1.5f`），`isWalking` 只是「是否在移动」的动画开关，**没有步态/速度区分**。需下游先按移动方式查表设 `agent.speed` + 走/跑动画（BlendTree 或动画倍速近似），详见下游仓库 `TODO_移动方式_跑步慢走.md`。

生成端（本仓库）待办，**待下游与字段约定确定后再做**：
- move 项新增可选字段 `moveType`（约定取值 `slowWalk`/`walk`/`run`，不传 = `walk`）。
- 导演 prompt（`autogen_agents.py`）：在 move 事件说明里加 `moveType` 字段 + 可选值 + 示例（移动方式仍走 move 事件，移动者不写 action 的约定不变）。
- schema/generator 预计不用改（move 多出字段默认被忽略/保留），落地时确认。
