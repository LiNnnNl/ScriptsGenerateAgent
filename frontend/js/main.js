// 主程序逻辑
document.addEventListener('DOMContentLoaded', init);

async function init() {
    await Promise.all([loadScenes(), loadCharacters()]);
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

// 加载角色库
async function loadCharacters() {
    try {
        const result = await API.getCharacters();
        if (result.success) {
            APP_STATE.characters = result.data;
        }
    } catch (error) {
        console.error('加载角色库失败:', error);
    }
}

// 添加角色到角色库
async function addCharacterToLibrary(index) {
    const slot = APP_STATE.castSlots[index];
    const name = (slot.customName || '').trim();
    if (!name) {
        alert('请先填写角色名称');
        return;
    }

    const btn = document.querySelector(`.add-to-library-btn[data-index="${index}"]`);
    btn.disabled = true;
    btn.textContent = '保存中…';

    try {
        const result = await API.addCharacter({
            name: slot.customName.trim(),
            description: slot.customDesc.trim()
        });

        if (result.success) {
            APP_STATE.characters.push(result.data);
            // 切换到库选模式并选中新角色
            APP_STATE.castSlots[index].mode = 'library';
            APP_STATE.castSlots[index].selectedName = result.data.name;
            UI.renderCastForm(APP_STATE.requiredCharacterCount);
        } else {
            btn.disabled = false;
            btn.textContent = '＋ 保存到角色库';
            alert(result.error || '添加失败');
        }
    } catch (e) {
        btn.disabled = false;
        btn.textContent = '＋ 保存到角色库';
        alert('网络错误，请重试');
    }
}

// 检查表单完整性
function checkFormComplete() {
    if (APP_STATE.selectedScene) {
        UI.enableStep('step3');
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

    // 保存到角色库（事件委托）
    document.getElementById('castForm').addEventListener('click', (e) => {
        if (e.target.classList.contains('add-to-library-btn')) {
            const index = parseInt(e.target.dataset.index);
            addCharacterToLibrary(index);
        }
    });

    // 导入 JSON 角色文件 - 点击"选择文件"按钮
    document.getElementById('castImportBtn').addEventListener('click', (e) => {
        e.stopPropagation();
        document.getElementById('castJsonInput').click();
    });

    // 点击拖放区任意位置也触发文件选择
    document.getElementById('castDropzone').addEventListener('click', () => {
        document.getElementById('castJsonInput').click();
    });

    // 拖放支持
    const dropzone = document.getElementById('castDropzone');
    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('drag-over');
    });
    dropzone.addEventListener('dragleave', () => {
        dropzone.classList.remove('drag-over');
    });
    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (!file) return;
        if (!file.name.endsWith('.json')) {
            showImportFeedback('error', '请拖入 .json 格式的文件');
            return;
        }
        const reader = new FileReader();
        reader.onload = (ev) => {
            importCharactersFromJSON(ev.target.result, file.name);
        };
        reader.readAsText(file, 'utf-8');
    });

    document.getElementById('castJsonInput').addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (ev) => {
            importCharactersFromJSON(ev.target.result, file.name);
        };
        reader.readAsText(file, 'utf-8');
        // 清空 input 以便同一文件可再次选择
        e.target.value = '';
    });
}

// 从 JSON 文件导入角色
function importCharactersFromJSON(text, filename) {
    const feedback = document.getElementById('castImportFeedback');

    let data;
    try {
        data = JSON.parse(text);
    } catch {
        showImportFeedback('error', `解析失败：文件不是合法的 JSON`);
        return;
    }

    if (!Array.isArray(data) || data.length === 0) {
        showImportFeedback('error', '文件格式错误：需要一个角色对象数组');
        return;
    }

    // 检查每个元素是否有 name 字段
    const valid = data.filter(c => c && typeof c.name === 'string' && c.name.trim());
    if (valid.length === 0) {
        showImportFeedback('error', '未找到有效角色（每个对象需要 name 字段）');
        return;
    }

    const count = valid.length;

    // 重置 castSlots 为 custom 模式并填入数据
    APP_STATE.castSlots = valid.map(char => ({
        mode: 'custom',
        selectedName: '',
        customName: char.name.trim(),
        customDesc: buildImportDesc(char)
    }));

    // 更新角色数量 UI
    updateCount(count);

    showImportFeedback('success', `已从「${filename}」导入 ${count} 个角色`);
}

// 从导入的角色对象构建描述文本
function buildImportDesc(char) {
    const parts = [];
    if (char.personality_traits && char.personality_traits !== '未知') parts.push(char.personality_traits);
    if (char.background && char.background !== '未知') parts.push(char.background);
    if (char.Faction && char.Faction !== '未知') parts.push(`阵营：${char.Faction}`);
    if (char.ip && char.ip !== '自定义') parts.push(`IP《${char.ip}》`);
    return parts.join(' · ');
}

function showImportFeedback(type, message) {
    const el = document.getElementById('castImportFeedback');
    el.className = `cast-import-feedback ${type}`;
    el.textContent = message;
    el.style.display = 'block';
}

// 更新角色数量
function updateCount(count) {
    if (count < 1 || count > 10) return;

    UI.updateCharacterCount(count);
    checkFormComplete();
}
