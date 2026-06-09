# ScriptsGenerateAgent — 项目规则

> 仅放本项目专属约定。通用规则（语言、协作态度、编码风格、提交格式）见全局 `~/.claude/CLAUDE.md`，此处不重复。

## 项目概述

- 技术栈：Flask 后端 + 原生 JS 前端（无框架）。
- 后端入口：`backend/app.py`，跑 `python3 backend/app.py`，服务在 `:5001`，debug 热重载。
- 前端：`frontend/`（`index.html` + `js/{api,config,main,ui}.js` + `css/style.css`），无构建步骤，刷新即生效。
- git：分支 `autogen_agents`，远程 `LiNnnNl/ScriptsGenerateAgent`。

## 验证命令（改完必跑）

- 前端 JS：`node --check frontend/js/<file>.js`
- 后端 Python：`python3 -m py_compile backend/src/<file>.py`
- 配置 JSON：`python3 -m json.tool <file>.json > /dev/null`

## 关键架构事实（动代码前必须记住）

- **位置系统权威数据源**：`backend/resources/cinematography/scene_info/*.json` 的 anchors/scene_markers 是**带真实 x/y/z 坐标的物品锚点**，为唯一权威。`scenes_resource.json` 的 `valid_positions`（Position 1~N）是**无坐标的逻辑槽**，旧版，仅作导演点位菜单 + 同框约束 + 校验。
- **坐标全部由摄影算**：角色站位 x/y/z 由摄影 Stage2（`CinematographyPositionStage`，分组→规划 region+neartarget→`CoordinateSkill` 用锚点坐标 + `LayoutLib.json` 按人数选站位方式）计算得出。Position N 本身不带坐标。
- **direct_mode（直接生成）**：用户粘已写好的剧本时，跳过头脑风暴/对白补写，由 `DirectorAgent_Direct` 做"结构化不创作"——对白逐字保留、保留每个镜头、按用户「位置」分配 Position N。实现在 `autogen_pipeline.py` 的 `_build_direct_draft` 与 `autogen_agents.py` 的 `build_director_system_message(..., direct_mode=True)`。
- 1 幕 = 输出 JSON 数组的 1 个 scene_obj（`autogen_pipeline.py` 数组长度严格 = act_count）；当前全流程只加载一个 scene。

## auto-mode / 改动风险三级约定

与 `.claude/settings.local.json` 的 `autoMode` 块一致，平时也遵守：

- 🟢 **可放手改（前端展示层）**：`frontend/**` 的 js/css/html。可逆、影响面小。
- 🟡 **改前先说清、git 写要确认（后端核心）**：`backend/**`（pipeline、cinematography 坐标逻辑、`backend/app.py`）；`git add/commit/push`、装依赖、`mv`/`rm` —— 一律先讲清改动再动手，提交/推送等用户确认。
- 🔴 **绝不自动改（不可再生的权威资源）**：`backend/resources/cinematography/**` —— 尤其 `scene_info/*.json`（真实坐标锚点）、`LayoutLib.json`、`CameraLib.json`。这些是离线产出、位置系统的命根子，**任何修改必须显式获得用户同意**。破坏性 git（`push --force`、推 main、`reset --hard`、`git clean`）一律禁止。

## 已知约束 / 陷阱

- 场景锚点目前仅 `SpaceStation` / `LotusTown` 两套有 `scene_info` 文件；任何"多场景"功能越多越依赖每个场景都有锚点文件。
- 直接模式里用户的「位置」是自由文本，可能对不上场景锚点 —— 约定当**偏好提示**喂摄影，摄影只从已有锚点挑、对不上优雅降级，不硬塞。
- 动作资源 `backend/resources/actions_resource.json` 每条只有 `description` + FBX 文件名，**无 gif/mp4/图片预览素材**；动作可视化预览类需求当前素材做不了，属素材生产问题。
