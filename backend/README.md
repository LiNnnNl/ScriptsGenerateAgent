# ScriptAgent

基于 Flask + AutoGen 的 AI 剧本生成服务，包含概念孵化、梗概、人物小传、分场大纲、导演、批评、对白、摄影指导等多智能体流水线。

## 快速启动

### 方式一：双击 bat 文件（推荐）

双击项目根目录下的：

```
start_backend.bat
```

然后用浏览器打开 `http://localhost:5000`。

前端已由 Flask 一并托管，**无需单独启动前端**。

### 方式二：PowerShell / 手动启动

```powershell
cd backend
pip install -r requirements.txt   # 首次运行
python app.py
```

浏览器访问 `http://localhost:5000`。

### 远程访问（ngrok）

后端启动后，另开终端：

```powershell
ngrok http 5000
```

将 ngrok 提供的 HTTPS 地址发给其他人即可，**无需修改任何前端代码**。

## 环境变量配置（.env）

首次运行前复制模板：

```powershell
copy backend\.env.example backend\.env
```

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `API_KEY` | 火山引擎 ARK API Key | — |
| `BASE_URL` | API 接入点 | `https://ark.cn-beijing.volces.com/api/v3` |
| `MODEL` | 主模型名称 | `doubao-seed-2-0-lite-260215` |
| `ENABLE_CINEMATOGRAPHY` | 是否开启摄影指导后处理 | `false` |
| `CINEMATOGRAPHY_MODEL` | 摄影智能体专用模型（留空则复用 MODEL） | — |
| `MODEL_FUNCTION_CALLING` | 是否启用工具调用版 ValidationAgent | `false` |

API Key 在 [火山引擎控制台](https://console.volcengine.com/ark) 获取。

## 目录结构

```
ScriptsGenerateAgent/
├── start_backend.bat               # 一键启动（后端 + 前端静态文件）
├── backend/
│   ├── app.py                      # Flask 入口，托管前端 + 所有 API 路由
│   ├── requirements.txt
│   ├── .env.example
│   ├── src/
│   │   ├── autogen_pipeline.py     # 多 Agent 主流程编排
│   │   ├── autogen_agents.py       # 各 Agent 创建函数
│   │   ├── autogen_bridge.py       # AutoGen ↔ Flask 流式桥接
│   │   ├── autogen_tools.py        # 技术验证工具函数
│   │   ├── resource_loader.py      # 场景 / 角色资源加载
│   │   ├── json_generator.py       # 最终 JSON 序列化
│   │   ├── position_agent_wrapper.py  # 坐标生成 Agent 包装
│   │   └── cinematography/         # 摄影指导后处理（3 阶段）
│   │       ├── __init__.py         # 流程入口 run_cinematography_pipeline()
│   │       ├── shot_planning_stage.py
│   │       ├── cinematography_position_stage.py
│   │       └── camera_planning_stage.py
│   ├── resources/
│   │   ├── characters_resource.json   # 角色库
│   │   ├── scenes_resource.json       # 场景语义信息
│   │   ├── actions_resource.json      # 动作库
│   │   ├── Images/                    # 角色预览图（文件名 = gameobject_name）
│   │   └── cinematography/
│   │       ├── CameraLib.json         # 摄影机参数库
│   │       ├── LayoutLib.json         # 站位布局库
│   │       └── scene_info/            # 各场景 Unity 坐标信息（手写优先）
│   └── outputs/                       # 生成文件输出目录
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/
│       ├── config.js   # API 端点配置 + APP_STATE 全局状态
│       ├── api.js      # fetch 封装
│       ├── ui.js       # DOM 渲染逻辑
│       └── main.js     # 事件绑定 + 业务流程
└── CHANGELOG.md
```

## 剧本生成流程

```
用户输入（场景 + 角色表 + 创作想法）
        │
        ▼
 ┌─────────────────┐
 │  ConceptAgent   │  生成 Logline、核心冲突、基调
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │  SynopsisAgent  │  生成故事梗概（开端 / 转折 / 结局方向）
 └────────┬────────┘
          ▼
 ┌──────────────────────┐
 │  CharacterBiosAgent  │  生成人物小传（目标 / 弧光 / 关系）
 └────────┬─────────────┘
          ▼
 ┌─────────────────┐
 │ TreatmentAgent  │  生成分场大纲（逐 beat 目标 / 冲突 / 结果）
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │  DirectorAgent  │  生成剧本 JSON 初稿
 └────────┬────────┘
          ▼  循环最多 3 轮
 ┌────────────────────────────────────────┐
 │  CriticAgent   审查剧情逻辑 / 节奏     │
 │  DialogueAgent 审查对白质量 / 语气     │
 │     ↓ 有问题 → DirectorAgent 修改      │
 │     ↓ 通过   → 跳出循环               │
 └────────────────────────────────────────┘
          ▼
 ┌─────────────────┐
 │  技术约束验证   │  Python 校验角色名、位置合法性等
 │ （最多修复3次） │  失败 → DirectorAgent 修复
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │  PositionAgent  │  抽象位置 → 真实点位 ID
 │ （最多重试3次） │
 └────────┬────────┘
          ▼
 ┌─────────────────┐
 │  保底点位修正   │  Python 扫描兜底，替换无效位置
 └────────┬────────┘
          ▼
 ┌─────────────────────────────────┐
 │  摄影指导（ENABLE_CINEMATOGRAPHY）│  可选后处理
 │  Stage 1: 逐幕镜头描述          │
 │  Stage 2: 角色站位规划          │
 │  Stage 3: 摄像机分配            │
 └────────┬────────────────────────┘
          ▼
    输出文件（outputs/）
    script_*.json
    actors_profile_*.json
    position_plan_*.json（摄影指导开启时）
    position_detail_*.json（摄影指导开启时）
```

## 后端 API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/scenes` | 获取所有场景列表（含区域与锚点） |
| GET  | `/api/characters` | 获取角色库（仅展示字段） |
| POST | `/api/characters` | 向角色库添加新角色 |
| POST | `/api/generate_characters` | AI 生成角色表 JSON |
| POST | `/api/generate` | 生成剧本（NDJSON 流式响应） |
| GET  | `/api/script_content/<filename>` | 读取已生成剧本内容 |
| GET  | `/api/character_image/<gameobject_name>` | 返回角色预览图 |
| GET  | `/api/download/<filename>` | 下载 outputs 目录中的文件 |

### `/api/generate` 流式响应格式（每行一个 JSON）

```jsonc
{ "type": "log",     "level": "info",   "message": "🚀 开始生成..." }
{ "type": "log",     "level": "output", "format": "stage",      "agent": "ConceptAgent",     "data": {...} }
{ "type": "log",     "level": "output", "format": "script",     "agent": "DirectorAgent",    "data": [...] }
{ "type": "log",     "level": "output", "format": "feedback",   "agent": "CriticAgent",      "data": {...} }
{ "type": "log",     "level": "output", "format": "validation", "agent": "ValidationAgent",  "data": {...} }
{ "type": "thinking_chunk", "agent": "DirectorAgent", "text": "..." }
{ "type": "thinking_done" }
{ "type": "success", "filename": "script_xxx.json", "actors_profile_filename": "actors_profile_xxx.json", "position_filename": null }
{ "type": "error",   "message": "..." }
```

## 故障排除

| 现象 | 解决方法 |
|------|---------|
| `ModuleNotFoundError` | 在 `backend/` 目录下运行 `pip install -r requirements.txt` |
| API Key 错误 | 检查 `backend/.env` 文件是否存在、Key 是否正确 |
| 端口被占用 | 修改 `app.py` 末尾 `port=5000` 为其他端口 |
| 摄影指导未生效 | 检查 `.env` 中 `ENABLE_CINEMATOGRAPHY=true`，并确认 `resources/cinematography/CameraLib.json` 存在 |
