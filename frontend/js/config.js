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
    selectedScene: null,    // 兼容字段：= scenePool[0]
    scenePool: [],          // 多场景：已选场景 id 数组（仅含有锚点的可选场景）
    actScenes: [],          // 多场景：下标=幕序号，值=该幕场景 id
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
    currentScriptTitle: null,        // 生成后自动获得的片名
    currentPositionPlanFilename: null,
    currentPositionDetailFilename: null,
    actCount: 3,
    currentSessionId: null,
    historyPanelOpen: false,
    availableActions: {},  // { state: [{trigger, description, state}] }
    shotTypes: [],         // 合法 shot_type 列表
    shotBlends: [],        // 合法 shot_blend 列表
    characterImageKeys: new Set([
        'F01_WithCamera',
        'F02_WithCamera',
        'M01_WithCamera',
        'RobotRecon',
        'TR_assasin',
        'TR_service',
        'TR_worker',
        'ToonRobot',
        'chamber',
        'jett',
        'sage',
    ]),
    localProject: null,    // 当前打开的本地 .scriptagent.json 项目
    localProjectHandle: null
};
