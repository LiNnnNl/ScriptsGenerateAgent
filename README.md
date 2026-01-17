# ScriptAgent - AI 剧本生成系统

基于 DeepSeek AI 的智能剧本生成工具，电影胶片风格界面，前后端分离架构。

## 🎬 项目特色

- **电影级 UI 设计**：模拟真实电影胶片的视觉效果
- **智能剧本生成**：基于 DeepSeek AI 的创作引擎
- **资源驱动架构**：UI 约束视觉风格，AI 调度行为
- **实时生成日志**：观看 AI 的创作思考过程
- **前后端分离**：现代化的系统架构

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
├── backend/                       # 后端API
│   ├── app.py                     # Flask应用
│   ├── requirements.txt
│   ├── .env.example
│   ├── .gitignore
│   ├── src/
│   │   ├── resource_loader.py
│   │   ├── director_ai.py
│   │   └── json_generator.py
│   ├── resources/
│   │   ├── characters_resource.json
│   │   ├── scenes_resource.json
│   │   └── actions_resource.json
│   ├── outputs/                   # 生成的剧本
│   └── README.md
│
├── main.py                        # 命令行版本
├── start_backend.bat              # 启动后端
├── start_frontend.bat             # 启动前端
└── README.md                      # 本文件
```

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

在 `backend` 目录下创建 `.env` 文件：

```bash
cd backend
copy .env.example .env
# 然后编辑 .env 文件，填入你的 API Key
```

或手动创建 `backend/.env`：

```bash
DEEPSEEK_API_KEY=your-api-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

### 3. 启动后端

```bash
# 方式1: 使用启动脚本（推荐）
start_backend.bat

# 方式2: 命令行
cd backend
python app.py
```

后端将运行在 `http://localhost:5000`

### 4. 启动前端

```bash
# 方式1: 使用启动脚本（推荐）
start_frontend.bat

# 方式2: 命令行
cd frontend
python -m http.server 8080
```

然后访问 `http://localhost:8080`

## 🎨 使用流程

1. **选择画风** → 点击喜欢的视觉风格
2. **选择场景** → 从下拉列表选择拍摄地点
3. **设定角色数** → 增减按钮调整演员数量
4. **选择角色** → 点击卡片选择参演角色
5. **输入创意** → （可选）输入主题或留空由 AI 发挥
6. **ACTION!** → 点击金色大按钮开始生成
7. **观看日志** → 实时查看 AI 创作过程
8. **下载剧本** → 获取生成的 JSON 文件

## 📦 资源文件说明

### 角色资源 (`backend/resources/characters_resource.json`)

定义可用角色，包含画风标签和性格描述：

```json
{
  "id": "char_001_xu",
  "name": "序一号",
  "style_tag": "Cyberpunk_Realism",
  "description": "冷静的指挥官，半机械人",
  "personality": "理智、冷漠、讲究逻辑"
}
```

### 场景资源 (`backend/resources/scenes_resource.json`)

定义可用场景和语义化点位：

```json
{
  "id": "scene_space_station",
  "name": "太空站-隔离室",
  "style_tag": "Cyberpunk_Realism",
  "valid_positions": [
    {
      "id": "Position 7",
      "description": "中央主控台，适合发号施令",
      "is_sittable": false
    }
  ]
}
```

### 动作资源 (`backend/resources/actions_resource.json`)

通用动作库，不区分画风：

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
- 响应式设计

### 后端
- Python 3.8+
- Flask (Web框架)
- Flask-CORS (跨域支持)
- OpenAI SDK (兼容 DeepSeek)

### AI
- DeepSeek API
- 流式响应
- 提示词工程

## 🎬 电影风格设计元素

- **胶片孔装饰**：左右边框模拟真实胶片
- **金色主题**：好莱坞经典金色调
- **八边形数字**：独特的步骤编号
- **光泽扫过**：按钮悬停的光影效果
- **渐变卡片**：深色渐变背景
- **等宽字体**：日志区域使用 Roboto Mono
- **装饰性边角**：卡片四角的金色边框

## 🛠️ API 接口

### GET /api/styles
获取所有可用画风

### GET /api/scenes/:style_tag
获取指定画风的场景列表

### GET /api/characters/:style_tag
获取指定画风的角色列表

### POST /api/generate
生成剧本（流式响应）

**请求体：**
```json
{
  "character_ids": ["char_001", "char_002"],
  "scene_id": "scene_001",
  "creative_idea": "可选的创作想法"
}
```

### GET /api/download/:filename
下载生成的剧本文件

## 🔧 扩展开发

### 添加新画风

1. 在 `backend/resources/characters_resource.json` 添加该画风的角色
2. 在 `backend/resources/scenes_resource.json` 添加该画风的场景
3. 动作库自动通用，无需修改

### 自定义UI主题

编辑 `frontend/css/style.css` 中的 CSS 变量：

```css
:root {
    --gold: #d4af37;          /* 主题色 */
    --gold-light: #f4e4a6;    /* 高亮色 */
    --black: #0a0a0a;         /* 背景色 */
}
```

### 集成其他 AI 模型

修改 `backend/src/director_ai.py` 中的 API 调用部分即可。

## 📝 输出格式

生成的剧本保存在 `backend/outputs/` 目录，JSON 格式符合 `scene_json_spec.md` 规范，包含：

- **场景信息**：角色、地点、剧情概述
- **对白片段**：说话者、内容、动作、位置
- **移动片段**：角色移动指令
- **氛围描述**：场景氛围和运镜建议

## 🐛 故障排除

### 跨域问题
确保后端已安装并启用 flask-cors

### API 连接失败
检查 `frontend/js/config.js` 中的 `BASE_URL` 配置

### 样式异常
清除浏览器缓存，确保使用现代浏览器

## 📄 命令行版本

如果不想使用 Web UI，可以使用命令行：

```bash
# 交互式模式
python main.py --mode interactive

# 配置文件模式
python main.py --mode config --config example_config.json
```

## 🌟 致谢

- UI 设计灵感来自经典好莱坞电影
- AI 技术由 DeepSeek 提供
- 字体：Cinzel & Roboto Mono (Google Fonts)

## 📜 许可证

MIT License

---

**Enjoy creating cinematic scripts with AI!** 🎬✨
