// API配置
const API_CONFIG = {
    BASE_URL: 'http://localhost:5000',  // 后端API地址
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
