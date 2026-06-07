// API配置
const API_CONFIG = {
    BASE_URL: '',
    ENDPOINTS: {
        SCENES: '/api/scenes',
        CHARACTERS: '/api/characters',
        GENERATE_CHARACTERS: '/api/generate_characters',
        GENERATE: '/api/generate',
        DOWNLOAD: '/api/download',
        DOWNLOAD_WORD: '/api/download_word',
        CHARACTER_IMAGE: '/api/character_image',
        SCRIPT_CONTENT: '/api/script_content',
        HISTORY: '/api/history',
        ACTIONS: '/api/actions',
        SHOT_TYPES: '/api/shot_types',
        DOWNLOAD_SESSION: '/api/download_session',
        POSITION_PLAN: '/api/position_plan'
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
    currentScriptData: null,         // 当前剧本数据（可编辑，下载时序列化）
    currentPositionPlanFilename: null,
    currentPositionDetailFilename: null,
    actCount: 3,
    currentSessionId: null,
    historyPanelOpen: false,
    availableActions: {},  // { state: [{trigger, description, state}] }
    shotTypes: [],         // 合法 shot_type 列表
    shotBlends: []         // 合法 shot_blend 列表
};
