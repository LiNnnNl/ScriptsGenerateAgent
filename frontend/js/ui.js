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
    showSuccess(filename, actorsProfileFilename, warnings = []) {
        const resultPanel = document.getElementById('resultPanel');
        const messageEl = document.getElementById('resultMessage');

        messageEl.textContent = `剧本：${filename}`;
        resultPanel.style.display = 'block';

        APP_STATE.currentFilename = filename;
        APP_STATE.currentActorsProfileFilename = actorsProfileFilename || null;

        const actorsBtn = document.getElementById('downloadActorsBtn');
        if (actorsBtn) {
            actorsBtn.style.display = actorsProfileFilename ? '' : 'none';
        }
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

    // 渲染场景列表
    renderScenes(scenes) {
        const select = document.getElementById('sceneSelect');
        select.innerHTML = '<option value="">请选择场景...</option>' +
            scenes.map(scene => `<option value="${scene.id}">${scene.name}</option>`).join('');
    },

    // 显示场景信息
    showSceneInfo(scene) {
        const info = document.getElementById('sceneInfo');
        const description = document.getElementById('sceneDescription');
        const positions = document.getElementById('scenePositions');

        description.textContent = scene.description;

        let html = '';

        if (scene.camera_groups && scene.camera_groups.length > 0) {
            // Build a map from group id to group info
            const groupMap = {};
            for (const group of scene.camera_groups) {
                groupMap[group.id] = group;
            }
            // Build a map from position id to position object
            const posMap = {};
            for (const pos of scene.positions) {
                posMap[pos.id] = pos;
            }
            // Track which positions have been rendered
            const rendered = new Set();

            for (const group of scene.camera_groups) {
                html += `<div class="position-group">`;
                html += `<div class="position-group-title">${group.id}组 · ${group.name}</div>`;
                for (const posId of group.position_ids) {
                    const pos = posMap[posId];
                    if (pos) {
                        html += `<p><strong>${pos.id}</strong>: ${pos.description}</p>`;
                        rendered.add(posId);
                    }
                }
                html += `</div>`;
            }

            // Render ungrouped positions
            const ungrouped = scene.positions.filter(pos => !rendered.has(pos.id));
            if (ungrouped.length > 0) {
                html += `<div class="position-group">`;
                html += `<div class="position-group-title">独立点位</div>`;
                html += ungrouped.map(pos => `<p><strong>${pos.id}</strong>: ${pos.description}</p>`).join('');
                html += `</div>`;
            }
        } else {
            // No groups, list all positions directly
            html += scene.positions.map(pos => `<p><strong>${pos.id}</strong>: ${pos.description}</p>`).join('');
        }

        positions.innerHTML = html;

        info.style.display = 'block';
    },

    // 更新角色数量
    updateCharacterCount(count) {
        document.getElementById('characterCount').value = count;
        APP_STATE.requiredCharacterCount = count;
        this.renderCastForm(count);
    },

    // 构建角色的描述字符串（用于发送给AI）
    _buildCharDesc(char) {
        const parts = [];
        if (char.personality_traits && char.personality_traits !== '未知') parts.push(char.personality_traits);
        if (char.background && char.background !== '未知' && !char.background.startsWith('用户自定义')) parts.push(char.background);
        if (char.Faction && char.Faction !== '未知') parts.push(`阵营：${char.Faction}`);
        if (char.ip && char.ip !== '自定义') parts.push(`IP《${char.ip}》`);
        return parts.join(' · ');
    },

    // 构建角色库选择器的 options HTML（按IP分组）
    _buildCharSelectOptions(selectedName) {
        const grouped = {};
        for (const char of APP_STATE.characters) {
            const ip = char.ip || '其他';
            if (!grouped[ip]) grouped[ip] = [];
            grouped[ip].push(char);
        }
        let html = '<option value="">请选择角色…</option>';
        for (const [ip, chars] of Object.entries(grouped)) {
            html += `<optgroup label="${ip}">`;
            for (const char of chars) {
                const gender = char.gender && char.gender !== '未知' ? ` · ${char.gender}` : '';
                const sel = char.name === selectedName ? ' selected' : '';
                html += `<option value="${char.name}"${sel}>${char.name}${gender}</option>`;
            }
            html += '</optgroup>';
        }
        return html;
    },

    // 构建单个角色槽的 HTML
    _buildCastSlotHTML(i) {
        const slot = APP_STATE.castSlots[i];
        const isLibrary = slot.mode === 'library';
        const libraryDisplay = isLibrary ? '' : ' style="display:none"';
        const customDisplay = isLibrary ? ' style="display:none"' : '';
        const libraryActive = isLibrary ? ' active' : '';
        const customActive = isLibrary ? '' : ' active';

        const selectOptions = this._buildCharSelectOptions(slot.selectedName);

        // 预览卡片
        const previewChar = APP_STATE.characters.find(c => c.name === slot.selectedName);
        const previewDisplay = previewChar ? '' : ' style="display:none"';
        const previewHTML = previewChar ? `
            <div class="char-preview-name">${previewChar.name}</div>
            <div class="char-preview-ip">${previewChar.ip}${previewChar.Faction && previewChar.Faction !== '未知' ? ' · ' + previewChar.Faction : ''}</div>
            <div class="char-preview-traits">${previewChar.personality_traits !== '未知' ? previewChar.personality_traits : ''}</div>
            <div class="char-preview-bg">${previewChar.background !== '未知' ? previewChar.background : ''}</div>
        ` : '';

        return `
        <div class="cast-slot" data-index="${i}">
            <div class="cast-slot-header">
                <span class="cast-index">角色 ${i + 1}</span>
                <div class="cast-mode-toggle">
                    <button class="mode-btn${libraryActive}" data-mode="library" data-index="${i}">从角色库选</button>
                    <button class="mode-btn${customActive}" data-mode="custom" data-index="${i}">自定义输入</button>
                </div>
            </div>
            <div class="cast-library-panel"${libraryDisplay}>
                <select class="cast-select" data-index="${i}">
                    ${selectOptions}
                </select>
                <div class="cast-char-preview"${previewDisplay}>
                    ${previewHTML}
                </div>
            </div>
            <div class="cast-custom-panel"${customDisplay}>
                <div class="cast-custom-form">
                    <div class="cast-field-row">
                        <div class="cast-field cast-field-name">
                            <label class="cast-field-label">姓名 <span class="cast-field-required">*</span></label>
                            <input type="text" class="cast-input cast-name" data-index="${i}" placeholder="角色名称" value="${slot.customName}">
                        </div>
                        <div class="cast-field cast-field-gender">
                            <label class="cast-field-label">性别</label>
                            <select class="cast-input cast-gender" data-index="${i}">
                                <option value="未知"${slot.customGender === '未知' || !slot.customGender ? ' selected' : ''}>未知</option>
                                <option value="男"${slot.customGender === '男' ? ' selected' : ''}>男</option>
                                <option value="女"${slot.customGender === '女' ? ' selected' : ''}>女</option>
                            </select>
                        </div>
                    </div>
                    <div class="cast-field">
                        <label class="cast-field-label">性格特征</label>
                        <input type="text" class="cast-input cast-personality" data-index="${i}" placeholder="如：沉稳、理性、话少" value="${slot.customPersonality}">
                    </div>
                    <div class="cast-field">
                        <label class="cast-field-label">背景故事</label>
                        <input type="text" class="cast-input cast-background" data-index="${i}" placeholder="如：一个计算机研究生，喜欢独处" value="${slot.customBackground}">
                    </div>
                    <div class="cast-field-row">
                        <div class="cast-field">
                            <label class="cast-field-label">阵营</label>
                            <input type="text" class="cast-input cast-faction" data-index="${i}" placeholder="如：未知" value="${slot.customFaction}">
                        </div>
                        <div class="cast-field">
                            <label class="cast-field-label">IP / 来源</label>
                            <input type="text" class="cast-input cast-ip" data-index="${i}" placeholder="如：自定义" value="${slot.customIp}">
                        </div>
                    </div>
                </div>
                <button class="add-to-library-btn" data-index="${i}">＋ 保存到角色库</button>
            </div>
        </div>`;
    },

    // 空的自定义槽默认值
    _emptyCustomSlot() {
        return {
            mode: 'library', selectedName: '',
            customName: '', customGender: '未知', customPersonality: '',
            customBackground: '', customFaction: '', customIp: '自定义'
        };
    },

    // 渲染角色表单
    renderCastForm(count) {
        const container = document.getElementById('castForm');

        // 初始化或调整 castSlots 长度
        if (!APP_STATE.castSlots || APP_STATE.castSlots.length !== count) {
            const prev = APP_STATE.castSlots || [];
            APP_STATE.castSlots = Array.from({length: count}, (_, i) =>
                prev[i] || this._emptyCustomSlot()
            );
        }

        // 重建 customCharacters 同步
        APP_STATE.customCharacters = Array.from({length: count}, (_, i) => {
            const slot = APP_STATE.castSlots[i];
            if (slot.mode === 'library' && slot.selectedName) {
                const char = APP_STATE.characters.find(c => c.name === slot.selectedName);
                if (char) return {
                    name: char.name, gender: char.gender, ip: char.ip,
                    personality_traits: char.personality_traits,
                    background: char.background, Faction: char.Faction
                };
            } else if (slot.mode === 'custom' && slot.customName) {
                return {
                    name: slot.customName, gender: slot.customGender || '未知',
                    ip: slot.customIp || '自定义',
                    personality_traits: slot.customPersonality,
                    background: slot.customBackground, Faction: slot.customFaction || '未知'
                };
            }
            return {name: '', gender: '', ip: '', personality_traits: '', background: '', Faction: ''};
        });

        container.innerHTML = Array.from({length: count}, (_, i) => this._buildCastSlotHTML(i)).join('');
        this._attachCastListeners(container);
    },

    // 挂载角色表单事件
    _attachCastListeners(container) {
        // 模式切换
        container.querySelectorAll('.mode-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.dataset.index);
                const mode = btn.dataset.mode;
                APP_STATE.castSlots[i].mode = mode;

                const slot = container.querySelector(`.cast-slot[data-index="${i}"]`);
                slot.querySelector('.cast-library-panel').style.display = mode === 'library' ? '' : 'none';
                slot.querySelector('.cast-custom-panel').style.display = mode === 'custom' ? '' : 'none';
                slot.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));

                // 切换后同步 customCharacters
                this._syncSlot(i, container);
            });
        });

        // 库选择变化
        container.querySelectorAll('.cast-select').forEach(select => {
            select.addEventListener('change', () => {
                const i = parseInt(select.dataset.index);
                const name = select.value;
                APP_STATE.castSlots[i].selectedName = name;

                const slot = container.querySelector(`.cast-slot[data-index="${i}"]`);
                const preview = slot.querySelector('.cast-char-preview');
                const char = APP_STATE.characters.find(c => c.name === name);
                if (char) {
                    preview.innerHTML = `
                        <div class="char-preview-name">${char.name}</div>
                        <div class="char-preview-ip">${char.ip}${char.Faction && char.Faction !== '未知' ? ' · ' + char.Faction : ''}</div>
                        <div class="char-preview-traits">${char.personality_traits !== '未知' ? char.personality_traits : ''}</div>
                        <div class="char-preview-bg">${char.background !== '未知' ? char.background : ''}</div>
                    `;
                    preview.style.display = '';
                } else {
                    preview.style.display = 'none';
                }
                this._syncSlot(i, container);
            });
        });

        // 自定义输入 - 所有字段统一处理
        const customFieldMap = {
            'cast-name':        'customName',
            'cast-personality': 'customPersonality',
            'cast-background':  'customBackground',
            'cast-faction':     'customFaction',
            'cast-ip':          'customIp',
        };
        for (const [cls, key] of Object.entries(customFieldMap)) {
            container.querySelectorAll(`.${cls}`).forEach(input => {
                input.addEventListener('input', () => {
                    const i = parseInt(input.dataset.index);
                    APP_STATE.castSlots[i][key] = input.value;
                    this._syncSlot(i, container);
                });
            });
        }
        container.querySelectorAll('.cast-gender').forEach(select => {
            select.addEventListener('change', () => {
                const i = parseInt(select.dataset.index);
                APP_STATE.castSlots[i].customGender = select.value;
                this._syncSlot(i, container);
            });
        });
    },

    // 同步单个槽到 customCharacters
    _syncSlot(i, container) {
        const slot = APP_STATE.castSlots[i];
        if (slot.mode === 'library' && slot.selectedName) {
            const char = APP_STATE.characters.find(c => c.name === slot.selectedName);
            APP_STATE.customCharacters[i] = char ? {
                name: char.name, gender: char.gender, ip: char.ip,
                personality_traits: char.personality_traits,
                background: char.background, Faction: char.Faction
            } : {name: '', gender: '', ip: '', personality_traits: '', background: '', Faction: ''};
        } else if (slot.mode === 'custom') {
            APP_STATE.customCharacters[i] = {
                name: slot.customName, gender: slot.customGender || '未知',
                ip: slot.customIp || '自定义',
                personality_traits: slot.customPersonality,
                background: slot.customBackground, Faction: slot.customFaction || '未知'
            };
        } else {
            APP_STATE.customCharacters[i] = {name: '', gender: '', ip: '', personality_traits: '', background: '', Faction: ''};
        }
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
