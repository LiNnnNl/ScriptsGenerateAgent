// API配置
const API_CONFIG = {
    BASE_URL: `http://${window.location.hostname}:5000`,  // 自动适配局域网IP
    ENDPOINTS: {
        SCENES: '/api/scenes',
        GENERATE: '/api/generate',
        DOWNLOAD: '/api/download'
    }
};

// 全局状态
const APP_STATE = {
    selectedScene: null,
    customCharacters: [],   // [{name: string, description: string}]
    requiredCharacterCount: 2,
    scenes: [],
    currentFilename: null
};
