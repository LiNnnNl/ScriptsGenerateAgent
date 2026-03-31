# ScriptAgent - AI 剧本生成系统

基于火山引擎 ARK Doubao 的智能剧本生成工具，电影胶片风格界面，多 Agent 协作架构。

## 🎬 项目特色

- **电影级 UI 设计**：模拟真实电影胶片的视觉效果
- **多 Agent 协作**：Director / Critic / Dialogue / Validation 四个 Agent 协同创作
- **资源驱动架构**：场景和动作约束 AI 输出，保证可执行性
- **实时生成日志**：观看 AI 的创作思考过程
- **单服务部署**：Flask 同时托管前端与 API，无需额外配置

## 🏗️ 架构设计

```
ScriptAgent/
├── frontend/                      # 前端（电影风格UI）
│   ├── index.html
│   ├── css/style.css
│   ├── js/
│   │   ├── config.js
│   │   ├── api.js
│   │   ├── ui.js
│   │   └── main.js
│   └── README.md
│
├── backend/                       # 后端 API + 静态文件服务
│   ├── app.py                     # Flask 应用（端口 8080）
│   ├── requirements.txt
│   ├── .env.example
│   ├── .gitignore
│   ├── src/
│   │   ├── resource_loader.py
│   │   ├── autogen_agents.py      # Agent 定义
│   │   ├── autogen_pipeline.py    # 多 Agent 流程编排
│   │   ├── autogen_bridge.py      # 异步→同步流式桥接
│   │   ├── autogen_tools.py       # 验证工具
│   │   ├── director_ai.py
│   │   └── json_generator.py
│   ├── resources/
│   │   ├── characters_resource.json
│   │   ├── scenes_resource.json
│   │   └── actions_resource.json
│   └── outputs/                   # 生成的剧本
│
└── README.md                      # 本文件
```

## 🚀 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cd backend
copy .env.example .env
# 编辑 .env，填入你的 API Key
```

`.env` 内容示例：

```
API_KEY=your-api-key-here
BASE_URL=https://ark.cn-beijing.volces.com/api/v3
MODEL=doubao-seed-2-0-lite-260215
```

API Key 在 [火山引擎 ARK 控制台](https://console.volcengine.com/ark) 获取。

### 3. 启动服务

```bash
cd backend
python app.py
```

访问 `http://localhost:8080` 即可使用。

### 4. 内网穿透（可选）

使用 [ngrok](https://ngrok.com) 将服务暴露到公网：

```bash
ngrok http 8080
```

运行后获得公网地址，发给对方直接访问即可。

## 🎨 使用流程

1. **选择场景** → 从下拉列表选择拍摄地点
2. **设定角色数** → 用 +/− 按钮调整演员数量
3. **填写角色** → （可选）输入角色名称和性格描述，留空由 AI 自由创作
4. **输入创意** → （可选）输入主题、情节或氛围，留空由 AI 自由发挥
5. **ACTION!** → 点击金色大按钮开始生成
6. **观看日志** → 实时查看 AI 创作过程
7. **下载剧本** → 获取生成的 JSON 文件

## 📦 资源文件说明

### 场景资源 (`backend/resources/scenes_resource.json`)

定义可用场景和语义化点位：

```json
{
  "id": "scene_school_rooftop",
  "name": "学校天台",
  "description": "午后的学校天台，能看到城市全景，微风轻拂。",
  "valid_positions": [
    {
      "id": "Position 1",
      "description": "天台中央，视野开阔，适合对峙、宣言",
      "is_sittable": false
    }
  ]
}
```

### 角色资源 (`backend/resources/characters_resource.json`)

定义预设角色的性格描述（供 AI 参考）：

```json
{
  "id": "char_001",
  "name": "角色名",
  "style_tag": "画风标签",
  "description": "外观描述",
  "personality": "性格描述"
}
```

### 动作资源 (`backend/resources/actions_resource.json`)

通用动作库，AI 从中选择角色动作：

```json
{
  "action_id": "Talk_Angry_Point",
  "category": "Talking",
  "description": "愤怒地指责对方，手臂用力前指",
  "compatible_states": ["standing"]
}
```

## 🎯 技术栈

### 前端
- 原生 JavaScript (ES6+)
- CSS3 动画
- Fetch API

### 后端
- Python 3.8+
- Flask + Flask-CORS
- AutoGen（多 Agent 编排）
- OpenAI SDK（兼容火山引擎 ARK）

### AI
- 火山引擎 ARK / Doubao-1.5-Pro
- 多 Agent 协作（Director → Critic → Dialogue → Validation → Output）
- 流式响应（NDJSON）

## 🛠️ API 接口

### GET /api/scenes
获取所有可用场景列表

**响应示例：**
```json
{
  "success": true,
  "data": [
    {
      "id": "scene_school_rooftop",
      "name": "学校天台",
      "description": "午后的学校天台...",
      "positions": [...]
    }
  ]
}
```

### POST /api/generate
生成剧本（流式响应）

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

`custom_characters` 留空数组或 name 为空时，AI 自由创作角色。

**响应格式：** NDJSON，每行一个 JSON 对象：

```json
{"type": "log", "level": "info", "message": "开始生成..."}
{"type": "thinking", "message": "AI 正在思考..."}
{"type": "success", "filename": "script_123456.json", "warnings": []}
{"type": "error", "message": "错误信息", "details": {}}
```

### GET /api/download/:filename
下载生成的剧本文件

## 📝 输出格式

生成的剧本保存在 `backend/outputs/` 目录，JSON 格式包含：

- **场景信息**：地点、剧情概述
- **对白片段**：说话者、内容、动作、位置
- **移动片段**：角色移动指令
- **氛围描述**：场景氛围和运镜建议

## 🔧 扩展开发

### 添加新场景

在 `backend/resources/scenes_resource.json` 中添加条目，填写 `id`、`name`、`description` 和 `valid_positions` 即可，重启后端生效。

### 自定义 UI 主题

编辑 `frontend/css/style.css` 中的 CSS 变量：

```css
:root {
    --gold: #d4af37;
    --gold-light: #f4e4a6;
    --black: #0a0a0a;
}
```

### 切换 AI 模型

修改 `backend/.env` 中的 `MODEL` 和 `BASE_URL` 即可，无需改动代码。

## 🐛 故障排除

**生成失败**：检查 `backend/.env` 中的 API Key 是否正确

**端口占用**：若 8080 被占用，修改 `backend/app.py` 末尾的 `port=8080` 为其他端口

**局域网其他设备无法访问**：以管理员身份开放防火墙端口：
```powershell
netsh advfirewall firewall add rule name="ScriptAgent" dir=in action=allow protocol=TCP localport=8080
```

**样式异常**：清除浏览器缓存，使用 Chrome 90+ / Firefox 88+ / Safari 14+

## 🌟 致谢

- UI 设计灵感来自经典好莱坞电影
- AI 技术由火山引擎 ARK / Doubao 提供
- 字体：Cinzel & Roboto Mono (Google Fonts)

## 📜 许可证

MIT License

---

**Enjoy creating cinematic scripts with AI!** 🎬✨
