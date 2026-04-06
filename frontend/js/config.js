// API配置
const API_CONFIG = {
    // Use same-origin base URL so /api is always proxied by Nginx.
    // Works for localhost and ngrok without hardcoding backend port.
    BASE_URL: window.location.origin,
    ENDPOINTS: {
        SCENES: '/api/scenes',
        CHARACTERS: '/api/characters',
        GENERATE_CHARACTERS: '/api/generate_characters',
        GENERATE: '/api/generate',
        DOWNLOAD: '/api/download',
        CHARACTER_IMAGE: '/api/character_image',
        SCRIPT_CONTENT: '/api/script_content'
    }
};

// 全局状态
const APP_STATE = {
    selectedScene: null,
    customCharacters: [],   // [{name: string, description: string}]
    castSlots: [],          // [{mode: 'library'|'custom', selectedName: '', customName: '', customDesc: ''}]
    requiredCharacterCount: 2,
    scenes: [],
    characters: [],         // 角色库完整数据
    currentFilename: null,
    currentActorsProfileFilename: null,
    currentPositionFilename: null,
    generatedCharacters: null,       // AI 生成的角色档案数组
    currentCharactersFilename: null, // 生成的角色档案文件名
    currentScriptFilename: null,     // 当前剧本文件名（用于编辑器下载）
    currentScriptData: null          // 当前剧本数据（可编辑，下载时序列化）
};
