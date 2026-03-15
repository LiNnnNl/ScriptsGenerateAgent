# ScriptAgent 前端

电影胶片风格的 AI 剧本生成系统前端界面。

## 📁 文件结构

```
frontend/
├── index.html          # 主页面
├── css/
│   └── style.css       # 电影风格样式
└── js/
    ├── config.js       # API 地址和全局状态配置
    ├── api.js          # API 调用模块
    ├── ui.js           # UI 渲染和控制
    └── main.js         # 主逻辑和事件绑定
```

## 🚀 启动方式

确保后端已启动（默认 `http://localhost:5000`），然后：

```bash
# Python 3
python -m http.server 8080
```

访问 `http://localhost:8080`

## ⚙️ 配置

修改后端地址：编辑 `js/config.js`

```javascript
const API_CONFIG = {
    BASE_URL: 'http://localhost:5000',  // 改为你的后端地址
};
```

## 🎨 使用流程

页面加载后自动获取场景列表，按步骤操作：

| 步骤 | 操作 | 说明 |
|------|------|------|
| 01 | 选择场景 | 从下拉列表选择拍摄地点，解锁后续步骤 |
| 02 | 设定角色数 | 用 +/− 调整演员数量 |
| 03 | 填写角色 | 输入名称和性格描述（可选，留空由 AI 决定） |
| 04 | 输入创意 | 输入主题或氛围（可选，留空由 AI 自由发挥） |
| — | ACTION! | 点击金色按钮生成，实时查看日志，完成后下载 JSON |

## 🔧 技术栈

- 纯原生 JavaScript（无框架）
- CSS3 动画
- Fetch API + NDJSON 流式读取

## 🐛 常见问题

**页面无法加载场景**：检查后端是否启动，`config.js` 中 `BASE_URL` 是否正确。

**样式显示异常**：清除浏览器缓存，使用 Chrome 90+ / Firefox 88+ / Safari 14+。

**按钮无法点击**：打开浏览器控制台查看 JavaScript 错误。
