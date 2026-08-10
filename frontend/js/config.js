// API配置
// 前后端分离后，静态前端可以单独跑在 8080，也可以部署到任意域名。
// 如需指定后端地址，可在 index.html 之前注入：
//   window.SCRIPT_AGENT_API_BASE_URL = 'https://api.example.com'
// 或在浏览器 localStorage 设置 SCRIPT_AGENT_API_BASE_URL。
function resolveApiBaseUrl() {
    let storedBaseUrl = '';
    try {
        storedBaseUrl = window.localStorage.getItem('SCRIPT_AGENT_API_BASE_URL') || '';
    } catch (e) {
        storedBaseUrl = '';
    }

    const explicitBaseUrl = window.SCRIPT_AGENT_API_BASE_URL
        || storedBaseUrl
        || '';

    if (explicitBaseUrl) {
        return explicitBaseUrl.replace(/\/+$/, '');
    }

    if (window.location.protocol === 'file:') {
        return 'http://localhost:5001';
    }

    const localHosts = new Set(['localhost', '127.0.0.1', '::1']);
    if (localHosts.has(window.location.hostname) && window.location.port !== '5001') {
        return 'http://localhost:5001';
    }

    if (window.location.pathname === '/script' || window.location.pathname.startsWith('/script/')) {
        return `${window.location.origin}/script`;
    }

    return '';
}

const API_CONFIG = {
    BASE_URL: resolveApiBaseUrl(),
    ENDPOINTS: {
        SCENES: '/api/scenes',
        CHARACTERS: '/api/characters',
        GENERATE_CHARACTERS: '/api/generate_characters',
        GENERATE: '/api/generate',
        GENERATE_DIRECTOR_WORD: '/api/generate_director_word',
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
    showUnavailableScenes: false,
    actScenes: [],          // 多场景：下标=幕序号，值=该幕场景 id
    customCharacters: [],   // [{name: string, description: string}]
    castSlots: [],          // [{mode: 'library'|'custom', selectedName: '', customName: '', customDesc: ''}]
    requiredCharacterCount: 2,
    scriptStyleId: '',
    scriptToneId: '',
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
