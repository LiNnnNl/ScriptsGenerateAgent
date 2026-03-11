// UI控制模块
const UI = {
    // 添加日志
    addLog(type, message) {
        const logPanel = document.getElementById('logPanel');
        const logContent = document.getElementById('logContent');
        
        logPanel.style.display = 'block';
        
        const timestamp = new Date().toLocaleTimeString('zh-CN', { 
            hour12: false, 
            hour: '2-digit', 
            minute: '2-digit', 
            second: '2-digit' 
        });
        
        const entry = document.createElement('div');
        entry.className = `log-entry ${type}`;
        entry.innerHTML = `<span class="log-timestamp">[${timestamp}]</span>${message}`;
        
        logContent.appendChild(entry);
        logContent.scrollTop = logContent.scrollHeight;
    },

    // 清空日志
    clearLog() {
        document.getElementById('logContent').innerHTML = '';
    },

    // 显示成功结果
    showSuccess(filename, warnings = []) {
        const resultPanel = document.getElementById('resultPanel');
        const messageEl = document.getElementById('resultMessage');
        
        messageEl.textContent = `文件已保存: ${filename}`;
        resultPanel.style.display = 'block';
        
        APP_STATE.currentFilename = filename;
    },

    // 显示错误
    showError(message, details = null) {
        const errorPanel = document.getElementById('errorPanel');
        const messageEl = document.getElementById('errorMessage');
        
        let fullMessage = message;
        if (details) {
            fullMessage += '\n\n' + JSON.stringify(details, null, 2);
        }
        
        messageEl.textContent = fullMessage;
        errorPanel.style.display = 'block';
    },

    // 隐藏结果面板
    hideResults() {
        document.getElementById('resultPanel').style.display = 'none';
        document.getElementById('errorPanel').style.display = 'none';
    },

    // 渲染画风列表
    renderStyles(styles) {
        const grid = document.getElementById('styleGrid');
        grid.innerHTML = styles.map(style => `
            <div class="style-card" data-style="${style}">
                <h3>${style}</h3>
            </div>
        `).join('');
    },

    // 渲染场景列表
    renderScenes(scenes) {
        const select = document.getElementById('sceneSelect');
        select.innerHTML = '<option value="">请选择场景...</option>' +
            scenes.map(scene => `
                <option value="${scene.id}">${scene.name}</option>
            `).join('');
    },

    // 显示场景信息
    showSceneInfo(scene) {
        const info = document.getElementById('sceneInfo');
        const description = document.getElementById('sceneDescription');
        const positions = document.getElementById('scenePositions');
        
        description.textContent = scene.description;
        positions.innerHTML = scene.positions.map(pos => `
            <p><strong>${pos.id}</strong>: ${pos.description}</p>
        `).join('');
        
        info.style.display = 'block';
    },

    // 渲染角色列表
    renderCharacters(characters) {
        const grid = document.getElementById('characterGrid');
        grid.innerHTML = characters.map(char => `
            <div class="character-card" data-id="${char.id}">
                <h4>${char.name}</h4>
                <p><strong>描述:</strong> ${char.description}</p>
                <p><strong>性格:</strong> ${char.personality}</p>
            </div>
        `).join('');
    },

    // 更新角色选择UI
    updateCharacterSelection() {
        document.querySelectorAll('.character-card').forEach(card => {
            card.classList.toggle('selected', 
                APP_STATE.selectedCharacters.includes(card.dataset.id));
        });
        
        document.getElementById('selectedCount').textContent = APP_STATE.selectedCharacters.length;
        document.getElementById('requiredCount').textContent = APP_STATE.requiredCharacterCount;
    },

    // 更新角色数量
    updateCharacterCount(count) {
        document.getElementById('characterCount').value = count;
        APP_STATE.requiredCharacterCount = count;
        document.getElementById('requiredCount').textContent = count;
    },

    // 启用/禁用步骤
    enableStep(stepId) {
        document.getElementById(stepId).classList.remove('disabled');
    },

    disableStep(stepId) {
        document.getElementById(stepId).classList.add('disabled');
    },

    // 启用/禁用生成按钮
    enableGenerateBtn() {
        document.getElementById('generateBtn').disabled = false;
    },

    disableGenerateBtn() {
        document.getElementById('generateBtn').disabled = true;
    }
};
