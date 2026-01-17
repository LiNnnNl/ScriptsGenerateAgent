# ScriptAgent 前端

电影胶片风格的 AI 剧本生成系统前端界面。

## 🎬 特性

- **电影胶片风格设计**：模拟真实电影胶片的视觉效果
- **高级交互体验**：流畅的动画和过渡效果
- **实时日志显示**：观看 AI 创作过程
- **前后端分离**：完全独立的前端应用

## 📁 文件结构

```
frontend/
├── index.html          # 主页面
├── css/
│   └── style.css       # 电影风格样式
├── js/
│   ├── config.js       # 配置文件
│   ├── api.js          # API 调用模块
│   ├── ui.js           # UI 控制模块
│   └── main.js         # 主逻辑
└── README.md          # 本文件
```

## 🚀 使用方法

### 方式一：直接打开（需要后端支持）

1. 确保后端服务已启动（默认 http://localhost:5000）
2. 用浏览器直接打开 `index.html`

### 方式二：使用本地服务器

推荐使用简单的 HTTP 服务器：

```bash
# Python 3
python -m http.server 8080

# Node.js (需要先安装 http-server)
npx http-server -p 8080
```

然后访问 `http://localhost:8080`

## ⚙️ 配置

编辑 `js/config.js` 修改后端 API 地址：

```javascript
const API_CONFIG = {
    BASE_URL: 'http://localhost:5000',  // 修改为你的后端地址
    // ...
};
```

## 🎨 设计特点

### 电影胶片元素

- **胶片边框**：左右两侧模拟胶片孔
- **金色主题**：好莱坞经典金色调
- **装饰角**：卡片四角的装饰边框
- **字体选择**：Cinzel (衬线) + Roboto Mono (等宽)

### 交互动画

- 步骤渐显动画
- 卡片悬停效果
- 按钮光泽扫过效果
- 日志淡入动画

### 响应式设计

- 适配桌面和移动设备
- 流式布局
- 自适应网格

## 🔧 技术栈

- **纯原生 JavaScript**：无框架依赖
- **CSS3 动画**：流畅的视觉效果
- **Fetch API**：现代化的网络请求
- **响应式设计**：适配多种设备

## 📝 注意事项

1. 需要现代浏览器支持（Chrome 90+, Firefox 88+, Safari 14+）
2. 确保后端已启用 CORS
3. 首次使用需要配置 DeepSeek API Key

## 💡 自定义

### 修改主题颜色

编辑 `css/style.css` 中的 CSS 变量：

```css
:root {
    --gold: #d4af37;          /* 金色 */
    --gold-light: #f4e4a6;    /* 浅金色 */
    --black: #0a0a0a;         /* 背景黑 */
    /* ... */
}
```

### 调整动画速度

在 `css/style.css` 中修改 `transition` 和 `animation` 的时间参数。

## 🐛 常见问题

**Q: 页面无法加载数据？**
A: 检查后端服务是否启动，CORS 是否配置正确。

**Q: 样式显示异常？**
A: 清除浏览器缓存，确保 CSS 文件正确加载。

**Q: 按钮无法点击？**
A: 检查浏览器控制台是否有 JavaScript 错误。
