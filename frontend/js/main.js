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


// 检查表单完整性
function checkFormComplete() {
    const castGenBtn = document.getElementById('castGenerateBtn');
    if (APP_STATE.selectedScene) {
        UI.enableStep('step3');
        castGenBtn.disabled = false;
    } else {
        castGenBtn.disabled = true;
        UI.disableGenerateBtn();
    }
    // ACTION! 只在角色档案已生成后可用
    if (APP_STATE.generatedCharacters) {
        UI.enableGenerateBtn();
    } else {
        UI.disableGenerateBtn();
    }
}

// 生成角色档案
async function generateCast() {
    const castGenBtn = document.getElementById('castGenerateBtn');
    castGenBtn.disabled = true;
    castGenBtn.querySelector('.btn-text').textContent = 'GENERATING...';

    // 收集部分指定角色（作为 AI 创作提示）
    const partialChars = (APP_STATE.castSlots || [])
        .filter(s => (s.customName || s.selectedName || '').trim())
        .map(s => ({
            name: (s.customName || s.selectedName || '').trim(),
            description: (s.customPersonality || s.customBackground || '').trim()
        }));

    let succeeded = false;
    try {
        const result = await API.generateCharacters({
            scene_id: APP_STATE.selectedScene,
            character_count: APP_STATE.requiredCharacterCount || 2,
            creative_idea: document.getElementById('creativeIdea').value.trim(),
            partial_characters: partialChars
        });

        if (result.success) {
            succeeded = true;
            APP_STATE.generatedCharacters = result.data;
            APP_STATE.currentCharactersFilename = result.filename;
            UI.renderCastPreview(result.data);
            UI.enableGenerateBtn();
        } else {
            alert('生成角色失败：' + (result.error || '未知错误'));
        }
    } catch (e) {
        alert('网络错误：' + e.message);
    } finally {
        castGenBtn.querySelector('.btn-text').textContent = 'GENERATE CAST';
        if (!succeeded) castGenBtn.disabled = false;
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
    if (APP_STATE.generatedCharacters && APP_STATE.generatedCharacters.length > 0) {
        UI.addLog('info', `角色: ${APP_STATE.generatedCharacters.map(c => c.name).join(', ')}`);
    }
    UI.addLog('info', `场景: ${APP_STATE.scenes.find(s => s.id === APP_STATE.selectedScene)?.name || APP_STATE.selectedScene}`);

    let succeeded = false;
    try {
        UI.addLog('info', '📡 正在连接 AI 服务...');

        await API.generateScript({
            custom_characters: APP_STATE.generatedCharacters || [],
            scene_id: APP_STATE.selectedScene,
            creative_idea: document.getElementById('creativeIdea').value.trim(),
            required_character_count: APP_STATE.requiredCharacterCount
        }, (data) => {
            if (data.type === 'success') succeeded = true;
            handleStreamData(data);
        });

    } catch (error) {
        UI.addLog('error', '❌ 生成失败: ' + error.message);
        UI.showError('生成失败: ' + error.message);
    } finally {
        if (!succeeded) generateBtn.disabled = false;
    }
}

// 处理流式数据
function handleStreamData(data) {
    if (data.type === 'log') {
        if (data.level === 'output') {
            UI.addOutputBlock(data);
            return;
        }
        UI.addLog(data.level || 'info', data.message, { stage: data.stage, phase: data.phase });
    } else if (data.type === 'thinking') {
        UI.addLog('thinking', '💭 ' + data.message);
    } else if (data.type === 'thinking_chunk') {
        UI.appendThinkingChunk(data.text);
    } else if (data.type === 'thinking_done') {
        UI.endThinkingStream();
        UI.addLog('thinking', '✅ 思考完成，开始生成剧本...');
    } else if (data.type === 'success') {
        UI.addLog('success', '✅ 剧本生成成功！');
        UI.addLog('success', `📁 剧本文件: ${data.filename}`);
        if (data.actors_profile_filename) {
            UI.addLog('success', `👥 演员档案: ${data.actors_profile_filename}`);
        }
        UI.showSuccess(data.filename, data.actors_profile_filename);
        loadScriptEditor(data.filename);
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

    // 生成角色按钮
    document.getElementById('castGenerateBtn').addEventListener('click', generateCast);

    // 生成按钮
    document.getElementById('generateBtn').addEventListener('click', generateScript);

    // 下载按钮 - 角色档案（始终下载当前编辑后的内容）
    document.getElementById('downloadCastBtn').addEventListener('click', () => {
        if (!APP_STATE.generatedCharacters) return;
        const blob = new Blob(
            [JSON.stringify(APP_STATE.generatedCharacters, null, 2)],
            { type: 'application/json' }
        );
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = APP_STATE.currentCharactersFilename || 'characters.json';
        a.click();
        URL.revokeObjectURL(url);
    });

    // 下载按钮 - 坐标文件（始终下载 positions_sample.json）
    document.getElementById('downloadPositionBtn').addEventListener('click', () => {
        API.downloadFile('positions_sample.json');
    });

    // 清空日志按钮
    document.getElementById('clearLogBtn').addEventListener('click', UI.clearLog);

    // 下载修改后的剧本（从 APP_STATE.currentScriptData 序列化）
    document.getElementById('downloadScriptEditedBtn').addEventListener('click', () => {
        if (!APP_STATE.currentScriptData) return;
        const blob = new Blob(
            [JSON.stringify(APP_STATE.currentScriptData, null, 2)],
            { type: 'application/json' }
        );
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = APP_STATE.currentScriptFilename || 'script_edited.json';
        a.click();
        URL.revokeObjectURL(url);
    });

    // ── 角色表 JSON 拖放导入 ──
    const dropzone   = document.getElementById('castDropzone');
    const fileInput  = document.getElementById('castFileInput');
    const selectBtn  = document.getElementById('castDropzoneBtn');

    selectBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
        if (fileInput.files[0]) importCastJSON(fileInput.files[0]);
        fileInput.value = '';
    });

    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('drag-over');
    });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file) importCastJSON(file);
    });
}

// 导入角色表 JSON 文件
function importCastJSON(file) {
    const feedback = document.getElementById('castImportFeedback');
    if (!file.name.endsWith('.json')) {
        feedback.className = 'cast-import-feedback error';
        feedback.textContent = '请选择 .json 格式文件';
        return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
        try {
            const data = JSON.parse(e.target.result);
            if (!Array.isArray(data) || data.length === 0) {
                throw new Error('文件内容必须是非空 JSON 数组');
            }
            if (!data.every(c => typeof c === 'object' && c !== null && (c.name || '').trim())) {
                throw new Error('每个角色对象必须包含 name 字段');
            }
            APP_STATE.generatedCharacters = data;
            UI.renderCastPreview(data);
            UI.enableGenerateBtn();
            feedback.className = 'cast-import-feedback success';
            feedback.textContent = `✓ 已导入 ${data.length} 位角色（${file.name}）`;
        } catch (err) {
            feedback.className = 'cast-import-feedback error';
            feedback.textContent = `解析失败：${err.message}`;
        }
    };
    reader.readAsText(file, 'utf-8');
}

// 更新角色数量
function updateCount(count) {
    if (count < 1 || count > 10) return;

    UI.updateCharacterCount(count);
    checkFormComplete();
}

// 加载剧本内容到可读编辑器
async function loadScriptEditor(filename) {
    const panel = document.getElementById('scriptEditorPanel');
    const viewer = document.getElementById('scriptViewer');

    panel.style.display = 'block';
    viewer.innerHTML = '<p style="padding:20px;color:rgba(224,224,224,0.5)">加载中…</p>';

    try {
        const result = await API.getScriptContent(filename);
        if (result.success) {
            APP_STATE.currentScriptFilename = filename;
            UI.renderScriptViewer(result.data);
        } else {
            viewer.innerHTML = `<p style="padding:20px;color:#f44336">加载失败：${result.error}</p>`;
        }
    } catch (e) {
        viewer.innerHTML = `<p style="padding:20px;color:#f44336">网络错误：${e.message}</p>`;
    }
}
