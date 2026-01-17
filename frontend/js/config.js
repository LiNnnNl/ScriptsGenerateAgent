// API配置
const API_CONFIG = {
    BASE_URL: 'http://localhost:5000',  // 后端API地址
    ENDPOINTS: {
        STYLES: '/api/styles',
        SCENES: '/api/scenes',
        CHARACTERS: '/api/characters',
        GENERATE: '/api/generate',
        DOWNLOAD: '/api/download'
    }
};

// 全局状态
const APP_STATE = {
    selectedStyle: null,
    selectedScene: null,
    selectedCharacters: [],
    requiredCharacterCount: 2,
    scenes: [],
    characters: [],
    currentFilename: null
};
