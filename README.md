# ScriptAgent

电影级 AI 剧本生成系统，基于 Flask + AutoGen 多智能体流水线。支持从创作灵感生成、多场景逐幕编排、粘贴现成剧本直接结构化，以及导演 Word 分镜模式；最终输出经统一合同校验后供 Unity 使用。

主要产物包括剧本 JSON、独立镜头脚本、演员档案、逐幕站位/坐标文件和校验报告。完整架构见 [PROJECT_OVERVIEW.md](./PROJECT_OVERVIEW.md)，最终字段合同见 [docs/script_contract.md](./docs/script_contract.md)。

---

## 本地项目与版本

剧本编辑器支持将当前工作保存为单个 `*.scriptagent.json` 文件。项目文件包含当前剧本、角色、生成配置和版本快照，可以从页面顶部的“打开本地项目/剧本”重新载入；该入口也支持导入普通剧本 JSON 数组，导入后可另存为完整项目。

- 首次点击“保存项目”时选择本地文件；后续编辑停止约 1.2 秒后自动写回。
- “创建版本”保存永久命名快照；自动保存每 10 分钟最多生成一个自动快照，并保留最近 30 份。
- 恢复历史版本前会先创建“恢复前自动快照”，避免覆盖当前工作。
- 不支持 File System Access API 的浏览器会退化为项目文件导入和下载，无法静默写回原文件。
- 服务端“生成记录”会在请求开始时登记任务，显示生成中/失败/成功状态；保存过输入快照的记录可一键复填场景、角色和生成选项。

---

## 快速启动

后端和前端现在是分离运行：

- 后端 Flask 默认端口 `5001`：本地开发时提供 `/api/*`；在反代 / Tunnel 的 `/script/*` 场景下也可直接托管前端静态文件
- 前端仍是纯静态文件；本地开发时可继续用任意静态服务器打开，推荐端口 `8080`

### macOS / Linux

```bash
# 首次运行，创建虚拟环境并安装依赖
uv venv
uv pip install -r backend/requirements.txt

# 推荐：用 tmux 同时管理前后端
scripts/tmux-dev.sh start
scripts/tmux-dev.sh attach
```

常用 tmux 管理命令：

```bash
scripts/tmux-dev.sh status
scripts/tmux-dev.sh restart
scripts/tmux-dev.sh stop
```

手动启动也可以：

```bash
# 终端 1：启动后端
uv run python backend/app.py

# 终端 2：启动前端静态服务
cd frontend
python3 -m http.server 8080
```

打开 `http://localhost:8080`。本地开发时前端会自动请求 `http://localhost:5001/api/*`。

如需模拟线上 `/script/` 前缀访问，也可以直接打开：

```text
http://localhost:5001/script/
```

### Windows

首次运行：

```powershell
uv venv
uv pip install -r backend/requirements.txt
```

然后双击：

```text
start_backend.bat
start_frontend.bat
```

或手动开两个 PowerShell：

```powershell
# 终端 1：启动后端
uv run python backend/app.py

# 终端 2：启动前端
cd frontend
python -m http.server 8080
```

打开 `http://localhost:8080`。

如需验证统一入口部署，也可以直接访问 `http://localhost:5001/script/`。

> 摄影指导后处理默认启用（无需额外配置）。

---

## 部署到服务器

### macOS / Linux 服务器

1. 安装基础工具：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# Ubuntu/Debian 如未安装 tmux：
sudo apt update
sudo apt install -y tmux
```

2. 拉取代码并安装依赖：

```bash
git clone <your-repo-url>
cd ScriptsGenerateAgent
uv venv
uv pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
```

编辑 `backend/.env`，填入 `API_KEY` 等变量。

3. 用 tmux 常驻运行：

```bash
scripts/tmux-dev.sh start
scripts/tmux-dev.sh status
scripts/tmux-dev.sh attach
```

服务地址：

```text
后端 API: http://127.0.0.1:5001
前端页面（本地开发）: http://127.0.0.1:8080
前端页面（统一入口）: http://127.0.0.1:5001/script/
```

常用管理：

```bash
scripts/tmux-dev.sh restart
scripts/tmux-dev.sh stop
```

### Windows 服务器

1. 安装 Python、Git、uv。

2. 拉取代码并安装依赖：

```powershell
git clone <your-repo-url>
cd ScriptsGenerateAgent
uv venv
uv pip install -r backend/requirements.txt
copy backend\.env.example backend\.env
```

编辑 `backend\.env`，填入 `API_KEY` 等变量。

3. 启动服务：

```powershell
start_backend.bat
start_frontend.bat
```

保持两个窗口打开即可。正式长期运行建议用 NSSM、Windows 服务或计划任务管理这两个命令。

### 绑定域名与反向代理

DNS 只负责把域名指到服务器。路径转发由 Nginx/Caddy/IIS 处理。

推荐路径：

```text
https://couvzob.kdns.fr/script  -> http://127.0.0.1:5001
https://couvzob.kdns.fr/api     -> http://127.0.0.1:5001
```

Nginx 示例：

```nginx
server {
    listen 80;
    server_name couvzob.kdns.fr;

    location /api/ {
        proxy_pass http://127.0.0.1:5001;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 600s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /script/ {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location = /script {
        return 301 /script/;
    }
}
```

若通过同域名 `/script/` 暴露前端，前端会自动把 API 指向同域名下的 `/script/api/*`。若前端和后端分属不同域名，或你希望显式指定 API 地址，可在 `frontend/index.html` 的脚本加载前注入：

```html
<script>
window.SCRIPT_AGENT_API_BASE_URL = 'https://api.example.com';
</script>
```

---

## 环境变量

复制模板后编辑：

```powershell
copy backend\.env.example backend\.env
```

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `API_KEY` | 火山引擎 ARK API Key | — |
| `BASE_URL` | API 接入点 | `https://ark.cn-beijing.volces.com/api/v3` |
| `MODEL` | 主模型名称 | `doubao-seed-2-0-lite-260215` |
| `SIMPLE_MODEL` | 片名、会议摘要、Story IR、Word 镜号规划使用的轻量模型 | `doubao-seed-2-0-lite-260428` |
| `STRUCTURED_MODEL_THINKING` | 方舟结构化 JSON Agent 的思考模式：`disabled` / `auto` / `enabled` | `disabled` |
| `FALLBACK_MODEL` | 主模型额度耗尽时使用的备用模型 | `doubao-seed-2-0-mini-260215` |
| `ENABLE_CINEMATOGRAPHY` | 是否开启摄影指导后处理 | `true` |
| `CINEMATOGRAPHY_MODEL` | 摄影智能体专用模型（留空则复用 MODEL） | — |
| `PERFORMANCE_MODEL` | 动作与表情专项智能体使用的模型（留空则复用 MODEL） | — |

---

## 使用流程

1. **建立场景池** — 选择一个或多个有摄影锚点的场景，再为每一幕指定场景；单场景仍兼容旧的 `scene_id` 请求。
2. **设置角色** — 指定角色数量（1-10），可填角色名/性格；GENERATE CAST 会参考整个场景池生成角色档案。
3. **选择模式与风格** — 正常模式输入创作灵感；直接模式粘贴已有 JSON/文本剧本并只做结构化；也可选剧本风格、剧情倾向、对白语言和镜头风格参考。
4. **开始生成** — ACTION! 运行完整流水线；摄影完成后，动作与表情专项 Agent 按最终画面逐镜头选择资源。DIRECTOR WORD 只生成可读分镜 Word。长编号分镜会按稳定事件 ID 分批处理并实时回传。
5. **下载与编辑** — 可下载单个产物或完整会话 ZIP；编辑后的剧本可调用同一合同进行校验。
6. **生成记录** — 可查看生成中/失败/成功状态、修改标题、一键复填输入、加载剧本及导出 Word。

---

## 项目结构

```
ScriptsGenerateAgent/
├── start_backend.bat / start_frontend.bat
├── backend/
│   ├── app.py                      # Flask 入口 + 所有 API 路由
│   ├── requirements.txt
│   ├── src/
│   │   ├── autogen_pipeline.py     # 阶段编排、分批、重试、发布门禁
│   │   ├── autogen_agents.py       # 模型路由、资源注入、兼容工厂
│   │   ├── agents/                 # 各 Agent 的创建入口与提示词
│   │   ├── autogen_bridge.py       # AutoGen ↔ Flask 流式桥接
│   │   ├── script_contract.py      # 剧本及跨文件最终合同
│   │   ├── schema.py               # 结构与镜头校验
│   │   ├── registry.py             # 历史状态、输入快照、产物索引
│   │   ├── word_exporter.py        # 剧本 → Word 导出
│   │   ├── resource_loader.py      # 场景/角色/动作资源
│   │   ├── json_generator.py       # 最终 JSON 序列化
│   │   ├── performance/            # 动作/表情专项 Agent 与安全回填
│   │   └── cinematography/         # 摄影指导后处理（3 阶段）
│   ├── resources/
│   │   ├── characters_resource.json
│   │   ├── scenes_resource.json
│   │   ├── actions_resource.json
│   │   ├── Images/                 # 角色预览图
│   │   └── cinematography/
│   └── outputs/                    # 旧版/调试产物，已被 Git 忽略
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/
│       ├── config.js               # API 端点 + APP_STATE
│       ├── api.js                  # fetch 封装
│       ├── ui.js                   # DOM 渲染
│       └── main.js                 # 事件绑定 + 业务流程
├── docs/                           # 合同、格式和历史设计文档
├── tests/                          # 回归测试
├── outputs/                        # 当前产物、.pending 与 registry.json（已忽略）
└── skills/                         # 写作风格提示词模板（*.md）
```

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 后端健康检查 |
| GET | `/api/scenes` | 场景列表（含区域/锚点） |
| GET | `/api/characters` | 角色库 |
| POST | `/api/characters` | 向角色库添加角色 |
| POST | `/api/generate_characters` | AI 生成角色表 |
| POST | `/api/generate` | 生成剧本（NDJSON 流式响应） |
| POST | `/api/generate_director_word` | 导演 Word 模式（NDJSON 流式响应） |
| GET | `/api/script_content/<filename>` | 读取已生成剧本内容 |
| POST | `/api/validate_script` | 校验编辑后的剧本 |
| GET | `/api/download/<filename>` | 下载 outputs 目录文件 |
| GET | `/api/download_word/<filename>` | 导出并下载 Word 版剧本 |
| GET | `/api/download_session/<session_id>` | 下载会话完整 ZIP |
| GET | `/api/position_plan/<session_id>` | 读取会话站位规划 |
| GET | `/api/history` | 历史生成会话列表 |
| PATCH | `/api/history/<session_id>/label` | 更新会话版本名称 |
| GET | `/api/character_image/<name>` | 角色预览图 |

### `/api/generate` 请求体

```json
{
  "scene_pool": ["LotusTown", "Auditorium"],
  "act_scenes": ["LotusTown", "Auditorium", "LotusTown"],
  "custom_characters": [],
  "creative_idea": "信任与背叛",
  "required_character_count": 3,
  "act_count": 3,
  "script_style_id": "auto",
  "script_tone_id": "",
  "dialogue_language": "mandarin",
  "shot_style_reference": "",
  "direct_mode": false
}
```

只选一个场景时仍可仅发送 `scene_id`。`direct_mode=true` 时，`creative_idea` 承载用户原始剧本，系统跳过创意会议和文学创作。

### `/api/generate` 流式响应（每行一个 JSON）

```
{ "type": "log", "level": "info", "message": "..." }
{ "type": "thinking_chunk", "agent": "DirectorAgent", "text": "..." }
{ "type": "thinking_done" }
{ "type": "success", "filename": "script_xxx.json", "camera_script_filename": "camera_script_xxx.json", "validation_filename": "validation_xxx.json", "session_id": "xxx", ... }
{ "type": "error", "message": "..." }
```

---

## 输出与发布门禁

生成文件先写入 `outputs/.pending/<run>/`。只有剧本、镜头、演员档案和站位文件通过统一结构与跨文件引用校验，才会移动到 `outputs/` 并登记为成功会话。

- `script_{ts}.json`：按幕组成的剧本数组。
- `camera_script_{ts}.json`：按 `shot_index` 对应幕、按 `event_index` 对应事件。
- `actors_profile_{ts}.json`：角色到 Unity `gameobject_name` 的映射。
- `position_plan_{ts}.json` / `position_detail_{ts}.json`：单幕直接输出对象，多幕输出 `{"scenes":[...]}`。
- `validation_{ts}.json`：最终合同错误与警告报告。

---

## 提交前检查

不要提交 `backend/.env`、API Key、`outputs/`、`backend/outputs/`、日志、虚拟环境、`.codex-runtime/`、本机 Conda 启动脚本或临时验收/报告渲染产物。摄影目录 `backend/resources/cinematography/**` 是离线生成的权威资源，提交前必须人工确认来源和坐标；不要把普通运行输出当作资源文件提交。

```bash
git status --short
git diff --check
git diff --stat
```

---

## 故障排除

| 现象 | 解决方法 |
|------|---------|
| `ModuleNotFoundError` | `uv pip install -r backend/requirements.txt --python .venv/Scripts/python.exe` |
| API Key 错误 | 检查 `backend/.env` 中 `API_KEY` 是否正确 |
| 端口被占用 | 修改 `backend/app.py` 末尾 `port=5001` |
| Word 导出失败 | 确认 `python-docx` 已安装到 `.venv` |
| 摄影指导未生效 | 联系开发者（已默认启用） |
