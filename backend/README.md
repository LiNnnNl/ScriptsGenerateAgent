# ScriptAgent 后端

基于 Flask + AutoGen 的 AI 剧本生成服务，包含导演、批评、对白、摄影指导等多智能体流水线。

## 快速启动

### 方式一：双击 bat 文件（推荐）

项目根目录下分别双击：

```
start_backend.bat    # 启动后端，运行在 http://localhost:5000
start_frontend.bat   # 启动前端，运行在 http://localhost:8080
```

然后用浏览器打开 `http://localhost:8080` 即可使用。

### 方式二：手动启动

```bash
# 1. 进入 backend 目录
cd backend

# 2. 安装依赖（首次运行）
pip install -r requirements.txt

# 3. 配置环境变量（首次运行）
copy .env.example .env
# 用文本编辑器打开 .env，填入你的 API Key

# 4. 启动后端
python app.py
```

另开一个终端启动前端：

```bash
cd frontend
python -m http.server 8080
```

## 环境变量配置（.env）

| 变量 | 说明 | 示例 |
|------|------|------|
| `API_KEY` | 火山引擎 ARK API Key | `your_api_key_here` |
| `BASE_URL` | API 接入点 | `https://ark.cn-beijing.volces.com/api/coding/v3` |
| `MODEL` | 主模型名称 | `ark-code-latest` |
| `ENABLE_CINEMATOGRAPHY` | 是否开启摄影指导后处理 | `false` / `true` |
| `CINEMATOGRAPHY_MODEL` | 摄影智能体专用模型（可选，不填则复用 MODEL） | — |

API Key 在 [火山引擎控制台](https://console.volcengine.com/ark) 获取。

## 目录结构

```
backend/
├── app.py                          # Flask 入口
├── requirements.txt
├── .env.example                    # 环境变量模板
├── src/
│   ├── autogen_pipeline.py         # 主生成流水线
│   ├── autogen_agents.py           # 各智能体定义
│   ├── resource_loader.py          # 资源加载
│   ├── json_generator.py           # 剧本格式化输出
│   └── cinematography/             # 摄影指导后处理（3阶段）
│       ├── __init__.py
│       ├── shot_planning_stage.py
│       ├── cinematography_position_stage.py
│       └── camera_planning_stage.py
├── resources/
│   ├── characters_resource.json    # 角色库
│   ├── scenes_resource.json        # 场景语义信息
│   ├── actions_resource.json       # 动作库
│   └── cinematography/
│       ├── CameraLib.json          # 摄影机参数库
│       ├── LayoutLib.json          # 站位布局库
│       └── scene_info/             # 各场景 Unity 坐标信息
└── outputs/                        # 生成的剧本 / 角色档案
```

## 故障排除

- **`ModuleNotFoundError`**：在 `backend/` 目录下运行 `pip install -r requirements.txt`
- **API Key 错误**：检查 `.env` 文件是否存在、Key 是否正确填写
- **端口被占用**：修改 `app.py` 末尾 `port=5000` 为其他端口，前端 `start_frontend.bat` 中的 `8080` 同理
