// 主程序逻辑
document.addEventListener('DOMContentLoaded', init);

async function init() {
    await loadScenes();
    setupEventListeners();
}

// 加载场景列表
async function loadScenes() {
    try {
        const result = await API.getScenes();
        if (result.success) {
            APP_STATE.scenes = result.data;
            UI.renderScenes(result.data);
        }
    } catch (error) {
        console.error('加载场景失败:', error);
        UI.showError('加载场景失败: ' + error.message);
    }
}

// 检查表单完整性
function checkFormComplete() {
    if (APP_STATE.selectedScene) {
        UI.enableStep('step4');
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
    const validChars = APP_STATE.customCharacters.filter(c => c.name !== '');
    if (validChars.length > 0) {
        UI.addLog('info', `自定义角色: ${validChars.map(c => c.name).join(', ')}`);
    } else {
        UI.addLog('info', 'AI 将自由创作角色');
    }
    UI.addLog('info', `场景: ${APP_STATE.scenes.find(s => s.id === APP_STATE.selectedScene)?.name || APP_STATE.selectedScene}`);

    try {
        UI.addLog('info', '📡 正在连接 AI 服务...');

        await API.generateScript({
            custom_characters: validChars,
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

            UI.enableStep('step2');
            UI.enableStep('step3');
            
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
    checkFormComplete();
}
