# ScriptAgent 后端 API

基于 Flask 的 RESTful API 服务器，为前端提供剧本生成功能。

## 📁 目录结构

```
backend/
├── app.py                      # Flask 应用主程序
├── requirements.txt            # Python 依赖
├── .env.example               # 环境变量示例
├── src/                       # 核心模块
│   ├── __init__.py
│   ├── resource_loader.py     # 资源加载器
│   ├── director_ai.py         # AI 导演
│   └── json_generator.py      # JSON 生成器
└── resources/                 # 资源库
    ├── characters_resource.json
    ├── scenes_resource.json
    └── actions_resource.json
```

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入你的 API Key：

```bash
DEEPSEEK_API_KEY=your-actual-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

### 3. 启动服务器

```bash
python app.py
```

服务器将运行在 `http://localhost:5000`

## 📡 API 接口

### GET /api/styles

获取所有可用画风。

**响应示例：**
```json
{
  "success": true,
  "data": ["Cyberpunk_Realism", "Anime_2D", "Fantasy_3D"]
}
```

### GET /api/scenes/:style_tag

获取指定画风的场景列表。

**参数：**
- `style_tag` - 画风标签

**响应示例：**
```json
{
  "success": true,
  "data": [
    {
      "id": "scene_space_station_isolation",
      "name": "太空站-隔离室1F",
      "description": "冰冷的金属走廊...",
      "positions": [...]
    }
  ]
}
```

### GET /api/characters/:style_tag

获取指定画风的角色列表。

**参数：**
- `style_tag` - 画风标签

**响应示例：**
```json
{
  "success": true,
  "data": [
    {
      "id": "char_001_xu",
      "name": "序一号",
      "description": "冷静的指挥官...",
      "personality": "理智、冷漠..."
    }
  ]
}
```

### POST /api/generate

生成剧本（流式响应）。

**请求体：**
```json
{
  "character_ids": ["char_001_xu", "char_002_lan"],
  "scene_id": "scene_space_station_isolation",
  "creative_idea": "可选的创作想法"
}
```

**响应格式：** NDJSON (Newline Delimited JSON)

每行一个 JSON 对象，类型包括：

```json
{"type": "log", "level": "info", "message": "开始生成..."}
{"type": "thinking", "message": "AI 正在思考..."}
{"type": "success", "filename": "script_123.json", "warnings": []}
{"type": "error", "message": "错误信息", "details": {}}
```

### GET /api/download/:filename

下载生成的剧本文件。

**参数：**
- `filename` - 文件名

## 🔧 配置

### CORS 设置

默认允许所有来源访问。生产环境建议修改 `app.py` 中的 CORS 配置：

```python
CORS(app, resources={
    r"/api/*": {
        "origins": ["http://your-frontend-domain.com"],  # 指定允许的域名
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})
```

### 端口配置

默认端口为 5000。修改 `app.py` 最后一行：

```python
app.run(debug=True, host='0.0.0.0', port=你的端口号)
```

## 📝 资源文件格式

### 角色资源

```json
{
  "id": "唯一标识",
  "name": "角色名称",
  "style_tag": "画风标签",
  "description": "外观描述",
  "personality": "性格描述（AI 生成对白的依据）"
}
```

### 场景资源

```json
{
  "id": "唯一标识",
  "name": "场景名称",
  "style_tag": "画风标签",
  "description": "场景描述",
  "valid_positions": [
    {
      "id": "Position 7",
      "description": "语义化描述",
      "is_sittable": false
    }
  ]
}
```

### 动作资源

```json
{
  "action_id": "动作ID",
  "category": "分类",
  "description": "详细描述（AI 选择的依据）",
  "compatible_states": ["standing", "sitting"]
}
```

## 🐛 故障排除

### 导入错误

确保在 `backend` 目录下运行：

```bash
cd backend
python app.py
```

### API Key 错误

检查 `.env` 文件是否存在且配置正确。

### CORS 错误

确保已安装 `flask-cors`：

```bash
pip install flask-cors
```

## 📊 输出文件

生成的剧本保存在 `outputs/` 目录，格式符合 `scene_json_spec.md` 规范。

## 🔒 安全建议

1. 不要将 `.env` 文件提交到版本控制
2. 生产环境使用 HTTPS
3. 限制 CORS 允许的域名
4. 使用环境变量管理敏感信息
5. 定期更新依赖包

## 📚 相关文档

- [总体 README](../README.md)
- [前端文档](../frontend/README.md)
- [JSON 规范](../scene_json_spec.md)
