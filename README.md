# ScriptAgent

电影级 AI 剧本生成系统，基于 Flask + AutoGen 多智能体流水线。输入场景、角色表和创作想法，自动生成带镜头设计的完整剧本 JSON。

---

## 快速启动

### macOS / Linux

```bash
# 首次运行，创建虚拟环境并安装依赖
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 启动后端
python -m flask run --port 5001
```

### Windows（双击 bat）

```
start_backend.bat
```

> 摄影指导后处理默认启用（无需额外配置）。

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
| `ENABLE_CINEMATOGRAPHY` | 是否开启摄影指导后处理（默认已启用，可忽略） | `false` |
| `CINEMATOGRAPHY_MODEL` | 摄影智能体专用模型（留空则复用 MODEL） | — |
| `MODEL_FUNCTION_CALLING` | 是否启用工具调用版 ValidationAgent | `false` |

---

## 使用流程

1. **选择场景** — 从下拉列表选择拍摄场景
2. **设置角色** — 指定角色数量（2-10），可选填角色名/性格，点击 GENERATE CAST 让 AI 生成完整档案
3. **创作想法** — 输入主题/情节/氛围（可留空）；设置**剧本幕数**（默认 3 幕）
4. **点击 ACTION!** — 开始生成，日志实时流式输出
5. **下载** — 生成完成后可下载 Word 版剧本（仅对话+镜头描述）、位置规划等文件
6. **历史记录** — 点击右上角「历史记录」查看所有生成记录，可命名版本、加载剧本、下载 Word

---

## 项目结构

```
ScriptsGenerateAgent/
├── .venv/                          # uv 虚拟环境（Python 3.12）
├── start_backend.bat               # 一键启动
├── backend/
│   ├── app.py                      # Flask 入口 + 所有 API 路由
│   ├── requirements.txt
│   ├── src/
│   │   ├── autogen_pipeline.py     # 多 Agent 主流程
│   │   ├── autogen_agents.py       # Agent 创建函数（含幕数注入）
│   │   ├── autogen_bridge.py       # AutoGen ↔ Flask 流式桥接
│   │   ├── registry.py             # 历史会话注册表
│   │   ├── word_exporter.py        # 剧本 → Word 导出
│   │   ├── resource_loader.py      # 场景/角色/动作资源
│   │   ├── json_generator.py       # 最终 JSON 序列化
│   │   └── cinematography/         # 摄影指导后处理（3 阶段）
│   ├── resources/
│   │   ├── characters_resource.json
│   │   ├── scenes_resource.json
│   │   ├── actions_resource.json
│   │   ├── Images/                 # 角色预览图
│   │   └── cinematography/
│   └── outputs/                    # 生成文件（JSON + docx）+ registry.json
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/
│       ├── config.js               # API 端点 + APP_STATE
│       ├── api.js                  # fetch 封装
│       ├── ui.js                   # DOM 渲染
│       └── main.js                 # 事件绑定 + 业务流程
└── skills/                         # 写作风格提示词模板（*.md）
```

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/scenes` | 场景列表（含区域/锚点） |
| GET | `/api/characters` | 角色库 |
| POST | `/api/characters` | 向角色库添加角色 |
| POST | `/api/generate_characters` | AI 生成角色表 |
| POST | `/api/generate` | 生成剧本（NDJSON 流式响应） |
| GET | `/api/script_content/<filename>` | 读取已生成剧本内容 |
| GET | `/api/download/<filename>` | 下载 outputs 目录文件 |
| GET | `/api/download_word/<filename>` | 导出并下载 Word 版剧本 |
| GET | `/api/history` | 历史生成会话列表 |
| PATCH | `/api/history/<session_id>/label` | 更新会话版本名称 |
| GET | `/api/character_image/<name>` | 角色预览图 |

### `/api/generate` 请求体

```json
{
  "scene_id": "LotusTown",
  "custom_characters": [...],
  "creative_idea": "信任与背叛",
  "required_character_count": 3,
  "act_count": 3
}
```

### `/api/generate` 流式响应（每行一个 JSON）

```
{ "type": "log", "level": "info", "message": "..." }
{ "type": "thinking_chunk", "agent": "DirectorAgent", "text": "..." }
{ "type": "thinking_done" }
{ "type": "success", "filename": "script_xxx.json", "session_id": "xxx", ... }
{ "type": "error", "message": "..." }
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
