# ScriptAgent 后端 API

基于 Flask 的 RESTful API 服务器，为前端提供剧本生成功能。

## 📁 目录结构

```
backend/
├── app.py                      # Flask 应用主程序
├── requirements.txt            # Python 依赖
├── .env.example                # 环境变量示例
├── src/                        # 核心模块
│   ├── resource_loader.py      # 资源加载器
│   ├── director_ai.py          # AI 导演
│   └── json_generator.py       # JSON 生成器
├── resources/                  # 资源库
│   ├── characters_resource.json
│   ├── scenes_resource.json
│   └── actions_resource.json
└── outputs/                    # 生成的剧本文件
```

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入你的 API Key：

```bash
copy .env.example .env
```

`.env` 内容：

```
API_KEY=your-actual-api-key
BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3
MODEL=ark-code-latest
```

### 3. 启动服务器

```bash
python app.py
```

服务器运行在 `http://localhost:5000`

## 📡 API 接口

### GET /api/scenes

获取所有可用场景列表。

**响应示例：**
```json
{
  "success": true,
  "data": [
    {
      "id": "scene_school_rooftop",
      "name": "学校天台",
      "description": "午后的学校天台...",
      "positions": [
        {"id": "Position 1", "description": "天台中央", "is_sittable": false}
      ]
    }
  ]
}
```

### POST /api/generate

生成剧本（流式响应）。

**请求体：**
```json
{
  "custom_characters": [
    {"name": "张三", "description": "沉默寡言的侦探"},
    {"name": "李四", "description": ""}
  ],
  "scene_id": "scene_school_rooftop",
  "creative_idea": "可选的创作想法"
}
```

- `custom_characters`：自定义角色列表，`name` 为空或数组为空时 AI 自由创作
- `scene_id`：必填，场景 ID
- `creative_idea`：可选，留空则 AI 完全自由发挥

**响应格式：** NDJSON（每行一个 JSON 对象）

```json
{"type": "log", "level": "info", "message": "开始生成..."}
{"type": "thinking", "message": "AI 正在思考..."}
{"type": "success", "filename": "script_123456.json", "warnings": []}
{"type": "error", "message": "错误信息", "details": {}}
```

### GET /api/download/:filename

下载生成的剧本文件。

## 📝 资源文件格式

### 场景资源 (`scenes_resource.json`)

```json
{
  "id": "唯一标识",
  "name": "场景名称",
  "description": "场景描述",
  "valid_positions": [
    {
      "id": "Position 1",
      "description": "语义化描述",
      "is_sittable": false
    }
  ]
}
```

### 角色资源 (`characters_resource.json`)

```json
{
  "id": "唯一标识",
  "name": "角色名称",
  "style_tag": "画风标签",
  "description": "外观描述",
  "personality": "性格描述（AI 生成对白的依据）"
}
```

### 动作资源 (`actions_resource.json`)

```json
{
  "action_id": "动作ID",
  "category": "分类",
  "description": "详细描述（AI 选择的依据）",
  "compatible_states": ["standing", "sitting"]
}
```

## 🔧 配置

### 端口

默认端口 5000，修改 `app.py` 最后一行：

```python
app.run(debug=True, host='0.0.0.0', port=你的端口号)
```

### CORS

默认允许所有来源。生产环境建议在 `app.py` 中限制：

```python
CORS(app, resources={r"/api/*": {"origins": ["http://your-domain.com"]}})
```

## 🐛 故障排除

**ImportError**：确保在 `backend/` 目录下运行 `python app.py`

**API Key 错误**：检查 `.env` 文件是否存在且 Key 正确

**CORS 错误**：确保已安装 `flask-cors`（`pip install flask-cors`）

## 🔒 安全建议

1. 不要将 `.env` 提交到版本控制（已在 `.gitignore` 中排除）
2. 生产环境使用 HTTPS
3. 限制 CORS 允许的域名
