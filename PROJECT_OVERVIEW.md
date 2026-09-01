# ScriptsGenerateAgent — 项目概述

> 面向**接手本项目的人**的完整架构与数据流说明。读完应能理解：系统做什么、一次生成在内部如何流转、关键数据模型、各模块职责、以及目前正在规划的「多场景」改造。
>
> 配套文档：`CLAUDE.md`（给 AI 助手的项目规则与红线）、`README.md`（运行说明）、`docs/`（更早期的设计稿）。
>
> **维护约定**：改了架构/数据流/约定后，请同步更新本文件；本文件描述「现状」，规划中的内容统一放在文末「多场景改造」章节，落地后再并入正文。

---

## 1. 这是什么

一个**多 Agent 驱动的剧本生成系统**：用户给定场景、角色、创作灵感和幕数，系统通过多个 LLM Agent 协作（创意会议 → 分场大纲 → 导演起草 → 文学审查 → 摄影指导），产出一套可供下游（Unity 引擎）使用的结构化剧本资产：剧本 JSON、镜头脚本、角色档案、角色站位与坐标。

两种生成模式：

- **正常生成模式**：用户给「创作灵感」，AI 从头脑风暴开始创作。
- **直接生成模式（direct_mode）**：用户粘贴已写好的剧本（JSON 或纯文本），系统跳过创作阶段，只做「结构化不创作」——逐字保留对白与镜头，补齐缺失字段。

---

## 2. 技术栈与运行

- **后端**：Python + Flask；多 Agent 基于 AutoGen（`RoundRobinGroupChat` / `AssistantAgent`）。入口 `backend/app.py`，跑 `uv run python backend/app.py`，服务在 `:5001`；本地开发时主要提供 `/api/*`，在反代 / Tunnel 的 `/script/*` 场景下也可直接托管前端静态文件，debug 热重载。
- **前端**：原生 HTML/JS/CSS，**无框架、无构建步骤**。`frontend/index.html` + `frontend/js/{config,api,main,ui}.js` + `frontend/css/style.css`；本地推荐 `python3 -m http.server 8080` 独立开发，也可由 Flask 在 `/script/*` 下统一托管。
- **LLM 调用**：通过 OpenAI 兼容接口（`backend/src/autogen_agents.py` 的 `make_model_client`，主模型 + 额度耗尽后的 `make_fallback_model_client` 备用模型）。
- **Git**：分支 `autogen_agents`，远程 `LiNnnNl/ScriptsGenerateAgent`。

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
│   │   │   └── scene_info/                 # 真实 x/y/z 锚点（唯一权威，仅 2 套）
│   │   │       ├── SpaceStation.json
│   │   │       └── LotusTown.json
│   │   ├── Images/ , position_templates/ , scene_exports/
│   ├── src/
│   │   ├── autogen_pipeline.py     # ⭐ 主流程编排（一次生成的全过程）
│   │   ├── autogen_agents.py       # 各 Agent 的 system_message 构建 + 工厂函数
│   │   ├── resource_loader.py      # 加载资源、scene_info（含文件名模糊匹配）
│   │   ├── json_generator.py       # 组装最终剧本 JSON
│   │   ├── position_metadata.py    # Position 元数据归一化、旧格式升级与校验
│   │   ├── schema.py               # Pydantic 校验（shot/position/camera_script）
│   │   ├── registry.py             # session 注册（产出文件索引）
│   │   ├── word_exporter.py        # 剧本导出为 Word
│   │   └── cinematography/         # 摄影三阶段后处理
│   │       ├── __init__.py             # run_cinematography_pipeline（入口，逐幕循环）
│   │       ├── shot_planning_stage.py      # Stage1：镜头描述
│   │       ├── cinematography_position_stage.py # Stage2：站位 + 坐标
│   │       ├── camera_planning_stage.py    # Stage3：镜头参数 → camera_script
│   │       ├── coordinate_skill.py         # 用锚点坐标 + LayoutLib 算 x/y/z
│   │       └── position_*.py
│   └── outputs/                    # 每次生成的产物（按 timestamp 命名）
├── frontend/
│   ├── index.html
│   ├── js/{config,api,main,ui}.js
│   └── css/style.css
├── CLAUDE.md                       # 给 AI 的项目规则与红线
├── PROJECT_OVERVIEW.md             # 本文件
├── README.md
└── docs/                           # 早期设计稿
```

---

## 4. 端到端数据流（一次生成）

静态前端发起 API 请求 → 后端 `run_autogen_pipeline` 编排 → 通过 NDJSON 流式回传日志/产物。

### 4.1 前端侧（`frontend/js/main.js`）

1. 页面加载：拉取场景/角色/动作/拍摄手法列表（`init` → `loadScenes` 等）。
2. 用户**选场景** → **设角色数** → 填**创作灵感** → 设**幕数**。
3. （可选，推荐）点 **GENERATE CAST**：调 `POST /api/generate_characters` 先生成角色档案，供预览/替换。
4. 点 **ACTION!**：调 `POST /api/generate`（NDJSON 流），`handleStreamData` 实时渲染日志与最终结果。

提交给 `/api/generate` 的关键字段：`custom_characters`、`scene_id`、`creative_idea`、`required_character_count`、`act_count`、`direct_mode`。

> 注：前端**始终传 `act_count`**（用户在 UI 设定），所以后端「按时长反推 act_count」的分支实际只在直接调 API 不传幕数时才会触发；时长真正影响的是「目标对白行数」。

### 4.2 后端主流程（`backend/src/autogen_pipeline.py: run_autogen_pipeline`）

1. **解析参数**：从 `creative_idea` 用正则提取 `user_constraints`（「不要…/必须…」）、`fixed_dialogues`（「角色名: 对白」原样保留）、目标时长 → 推算目标对白行数。
2. **加载场景**：`resource_loader.get_scene_by_id(scene_id)`（当前**只加载一个 scene**，全流程共用）。
3. **构建角色**：有 `custom_characters` 则 `build_custom_characters`，否则交给 AI 自由创作。
4. **创意阶段**（direct_mode 整体跳过）：
   - **创意会议**：`RoundRobinGroupChat`（ConceptPitch / CharacterVoice / NarrativeArch 三顾问轮流发言，最多 6 条消息或出现 `[AGREE]` 提前终止）。
   - **创意摘要**：`MeetingSummaryAgent` 将会议原文压缩为角色、冲突、幕目标、保留项和场景/风格约束；后续阶段不再接收会议全文。
   - **分场规划**：`TreatmentAgent` 把创意摘要转成分场大纲（数组长度恰好 = `act_count`）。
   - **剧本起草**：`DirectorAgent` 输出剧本 JSON 初稿（shot 结构不合规时最多重试 `MAX_SHOT_STRUCT_RETRIES=2` 次）；完整请求网络重试耗尽时，自动按幕请求单个 JSON 并按顺序合并。
   - 文学审查 / 对白补写（`CriticAgent` / `DialogueAgent`）。
   - direct_mode 分支：`DirectorAgent_Direct` 经 `_build_direct_draft` 把用户剧本结构化。
   - 导演 Word 模式：识别到超过 12 个 `S01` 式镜号时，`ShotPlanAgent` 先规划镜号到幕的连续归属，再按 6-8 镜头批量补全；每批经 NDJSON 回传预览，后端按镜号顺序合并。
5. **时长估算**：按对白字数 + 行数估算影片秒数。
6. **位置兜底**：`_extract_position_files` 从剧本直接抽 position_plan/detail（无 LLM，摄影未开启时的兜底；摄影默认开启故通常被覆盖）。
7. **摄影指导**（默认启用，`run_cinematography_pipeline`，详见 §5）：逐幕跑三阶段，产出 camera_script 与含坐标的 position_plan/detail，并回填镜头字段重写剧本。
8. **演员档案**：从最终剧本提取出现的角色，匹配 `characters_resource.json`（`gameobject_name` 必须来自资源库，缺失时 `_find_fallback_gameobject_name` 按名称/性别近似兜底）→ `actors_profile.json`。
9. **注册 session** 并发 `success` 事件，回传所有产出文件名。

### 4.3 产出文件（`backend/outputs/`，`{ts}` = 时间戳）

| 文件 | 内容 |
|------|------|
| `script_{ts}.json` | 剧本主文件（scene_obj 数组，含 beats + 站位描述 + 镜头字段） |
| `camera_script_{ts}.json` | 镜头脚本（每个事件的镜头参数） |
| `actors_profile_{ts}.json` | 演员档案（name/gender/gameobject_name/appearance/traits…） |
| `position_plan_{ts}.json` | 站位规划（`where`/`groups`/`singles`，含 region/lookat） |
| `position_detail_{ts}.json` | 站位详情（更细的 neartarget 等） |
| `CinematographyStages/` | 摄影各阶段中间产物（调试用） |

---

## 5. 摄影指导管线（`backend/src/cinematography/__init__.py`）

入口 `run_cinematography_pipeline(script, scene, resource_dir, output_dir, timestamp)`，同步执行（在 executor 里跑）。

- 先从资源构建 `base_scene_info`（`get_scene_info_json`，含文件名模糊匹配）。
- **逐幕循环**（`for scene_obj in script`）：
  - **Stage 1 `ShotPlanningStage`**：补镜头描述。
  - **Stage 2 `CinematographyPositionStage`**：分组 → 规划 `region` + `neartarget` → `CoordinateSkill` 用锚点坐标 + `LayoutLib`（按人数选站位方式）算出 x/y/z。产出 position_plan/detail。
  - **Stage 3 `CameraPlanningStage`**：生成镜头计划。
  - 冲突修复：备份/还原 move 节点的 `shot:"scene"`、归一化 `shot_blend` 为运行时的 `cut/blend/easein`、按脚本 `where` 覆盖 `scene_info.where`。
- 循环后构建 `camera_script` 并用 schema 校验；失败则对失败的幕重试 Stage 3 一次。
- 落盘 camera_script、position_plan、position_detail。

> ⚠️ **关键现状**：position_plan/detail 落盘时取的是 `last_position_plan`（**最后一幕**的 Stage2 结果），并非合并全部幕——这是既有行为。
>
> ⚠️ **多场景核心点**：循环内所有幕**共用同一个 `base_scene_info`（单一 scene 的锚点）**。要支持「不同幕不同场景」，关键就是让循环内按每幕的场景取对应锚点（见 §10）。

---

## 6. 关键数据模型与概念

### 6.1 幕 = JSON 数组的一个元素

- 剧本输出是一个 JSON 数组，**数组长度严格 = `act_count`**，每个元素是一个 `scene_obj`（一幕）。
- `scene_obj` 关键字段：
  - `scene information`：本幕元信息（`who` 出场角色、`where` 场景名等）。
  - `initial position`：起始站位（`position` + `character` + `state`）；`state` 表示角色在剧情开始时的姿态（如 `standing` / `sitting`）。
  - `scene`：beats 数组（对白/动作/镜头节拍）。
  - `position_metadata`：以稳定技术 ID（`Position N`）为键，每项包含 `number`、简短 `name` 和详细 `description`。旧输入中的 `position_descriptions` 会被确定性升级，最终产物只输出新格式。
- `schema.py: validate_script_position_structure` 强制 `initial position.state` 非空，并要求同一 `initial position`、同一镜头的 `current position` 中不同人物的 Position 互不相同；违反时作为阻塞错误进入重试/自动修复流程。

### 6.2 Beat 类型（`backend/src/schema.py`）

- **character beat**：`shot="character"` + `shot_blend` + `shot_type` + `Follow(0/1)` + `motion_detail`（必填，英文动作细节）。
- **scene beat**：`shot` + `shot_blend` + `camera`。
- **empty shot**：`speaker=""` 且 `content=""`（非 move）表示环境空镜；固定 `shot="scene"`、默认 `duration="5s"`、`actions=[]`，不含 `shot_type`/`Follow`。统一由 `scene_segments.py` 做确定性保护；文学审查跳过，摄影 Stage 1 生成/保留环境描述，Stage 2 不据此调整人物分组，Stage 3 跳过人物镜头分配。
- **move**：角色移动。对齐下游 `ExecuteMoveEvent` 两种形态：①**基础移动**（只走不说）`{move:[{character, destination}]}`，move 可单对象或数组（多人同移），移动者**不写 action**（走路由系统驱动）；②**边走边说**——在 move 事件**顶层**加 `speaker`/`content`（+可选 emotion），说话人须为真实角色、非 default。落盘 `script` 的 move 事件镜头字段已剥离至 `camera_script`（沿用场景固定机位）。
- 合法值：`VALID_SHOT_TYPE`（全景/中景/中近景/近景/仰拍/俯拍…）、`VALID_SHOT_BLEND`（运行时归一为 `cut/blend/easein`）、`VALID_LAYOUTS`（two_person/L_shape/triangle/line/square/arc/cluster/layered）。

### 6.3 位置系统（最容易踩坑，务必理解）

| 数据源 | 性质 | 用途 |
|--------|------|------|
| `cinematography/scene_info/*.json` 的 anchors/scene_markers | **带真实 x/y/z 坐标的物品锚点**，唯一权威 | 摄影算角色坐标的依据 |
| `scenes_resource.json` 的 `valid_positions`（Position 1~N） | **无坐标的逻辑槽**，含 `number` / `name` / `description` | 导演点位菜单 + 同框约束 + 元数据生成与校验 |

- **角色坐标全部由摄影 Stage2 计算**（region+neartarget → CoordinateSkill + LayoutLib）。`Position N` 本身不带坐标。
- `Position N` 继续作为剧本引用和 Unity 查找使用的稳定技术 ID；面向创作与界面展示统一使用“序号 · 名称”，悬停或详情区域展示 `description`。
- 锚点是场景中**标志性物体（雕像/树/石柱…）的坐标，不是角色站立点**——导演只为站位选「区域名」，坐标交给摄影。
- 目前**只有 `SpaceStation` / `LotusTown` 两套**有 scene_info 锚点文件。

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
| POST | `/api/generate_characters` | AI 生成角色档案（按场景） |
| POST | `/api/generate` | **生成剧本（NDJSON 流式）** |
| GET | `/api/script_content/<filename>` | 读剧本内容 |
| GET | `/api/character_image/<gameobject_name>` | 角色形象图 |
| GET | `/api/download/<filename>` | 下载产出文件 |
| GET | `/api/position_plan/<session_id>` | 读 position_plan |
| GET | `/api/history` ; PATCH `/api/history/<session_id>/label` | 历史记录 / 改版本名 |

---

## 8. 前端结构（`frontend/`）

- `config.js`：全局 `APP_STATE`（选中场景、角色、幕数、生成结果、当前 session 等）。
- `api.js`：`fetch` 封装（含 NDJSON 流式解析）。
- `main.js`：流程编排 + 事件监听 + 流数据处理（`generateScript` / `generateCast` / `handleStreamData`）。
- `ui.js`：DOM 渲染（场景信息、角色卡、剧本可读视图、历史面板、Position→锚点名映射展示等）。

---

## 9. 关键约定与已知陷阱

- **资源改动三级**（与 `CLAUDE.md`、`.claude/settings.local.json` 的 `autoMode` 一致）：
  - 🟢 `frontend/**`：可放手改（展示层，可逆）。
  - 🟡 `backend/**`（pipeline、cinematography、`app.py`）+ git 写 + 装依赖 + `mv`/`rm`：先讲清再动，提交/推送等用户确认。
  - 🔴 `backend/resources/cinematography/**`（尤其 `scene_info/*.json`、`LayoutLib.json`、`CameraLib.json`）：离线产出的权威坐标，**任何修改必须显式获得用户同意**。破坏性 git（`push --force`、推 main、`reset --hard`、`git clean`）一律禁止。
- 场景锚点只有 2 套 → 任何「多场景」功能越多越依赖每个场景都备好锚点文件。
- 直接模式里用户的「位置」是自由文本，可能对不上锚点 → 当**偏好提示**喂摄影，摄影只从已有锚点挑、对不上优雅降级，不硬塞。
- 动作资源只有 `description` + FBX 文件名，**无 gif/mp4/图片预览**；动作可视化预览类需求当前素材做不了（属素材生产问题）。
- `gameobject_name` 必须来自 `characters_resource.json`；AI/自定义角色缺失时按名称/性别近似兜底。

---

## 10. 多场景改造（一期已落地 2026-06-16｜二期规划中）

> 目标：支持**不同幕使用不同场景**。**一期（正常生成模式）已实现并提交**，下方设计即现状；二期（直接模式接入、按幕换角色）仍为规划。
>
> 一期落地要点：请求新增可选 `scene_pool`/`act_scenes`；导演按幕注场景并分场景列区域；摄影按幕序号取对应场景锚点；角色生成喂整个场景池概述；每幕 `scene information.where` 由 generator 按 `act_scene_ids` 逐幕写对应**场景 id**（**不再有 `scene_id` 字段**，场景标识统一由 `where` 表达）；无锚点场景前端禁用、后端校验挡掉。不传 `scene_pool` 完全回退单场景旧行为。

### 10.1 现状限制

1 幕 = JSON 数组的 1 个 scene_obj，但全流程**只加载一个 scene**（`get_scene_by_id`），导演、摄影、角色生成共用。

### 10.2 产品形态（已与需求方确认）

- **两步式场景池**：用户一开始**多选**一组场景（场景池）；剧本生成（创意会议/导演）**参考整个池**把剧情分布到这些场景；每一幕的场景从池子里挑。
- **角色生成不绑定单一场景**：跨幕复用同一批角色，角色生成参考**整个场景池概述**作为环境参考。
- **幕数由用户确定**（前端始终传 `act_count`），逐幕分配场景无歧义。

### 10.3 数据模型（最小侵入、向后兼容）

- 请求新增可选字段：
  - `scene_pool: [sceneId, ...]`：用户选的场景池（≥1）。
  - `act_scenes: [sceneId, ...]`：下标=幕序号，每幕用池里哪个场景。
- **缺省回退**：没传 `scene_pool` → 用现有单 `scene_id`（旧行为）；`act_scenes[i]` 缺失/越界 → 回退 `scene_pool[0]`。
- 生成后由**代码强制**给每个 scene_obj 的 `scene information` 写入 `scene_id`（按 `act_scenes[i]`），不靠 AI 填。

### 10.4 改动分层

| 层 | 文件 | 改动 |
|----|------|------|
| 解析/编排 | `autogen_pipeline.py` | 解析 `scene_pool`/`act_scenes`，预加载池内所有 scene + scene_info（校验锚点存在），构建 `act_scene_map[i]=scene`，`scene=scene_pool[0]` 作默认 |
| 导演提示 | `autogen_agents.py` | `build_director_system_message` 新增可选 `act_scene_map`：多场景时按幕注明所属场景 + 分别列出各场景可用区域；缺省等价旧逻辑 |
| 创意会议 | `autogen_pipeline.py` | 会议 brief 列出场景池概述，让头脑风暴场景感知 |
| 写 scene_id | `autogen_pipeline.py` | 生成后按 `act_scenes` 给每个 scene_obj 写 `scene_id` |
| 摄影 | `cinematography/__init__.py` | `run_cinematography_pipeline` 新增 `scene_resolver`；循环内按 scene_obj 的 `scene_id` 解析对应场景 scene_info（缓存），缺省回退 `base_scene_info` |
| 角色生成 | `app.py /api/generate_characters` | 接收 `scene_pool`，提示注入整池概述；缺省回退单 scene |
| 前端 | `index.html`/`main.js`/`ui.js`/`config.js` | 场景改多选（池）；幕数确定后渲染 N 个「幕→场景」下拉（选项限池内）；提交带 `scene_pool`+`act_scenes`；角色生成传 `scene_pool` |

### 10.5 分期

- **一期 ✅ 已落地（2026-06-16）**：正常生成模式多场景（数据模型 + 前端 UI + 导演按幕注场景 + 摄影按幕取锚点 + 角色生成喂场景池概述）。
- **二期**：直接生成模式接入多场景 + 按幕换角色（如需）。

### 10.6 风险

- 场景库只有 `SpaceStation`/`LotusTown` 有锚点 → 池内只能选这两个；选了无锚点的场景须在校验阶段挡掉并提示。
- position_plan/detail 落盘目前只保留最后一幕（既有行为）；多场景下不同幕在不同场景，若下游需要按幕区分的位置文件，需要单独评估聚合方式。

---

## 11. 待办（Roadmap）

### 11.1 move 支持多种移动方式（跑步 / 慢走 / 正常走）

> **依赖下游先支持**：当前下游 `XMU_FILM_code` 的移动速度硬编码（`CharacterBehaviorModule.MoveExecute` 里 `agent.speed = 1.5f`），`isWalking` 只是「是否在移动」的动画开关，**没有步态/速度区分**。需下游先按移动方式查表设 `agent.speed` + 走/跑动画（BlendTree 或动画倍速近似），详见下游仓库 `TODO_移动方式_跑步慢走.md`。

生成端（本仓库）待办，**待下游与字段约定确定后再做**：
- move 项新增可选字段 `moveType`（约定取值 `slowWalk`/`walk`/`run`，不传 = `walk`）。
- 导演 prompt（`autogen_agents.py`）：在 move 事件说明里加 `moveType` 字段 + 可选值 + 示例（移动方式仍走 move 事件，移动者不写 action 的约定不变）。
- schema/generator 预计不用改（move 多出字段默认被忽略/保留），落地时确认。
