# ScriptAgent - AI 剧本生成系统

基于 DeepSeek AI 的智能剧本生成工具，通过资源驱动的方式自动生成符合规范的场景脚本 JSON。

## 📋 核心设计理念

**UI 负责约束视觉风格（画风/场景/角色），AI 负责调度通用行为（动作/走位）。**

- **角色资源**: 定义演员池，包含画风标签和性格描述
- **场景资源**: 定义舞台空间，包含画风标签和语义化点位
- **动作资源**: 通用表演库，不区分画风，所有角色共用

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 设置 API Key

创建 `.env` 文件：

```bash
DEEPSEEK_API_KEY=your-api-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

### 3. 运行

**方式一：Web UI（推荐）**

```bash
python app.py
```

然后在浏览器中访问 `http://localhost:5000`

**方式二：命令行（交互式模式）**

```bash
python main.py --mode interactive --output my_script.json
```

系统将引导你：
1. 选择画风（Cyberpunk_Realism, Anime_2D, Fantasy_3D 等）
2. 选择场景（如：太空站-隔离室）
3. 选择角色（可多选，必须与场景画风一致）
4. 输入剧情大纲（多行输入，输入 `END` 结束）

### 4. 运行（配置文件模式）

```bash
python main.py --mode config --config example_config.json --output output_script.json
```

配置文件格式：

```json
{
  "character_ids": ["char_001_xu", "char_002_lan"],
  "scene_id": "scene_space_station_isolation",
  "plot_outline": "你的剧情大纲..."
}
```

## 📁 项目结构

```
ScriptAgent/
├── resources/                      # 资源文件目录
│   ├── characters_resource.json    # 角色资源库
│   ├── scenes_resource.json        # 场景资源库
│   └── actions_resource.json       # 动作资源库（通用）
├── src/                            # 核心代码
│   ├── __init__.py
│   ├── resource_loader.py          # 资源加载和验证
│   ├── director_ai.py              # 导演AI核心逻辑
│   └── json_generator.py           # JSON生成器
├── main.py                         # 主程序入口
├── example_config.json             # 配置文件示例
├── scene_json_spec.md              # JSON格式规范文档
├── requirements.txt                # Python依赖
└── README.md                       # 本文件
```

## 🎨 资源文件说明

### 角色资源 (`characters_resource.json`)

定义可用的角色，包含：
- `id`: 唯一标识符
- `name`: 角色名称
- `style_tag`: 画风标签（用于UI筛选）
- `description`: 外观描述
- `personality`: 性格描述（AI生成对白的依据）

**示例：**

```json
{
  "id": "char_001_xu",
  "name": "序一号",
  "style_tag": "Cyberpunk_Realism",
  "description": "冷静的指挥官，半机械人。",
  "personality": "理智、冷漠、讲究逻辑，说话不带感情色彩。"
}
```

### 场景资源 (`scenes_resource.json`)

定义可用的场景，包含：
- `id`: 唯一标识符
- `name`: 场景名称
- `style_tag`: 画风标签（必须与角色匹配）
- `description`: 场景描述
- `valid_positions`: 语义化点位列表
  - `id`: 点位ID（如 "Position 7"）
  - `description`: 语义描述（如 "中央主控台，适合发号施令"）
  - `is_sittable`: 是否可坐

**示例：**

```json
{
  "id": "scene_space_station_isolation",
  "name": "太空站-隔离室1F",
  "style_tag": "Cyberpunk_Realism",
  "valid_positions": [
    {
      "id": "Position 7",
      "description": "中央主控台前方，适合发号施令、对峙",
      "is_sittable": false
    }
  ]
}
```

### 动作资源 (`actions_resource.json`)

定义通用动作库（不区分画风），包含：
- `action_id`: 动作ID
- `category`: 分类（Idle, Talking, Emotion, Movement等）
- `description`: 详细描述（AI选择动作的依据）
- `compatible_states`: 兼容的状态（standing, sitting）

**示例：**

```json
{
  "action_id": "Talk_Angry_Point",
  "category": "Talking",
  "description": "愤怒地指责对方，手臂用力前指，身体前倾。适合争吵、命令、威胁。",
  "compatible_states": ["standing"]
}
```

## 🤖 工作流程

1. **输入层（UI）**
   - 用户选择画风
   - 在该画风下选择场景和角色
   - 输入剧情大纲

2. **决策层（Director AI）**
   - 读取角色性格 → 知道怎么说话
   - 读取场景点位 → 知道往哪走
   - 读取通用动作 → 知道怎么表演
   - 生成中间态剧本指令

3. **输出层（JSON Generator）**
   - 将AI指令转换为符合 `scene_json_spec.md` 的标准格式
   - 自动维护角色位置和状态

## 📝 输出格式

生成的 JSON 符合 `scene_json_spec.md` 规范，包含：

```json
[
  {
    "scene information": {
      "who": ["角色列表"],
      "where": "场景名称",
      "what": "剧情概述"
    },
    "scene": [
      {
        "speaker": "角色名称",
        "content": "对白内容",
        "shot": "character",
        "actions": [...],
        "current position": [...]
      }
    ]
  }
]
```

## 🔧 扩展指南

### 添加新角色

编辑 `resources/characters_resource.json`，添加新条目：

```json
{
  "id": "char_999_new",
  "name": "新角色",
  "style_tag": "你的画风标签",
  "description": "外观描述",
  "personality": "性格描述（越详细，AI生成的对白越准确）"
}
```

### 添加新场景

编辑 `resources/scenes_resource.json`，添加新条目，注意定义清晰的语义化点位。

### 添加新动作

编辑 `resources/actions_resource.json`，添加新条目。关键是 `description` 字段要详细，这是AI选择动作的唯一依据。

## ⚙️ 配置参数

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--mode` | 运行模式：interactive 或 config | interactive |
| `--config` | 配置文件路径 | - |
| `--api-key` | Anthropic API Key | 从环境变量读取 |
| `--output` | 输出文件路径 | output_script.json |

### 环境变量

- `ANTHROPIC_API_KEY`: Anthropic API密钥

## 🛡️ 防错机制

1. **画风校验**: 系统强制要求角色和场景的 `style_tag` 必须一致
2. **资源验证**: AI 只能从资源文件提供的 ID 中选择，杜绝生成不存在的资源
3. **状态追踪**: 自动维护角色的位置和状态（standing/sitting）
4. **规范验证**: 输出前自动验证是否符合 `scene_json_spec.md`

## 📖 示例

查看 `example_config.json` 了解配置文件示例。

运行示例：

```bash
python main.py --mode config --config example_config.json --output example_output.json
```

## 🐛 故障排除

### API调用失败

- 检查 API Key 是否正确设置
- 检查网络连接
- 检查 API 配额

### 生成的剧本不符合预期

- 优化剧情大纲的描述，更详细、更具体
- 调整角色的 `personality` 描述
- 检查场景点位的语义描述是否清晰

### 验证失败

- 检查角色和场景的画风标签是否一致
- 确保所有引用的 ID 在资源文件中存在

## 📄 许可证

本项目为技术方案演示代码。

## 🙏 致谢

本系统基于 Anthropic Claude AI 构建。

