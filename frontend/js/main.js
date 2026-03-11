// 主程序逻辑
document.addEventListener('DOMContentLoaded', init);

async function init() {
    await loadStyles();
    setupEventListeners();
}

// 加载画风列表
async function loadStyles() {
    try {
        const result = await API.getStyles();
        if (result.success) {
            UI.renderStyles(result.data);
            bindStyleClickEvents();
        }
    } catch (error) {
        console.error('加载画风失败:', error);
        UI.showError('加载画风失败: ' + error.message);
    }
}

// 绑定画风点击事件
function bindStyleClickEvents() {
    document.querySelectorAll('.style-card').forEach(card => {
        card.addEventListener('click', () => selectStyle(card.dataset.style));
    });
}

// 选择画风
async function selectStyle(style) {
    APP_STATE.selectedStyle = style;
    APP_STATE.selectedScene = null;
    APP_STATE.selectedCharacters = [];

    // 更新UI
    document.querySelectorAll('.style-card').forEach(card => {
        card.classList.toggle('selected', card.dataset.style === style);
    });

    UI.enableStep('step2');

    // 加载场景和角色
    try {
        const [scenesResult, charactersResult] = await Promise.all([
            API.getScenes(style),
            API.getCharacters(style)
        ]);

        if (scenesResult.success) {
            APP_STATE.scenes = scenesResult.data;
            UI.renderScenes(scenesResult.data);
        }

        if (charactersResult.success) {
            APP_STATE.characters = charactersResult.data;
            UI.renderCharacters(charactersResult.data);
            bindCharacterClickEvents();
        }
    } catch (error) {
        console.error('加载数据失败:', error);
        UI.showError('加载数据失败: ' + error.message);
    }

    checkFormComplete();
}

// 绑定角色点击事件
function bindCharacterClickEvents() {
    document.querySelectorAll('.character-card').forEach(card => {
        card.addEventListener('click', () => toggleCharacter(card.dataset.id));
    });
}

// 切换角色选择
function toggleCharacter(charId) {
    const index = APP_STATE.selectedCharacters.indexOf(charId);
    
    if (index > -1) {
        APP_STATE.selectedCharacters.splice(index, 1);
    } else {
        if (APP_STATE.selectedCharacters.length >= APP_STATE.requiredCharacterCount) {
            UI.addLog('warning', `最多只能选择 ${APP_STATE.requiredCharacterCount} 个角色`);
            return;
        }
        APP_STATE.selectedCharacters.push(charId);
    }

    UI.updateCharacterSelection();
    checkFormComplete();
}

// 检查表单完整性
function checkFormComplete() {
    const hasCorrectCharacterCount = 
        APP_STATE.selectedCharacters.length === APP_STATE.requiredCharacterCount;
    
    if (hasCorrectCharacterCount) {
        UI.enableStep('step5');
    }
    
    const isComplete = APP_STATE.selectedStyle && 
                      APP_STATE.selectedScene && 
                      hasCorrectCharacterCount;

    if (isComplete) {
        UI.enableGenerateBtn();
    } else {
        UI.disableGenerateBtn();
    }
}

// 生成剧本
async function generateScript() {
    const generateBtn = document.getElementById('generateBtn');
    generateBtn.disabled = true;

    UI.hideResults();
    UI.clearLog();
    document.getElementById('logPanel').style.display = 'block';

    // 开始日志
    UI.addLog('info', '🚀 开始生成剧本...');
    UI.addLog('info', `选择的角色: ${APP_STATE.selectedCharacters.map(id => {
        const char = APP_STATE.characters.find(c => c.id === id);
        return char ? char.name : id;
    }).join(', ')}`);
    UI.addLog('info', `场景: ${APP_STATE.scenes.find(s => s.id === APP_STATE.selectedScene)?.name || APP_STATE.selectedScene}`);

    try {
        UI.addLog('info', '📡 正在连接 AI 服务...');

        await API.generateScript({
            character_ids: APP_STATE.selectedCharacters,
            scene_id: APP_STATE.selectedScene,
            creative_idea: document.getElementById('creativeIdea').value.trim()
        }, handleStreamData);

    } catch (error) {
        UI.addLog('error', '❌ 生成失败: ' + error.message);
        UI.showError('生成失败: ' + error.message);
    } finally {
        generateBtn.disabled = false;
    }
}

// 处理流式数据
function handleStreamData(data) {
    if (data.type === 'log') {
        UI.addLog(data.level || 'info', data.message);
    } else if (data.type === 'thinking') {
        UI.addLog('thinking', '💭 ' + data.message);
    } else if (data.type === 'success') {
        UI.addLog('success', '✅ 剧本生成成功！');
        UI.addLog('success', `📁 文件保存: ${data.filename}`);
        
        UI.showSuccess(data.filename);
    } else if (data.type === 'error') {
        UI.addLog('error', '❌ ' + data.message);
        if (data.details) {
            UI.addLog('error', JSON.stringify(data.details, null, 2));
        }
        UI.showError(data.message, data.details);
    }
}

// 设置事件监听
function setupEventListeners() {
    // 场景选择
    document.getElementById('sceneSelect').addEventListener('change', (e) => {
        APP_STATE.selectedScene = e.target.value;
        
        if (e.target.value) {
            const scene = APP_STATE.scenes.find(s => s.id === e.target.value);
            if (scene) {
                UI.showSceneInfo(scene);
            }

            UI.enableStep('step3');
            UI.enableStep('step4');
            
            const count = parseInt(document.getElementById('characterCount').value) || 2;
            APP_STATE.requiredCharacterCount = count;
            UI.updateCharacterCount(count);
        }

        checkFormComplete();
    });

    // 角色数量 - 减少
    document.getElementById('decreaseBtn').addEventListener('click', () => {
        const input = document.getElementById('characterCount');
        const value = parseInt(input.value) || 2;
        if (value > 1) {
            updateCount(value - 1);
        }
    });

    // 角色数量 - 增加
    document.getElementById('increaseBtn').addEventListener('click', () => {
        const input = document.getElementById('characterCount');
        const value = parseInt(input.value) || 2;
        if (value < 10) {
            updateCount(value + 1);
        }
    });

    // 角色数量 - 直接输入
    document.getElementById('characterCount').addEventListener('input', (e) => {
        const count = parseInt(e.target.value) || 0;
        updateCount(count);
    });

    // 创作想法输入
    document.getElementById('creativeIdea').addEventListener('input', checkFormComplete);

    // 生成按钮
    document.getElementById('generateBtn').addEventListener('click', generateScript);

    // 下载按钮
    document.getElementById('downloadBtn').addEventListener('click', () => {
        if (APP_STATE.currentFilename) {
            API.downloadFile(APP_STATE.currentFilename);
        }
    });

    // 清空日志按钮
    document.getElementById('clearLogBtn').addEventListener('click', UI.clearLog);
}

// 更新角色数量
function updateCount(count) {
    if (count < 1 || count > 10) return;
    
    UI.updateCharacterCount(count);
    
    // 清空已选角色如果超出数量
    if (APP_STATE.selectedCharacters.length > count) {
        APP_STATE.selectedCharacters = APP_STATE.selectedCharacters.slice(0, count);
        UI.updateCharacterSelection();
    }
    
    checkFormComplete();
}
