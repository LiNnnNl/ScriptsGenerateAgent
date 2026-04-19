// UI控制模块
const UI = {
    // 追加思考流式片段（复用或新建一个 thinking-live 条目）
    appendThinkingChunk(text) {
        const logContent = document.getElementById('logContent');
        let entry = logContent.querySelector('.thinking-live');
        if (!entry) {
            const timestamp = new Date().toLocaleTimeString('zh-CN', {
                hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
            entry = document.createElement('div');
            entry.className = 'log-entry thinking thinking-live';
            entry.innerHTML = `<span class="log-timestamp">[${timestamp}]</span><span class="thinking-text">💭 </span>`;
            logContent.appendChild(entry);
        }
        entry.querySelector('.thinking-text').textContent += text;
        logContent.scrollTop = logContent.scrollHeight;
    },

    // 结束思考流（移除 live 标记）
    endThinkingStream() {
        const entry = document.getElementById('logContent').querySelector('.thinking-live');
        if (entry) entry.classList.remove('thinking-live');
    },

    // 添加日志
    addLog(type, message, meta = null) {
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
        const stagePrefix = this._formatStagePrefix(meta);
        entry.innerHTML = `<span class="log-timestamp">[${timestamp}]</span>${stagePrefix}${message}`;
        
        logContent.appendChild(entry);
        logContent.scrollTop = logContent.scrollHeight;
    },

    _formatStagePrefix(meta) {
        if (!meta || !meta.stage) return '';
        const labels = {
            setup: '流程准备期',
            concept: '概念孵化期',
            synopsis: '故事梗概期',
            character_bios: '人物塑形期',
            treatment: '分场规划期',
            draft: '剧本起草期',
            review: '审核与迭代期',
            validation: '技术验证期',
            position_mapping: '位置映射期',
            position_generation: '坐标生成期',
            output: '输出阶段'
        };
        const stageLabel = labels[meta.stage] || meta.stage;
        const phaseLabel = meta.phase ? ` · ${meta.phase}` : '';
        return `[${stageLabel}${phaseLabel}] `;
    },

    // 清空日志
    clearLog() {
        document.getElementById('logContent').innerHTML = '';
    },

    // 显示成功结果
    showSuccess(filename, actorsProfileFilename, positionFilename, warnings = [], positionPlanFilename = null, positionDetailFilename = null) {
        const resultPanel = document.getElementById('resultPanel');
        const messageEl = document.getElementById('resultMessage');

        messageEl.textContent = `剧本：${filename}`;
        resultPanel.style.display = 'block';

        APP_STATE.currentFilename = filename;
        APP_STATE.currentActorsProfileFilename = actorsProfileFilename || null;
        APP_STATE.currentPositionFilename = positionFilename || null;
        APP_STATE.currentPositionPlanFilename = positionPlanFilename || null;
        APP_STATE.currentPositionDetailFilename = positionDetailFilename || null;

        const actorsBtn = document.getElementById('downloadActorsBtn');
        if (actorsBtn) {
            actorsBtn.style.display = actorsProfileFilename ? '' : 'none';
        }

        const planBtn = document.getElementById('downloadPositionPlanBtn');
        if (planBtn) {
            planBtn.style.display = '';
            planBtn.disabled = !positionPlanFilename;
        }

        const detailBtn = document.getElementById('downloadPositionDetailBtn');
        if (detailBtn) {
            detailBtn.style.display = '';
            detailBtn.disabled = !positionDetailFilename;
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
    },

    // 渲染角色档案预览（含逐个替换编辑器）
    renderCastPreview(characters) {
        const panel = document.getElementById('castPreviewPanel');
        const list = document.getElementById('castPreviewList');

        list.innerHTML = characters.map((c, i) => {
            const imgURL = this._charImageURL(c.gameobject_name);
            return `
            <div class="cast-card" data-index="${i}">
                <div class="cast-card-display">
                    <div class="cast-card-name">${c.name}</div>
                    <div class="cast-card-meta">
                        <span>${c.gender || ''}${c.ip ? ' · ' + c.ip : ''}</span>
                        <span>${c.personality_traits || ''}</span>
                        <span class="cast-card-bg">${(c.background || '').slice(0, 80)}${(c.background || '').length > 80 ? '…' : ''}</span>
                    </div>
                    ${imgURL ? `<img class="cast-card-img" src="${imgURL}" alt="${c.name}" onerror="this.style.display='none'">` : ''}
                    <button class="cast-replace-btn" data-index="${i}">替换角色</button>
                </div>
                <div class="cast-editor" style="display:none">
                    <div class="cast-mode-toggle">
                        <button class="mode-btn active" data-editor-mode="library" data-index="${i}">从角色库选</button>
                        <button class="mode-btn" data-editor-mode="custom" data-index="${i}">自定义输入</button>
                    </div>
                    <div class="cast-library-panel">
                        <select class="cast-select editor-select" data-index="${i}">
                            ${this._buildCharSelectOptions('')}
                        </select>
                        <div class="cast-char-preview" style="display:none"></div>
                    </div>
                    <div class="cast-custom-panel" style="display:none">
                        <div class="cast-custom-form">
                            <div class="cast-field-row">
                                <div class="cast-field cast-field-name">
                                    <label class="cast-field-label">姓名</label>
                                    <input type="text" class="cast-input editor-name" data-index="${i}" placeholder="角色名称">
                                </div>
                                <div class="cast-field cast-field-gender">
                                    <label class="cast-field-label">性别</label>
                                    <select class="cast-input editor-gender" data-index="${i}">
                                        <option value="未知">未知</option>
                                        <option value="男">男</option>
                                        <option value="女">女</option>
                                    </select>
                                </div>
                            </div>
                            <div class="cast-field-row">
                                <div class="cast-field">
                                    <label class="cast-field-label">IP / 来源</label>
                                    <input type="text" class="cast-input editor-ip" data-index="${i}" placeholder="如：原创">
                                </div>
                                <div class="cast-field">
                                    <label class="cast-field-label">制作方</label>
                                    <input type="text" class="cast-input editor-manufacturer" data-index="${i}" placeholder="如：用户创建">
                                </div>
                            </div>
                            <div class="cast-field-row">
                                <div class="cast-field">
                                    <label class="cast-field-label">阵营</label>
                                    <input type="text" class="cast-input editor-faction" data-index="${i}" placeholder="如：未知">
                                </div>
                                <div class="cast-field">
                                    <label class="cast-field-label">职位 / 定位</label>
                                    <input type="text" class="cast-input editor-role" data-index="${i}" placeholder="如：主角">
                                </div>
                            </div>
                            <div class="cast-field">
                                <label class="cast-field-label">性格特征</label>
                                <input type="text" class="cast-input editor-personality" data-index="${i}" placeholder="如：沉稳, 理性, 话少">
                            </div>
                            <div class="cast-field">
                                <label class="cast-field-label">背景故事</label>
                                <textarea class="cast-input editor-background" data-index="${i}" rows="3" placeholder="角色背景故事..."></textarea>
                            </div>
                        </div>
                    </div>
                    <button class="cast-confirm-btn" data-index="${i}">✓ 确认替换</button>
                </div>
            </div>
        `;
        }).join('');

        document.getElementById('downloadCastBtn').style.display = '';
        this._attachEditorListeners(list);
    },

    // 挂载替换编辑器事件
    _attachEditorListeners(container) {
        // 展开/收起编辑器
        container.querySelectorAll('.cast-replace-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.dataset.index);
                const card = container.querySelector(`.cast-card[data-index="${i}"]`);
                const editor = card.querySelector('.cast-editor');
                editor.style.display = editor.style.display === 'none' ? '' : 'none';
            });
        });

        // 编辑器模式切换（从角色库 / 自定义输入）
        container.querySelectorAll('[data-editor-mode]').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.dataset.index);
                const mode = btn.dataset.editorMode;
                const card = container.querySelector(`.cast-card[data-index="${i}"]`);
                card.querySelector('.cast-library-panel').style.display = mode === 'library' ? '' : 'none';
                card.querySelector('.cast-custom-panel').style.display = mode === 'custom' ? '' : 'none';
                card.querySelectorAll('[data-editor-mode]').forEach(b =>
                    b.classList.toggle('active', b.dataset.editorMode === mode)
                );
            });
        });

        // 角色库下拉 → 预览
        container.querySelectorAll('.editor-select').forEach(select => {
            select.addEventListener('change', () => {
                const i = parseInt(select.dataset.index);
                const card = container.querySelector(`.cast-card[data-index="${i}"]`);
                const preview = card.querySelector('.cast-char-preview');
                const char = APP_STATE.characters.find(c => c.name === select.value);
                if (char) {
                    preview.innerHTML = this._buildCharPreviewHTML(char);
                    preview.style.display = '';
                } else {
                    preview.style.display = 'none';
                }
            });
        });

        // 确认替换
        container.querySelectorAll('.cast-confirm-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.dataset.index);
                const card = container.querySelector(`.cast-card[data-index="${i}"]`);
                const isLibrary = card.querySelector('.cast-library-panel').style.display !== 'none';

                let newChar;
                if (isLibrary) {
                    const name = card.querySelector('.editor-select').value;
                    if (!name) { alert('请先选择角色'); return; }
                    const char = APP_STATE.characters.find(c => c.name === name);
                    if (!char) { alert('角色不存在'); return; }
                    newChar = Object.assign({}, char);
                } else {
                    const name = card.querySelector('.editor-name').value.trim();
                    if (!name) { alert('请填写角色名称'); return; }
                    newChar = {
                        name,
                        gender: card.querySelector('.editor-gender').value || '未知',
                        ip: card.querySelector('.editor-ip').value.trim() || '原创',
                        manufacturer: card.querySelector('.editor-manufacturer').value.trim() || '用户创建',
                        Faction: card.querySelector('.editor-faction').value.trim() || '未知',
                        role_position: card.querySelector('.editor-role').value.trim() || '未知',
                        personality_traits: card.querySelector('.editor-personality').value.trim() || '',
                        background: card.querySelector('.editor-background').value.trim() || '',
                        important_relationships: []
                    };
                }

                // 更新状态
                APP_STATE.generatedCharacters[i] = newChar;

                // 更新卡片显示内容
                const display = card.querySelector('.cast-card-display');
                display.querySelector('.cast-card-name').textContent = newChar.name;
                const spans = display.querySelectorAll('.cast-card-meta span');
                spans[0].textContent = `${newChar.gender || ''} · ${newChar.ip || ''}`;
                spans[1].textContent = newChar.personality_traits || '';
                spans[2].textContent = (newChar.background || '').slice(0, 80) + ((newChar.background || '').length > 80 ? '…' : '');

                // 更新图片
                const newImgURL = this._charImageURL(newChar.gameobject_name);
                let existingImg = display.querySelector('.cast-card-img');
                if (newImgURL) {
                    if (!existingImg) {
                        existingImg = document.createElement('img');
                        existingImg.className = 'cast-card-img';
                        existingImg.setAttribute('onerror', "this.style.display='none'");
                        display.insertBefore(existingImg, display.querySelector('.cast-replace-btn'));
                    }
                    existingImg.src = newImgURL;
                    existingImg.alt = newChar.name;
                    existingImg.style.display = '';
                } else if (existingImg) {
                    existingImg.style.display = 'none';
                }

                // 收起编辑器
                card.querySelector('.cast-editor').style.display = 'none';
            });
        });
    },

    // 构建角色图片 URL
    _charImageURL(gameobject_name) {
        if (!gameobject_name) return '';
        return `${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.CHARACTER_IMAGE}/${encodeURIComponent(gameobject_name)}`;
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

    // 构建角色库选择器的 options HTML（按性别分组）
    _buildCharSelectOptions(selectedName) {
        const grouped = {};
        for (const char of APP_STATE.characters) {
            const group = char.gender || 'other';
            if (!grouped[group]) grouped[group] = [];
            grouped[group].push(char);
        }
        const groupLabels = { female: '女性', male: '男性', none: '机械/无性别', other: '其他' };
        let html = '<option value="">请选择角色…</option>';
        for (const [group, chars] of Object.entries(grouped)) {
            html += `<optgroup label="${groupLabels[group] || group}">`;
            for (const char of chars) {
                const traits = Array.isArray(char.traits) && char.traits.length
                    ? ` · ${char.traits.slice(0, 2).join('/')}` : '';
                const sel = char.name === selectedName ? ' selected' : '';
                html += `<option value="${char.name}"${sel}>${char.name}${traits}</option>`;
            }
            html += '</optgroup>';
        }
        return html;
    },

    // 生成角色预览 HTML（只显示展示字段，不显示 gameobject_name 等引擎字段）
    _buildCharPreviewHTML(char) {
        const app = char.appearance || {};
        const height = app.height || '';
        const bodyType = (app.body_type || '').slice(0, 60) + ((app.body_type || '').length > 60 ? '…' : '');
        const traits = Array.isArray(char.traits) ? char.traits.join(' · ') : '';
        const bg = (char.background || '').slice(0, 80) + ((char.background || '').length > 80 ? '…' : '');
        const gobj = char.gameobject_name || '';
        const imgURL = gobj
            ? `${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.CHARACTER_IMAGE}/${encodeURIComponent(gobj)}`
            : '';
        return `
            <div class="char-preview-layout">
                <div class="char-preview-info">
                    <div class="char-preview-name">${char.name}</div>
                    ${height ? `<div class="char-preview-ip">${char.gender || ''} · ${height}</div>` : ''}
                    ${traits ? `<div class="char-preview-traits">${traits}</div>` : ''}
                    ${bodyType ? `<div class="char-preview-traits" style="color:rgba(224,224,224,0.55)">${bodyType}</div>` : ''}
                    ${bg ? `<div class="char-preview-bg">${bg}</div>` : ''}
                </div>
                ${imgURL ? `<img class="char-preview-img" src="${imgURL}" alt="${char.name}" onerror="this.style.display='none'">` : ''}
            </div>
        `;
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
        const previewHTML = previewChar ? this._buildCharPreviewHTML(previewChar) : '';

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
                    preview.innerHTML = this._buildCharPreviewHTML(char);
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

    // 渲染剧本为可读格式（对白/氛围可编辑）
    renderScriptViewer(scriptData) {
        APP_STATE.currentScriptData = JSON.parse(JSON.stringify(scriptData));
        const viewer = document.getElementById('scriptViewer');

        viewer.innerHTML = scriptData.map((scene, si) => {
            const info    = scene['scene information'] || {};
            const who     = (info.who || []).join('、');
            const initPos = (scene['initial position'] || [])
                .map(p => `${p.character} → ${p.position}`).join('　');

            const beats = (scene['scene'] || []).map((beat, bi) => {
                if (beat.speaker !== undefined) {
                    const actions = (beat.actions || [])
                        .map(a => `[${a.character}] ${a.action}${a.state ? ' (' + a.state + ')' : ''}`)
                        .join(' · ');
                    const positions = (beat['current position'] || [])
                        .map(p => `${p.character}→${p.position}`).join('　');
                    return `
                    <div class="sv-beat sv-beat-dialogue">
                        <div class="sv-speaker">${beat.speaker}</div>
                        <textarea class="sv-content sv-editable"
                            data-scene="${si}" data-beat="${bi}" data-field="content"
                            rows="3"></textarea>
                        ${actions ? `<div class="sv-meta"><span class="sv-label">动作</span><span class="sv-value">${actions}</span></div>` : ''}
                        ${positions ? `<div class="sv-meta"><span class="sv-label">站位</span><span class="sv-value">${positions}</span></div>` : ''}
                        ${beat.motion_description !== undefined ? `
                        <div class="sv-meta sv-atmosphere">
                            <span class="sv-label">氛围</span>
                            <input class="sv-motion-desc sv-editable"
                                data-scene="${si}" data-beat="${bi}" data-field="motion_description">
                        </div>` : ''}
                    </div>`;
                } else if (beat.move) {
                    const moves = (beat.move || [])
                        .map(m => `${m.character} 移至 ${m.destination}`).join('　');
                    return `
                    <div class="sv-beat sv-beat-move">
                        <span class="sv-move-arrow">▶</span>
                        <span class="sv-move-text">${moves}</span>
                    </div>`;
                }
                return '';
            }).join('');

            return `
            <div class="sv-scene">
                <div class="sv-scene-header">
                    <span class="sv-scene-num">第 ${si + 1} 幕</span>
                    <span class="sv-scene-where">${info.where || ''}</span>
                    <span class="sv-scene-who">${who}</span>
                </div>
                ${info.what ? `<p class="sv-scene-what">${info.what}</p>` : ''}
                ${initPos ? `<div class="sv-init-pos"><span class="sv-label">初始站位</span> ${initPos}</div>` : ''}
                <div class="sv-beats">${beats}</div>
            </div>`;
        }).join('');

        // 设置可编辑字段的初始值（避免 HTML 转义问题）
        viewer.querySelectorAll('.sv-editable').forEach(el => {
            const si    = parseInt(el.dataset.scene);
            const bi    = parseInt(el.dataset.beat);
            const field = el.dataset.field;
            const val   = (APP_STATE.currentScriptData[si]?.['scene']?.[bi]?.[field]) ?? '';
            el.value = val;
        });

        this._attachScriptViewerListeners(viewer);
    },

    _attachScriptViewerListeners(viewer) {
        viewer.querySelectorAll('.sv-editable').forEach(el => {
            el.addEventListener('input', () => {
                const si    = parseInt(el.dataset.scene);
                const bi    = parseInt(el.dataset.beat);
                const field = el.dataset.field;
                if (APP_STATE.currentScriptData[si]?.['scene']?.[bi]) {
                    APP_STATE.currentScriptData[si]['scene'][bi][field] = el.value;
                }
            });
        });
    },

    // 在日志中渲染结构化输出块
    addOutputBlock(event) {
        const logContent = document.getElementById('logContent');
        const timestamp = new Date().toLocaleTimeString('zh-CN', {
            hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'
        });

        const wrap = document.createElement('div');
        wrap.className = 'log-entry output-block-entry';

        const fmt = event.format;
        const agent = event.agent || '';
        const data = event.data;

        let bodyHTML = '';
        let labelHTML = '';

        if (fmt === 'script' && Array.isArray(data)) {
            labelHTML = `<span class="ob-label">剧本 · ${data.length} 幕</span>`;
            bodyHTML = data.map((scene, si) => {
                const info = scene['scene information'] || {};
                const who = (info.who || []).join('、');
                const initPos = (scene['initial position'] || [])
                    .map(p => `${p.character || ''} → ${p.position || ''}`)
                    .join('　');
                const beats = (scene['scene'] || []).map(beat => {
                    if (beat.speaker !== undefined) {
                        const content = (beat.content || '').slice(0, 60) + ((beat.content || '').length > 60 ? '…' : '');
                        const shot = beat.shot || '';
                        const anchors = Array.isArray(beat.shot_anchors) ? beat.shot_anchors.join(', ') : '';
                        const camera = beat.camera !== undefined ? `机位 ${beat.camera}` : '';
                        const actions = (beat.actions || [])
                            .map(a => `[${a.character || ''}] ${a.action || ''}${a.state ? ` (${a.state})` : ''}`)
                            .join(' · ');
                        const positions = (beat['current position'] || [])
                            .map(p => `${p.character || ''}→${p.position || ''}`)
                            .join('　');
                        const motion = beat.motion_description || '';
                        const shotMeta = [shot, anchors ? `锚点 ${anchors}` : '', camera].filter(Boolean).join(' · ');
                        return `
                        <div class="ob-beat ob-dialogue">
                            <span class="ob-speaker">${this._esc(beat.speaker)}</span>
                            <span class="ob-content">${this._esc(content)}</span>
                            ${shotMeta ? `<div class="ob-beat-meta">镜头：${this._esc(shotMeta)}</div>` : ''}
                            ${actions ? `<div class="ob-beat-meta">动作：${this._esc(actions)}</div>` : ''}
                            ${positions ? `<div class="ob-beat-meta">站位：${this._esc(positions)}</div>` : ''}
                            ${motion ? `<div class="ob-beat-meta">氛围：${this._esc(motion)}</div>` : ''}
                        </div>`;
                    } else if (beat.move) {
                        const moves = (beat.move || []).map(m => `${m.character} → ${m.destination}`).join('　');
                        const positions = (beat['current position'] || [])
                            .map(p => `${p.character || ''}→${p.position || ''}`)
                            .join('　');
                        return `
                        <div class="ob-beat ob-move">
                            <div>▶ ${this._esc(moves)}</div>
                            ${positions ? `<div class="ob-beat-meta">站位：${this._esc(positions)}</div>` : ''}
                        </div>`;
                    }
                    return '';
                }).join('');
                return `
                <div class="ob-scene">
                    <div class="ob-scene-header">第 ${si + 1} 幕 · <span class="ob-where">${this._esc(info.where || '')}</span> · <span class="ob-who">${this._esc(who)}</span></div>
                    ${info.what ? `<div class="ob-what">${this._esc(info.what)}</div>` : ''}
                    ${initPos ? `<div class="ob-what">初始站位：${this._esc(initPos)}</div>` : ''}
                    ${beats}
                </div>`;
            }).join('');

        } else if (fmt === 'stage' && data) {
            labelHTML = `<span class="ob-label">阶段产物</span>`;
            const agentKey = (agent || '').toLowerCase();
            if (agentKey.includes('concept')) {
                bodyHTML = `
                    ${data.logline ? `<div class="ob-what"><strong>Logline：</strong>${this._esc(data.logline)}</div>` : ''}
                    ${data.core_conflict ? `<div class="ob-beat-meta">核心冲突：${this._esc(data.core_conflict)}</div>` : ''}
                    ${data.tone ? `<div class="ob-beat-meta">基调：${this._esc(data.tone)}</div>` : ''}
                    ${data.stakes ? `<div class="ob-beat-meta">代价：${this._esc(data.stakes)}</div>` : ''}
                `;
            } else if (agentKey.includes('synopsis')) {
                bodyHTML = `
                    ${data.synopsis ? `<div class="ob-what">${this._esc(data.synopsis)}</div>` : ''}
                    ${data.opening ? `<div class="ob-beat-meta">开场：${this._esc(data.opening)}</div>` : ''}
                    ${data.turning_point ? `<div class="ob-beat-meta">转折：${this._esc(data.turning_point)}</div>` : ''}
                    ${data.ending_direction ? `<div class="ob-beat-meta">走向：${this._esc(data.ending_direction)}</div>` : ''}
                `;
            } else if (agentKey.includes('characterbios')) {
                const bios = Array.isArray(data.character_bios) ? data.character_bios : [];
                bodyHTML = bios.map((b, idx) => `
                    <div class="ob-scene">
                        <div class="ob-scene-header">角色 ${idx + 1} · ${this._esc(b.name || '未命名')}</div>
                        ${b.role ? `<div class="ob-beat-meta">叙事功能：${this._esc(b.role)}</div>` : ''}
                        ${b.goal ? `<div class="ob-beat-meta">目标：${this._esc(b.goal)}</div>` : ''}
                        ${b.inner_conflict ? `<div class="ob-beat-meta">内在冲突：${this._esc(b.inner_conflict)}</div>` : ''}
                        ${b.relationship_hint ? `<div class="ob-beat-meta">关系线索：${this._esc(b.relationship_hint)}</div>` : ''}
                    </div>
                `).join('');
            } else if (agentKey.includes('treatment')) {
                const beats = Array.isArray(data.treatment) ? data.treatment : [];
                bodyHTML = beats.map((b) => `
                    <div class="ob-scene">
                        <div class="ob-scene-header">Beat ${this._esc(b.beat || '')}</div>
                        ${b.objective ? `<div class="ob-beat-meta">目标：${this._esc(b.objective)}</div>` : ''}
                        ${b.conflict ? `<div class="ob-beat-meta">冲突：${this._esc(b.conflict)}</div>` : ''}
                        ${b.outcome ? `<div class="ob-beat-meta">结果：${this._esc(b.outcome)}</div>` : ''}
                    </div>
                `).join('') + (data.draft_guidance ? `<div class="ob-revision">💡 起草指引：${this._esc(data.draft_guidance)}</div>` : '');
            } else {
                bodyHTML = `<div class="ob-beat-meta">${this._esc(JSON.stringify(data, null, 2))}</div>`;
            }
        } else if (fmt === 'feedback' && data) {
            const hasIssues = data.has_issues;
            labelHTML = hasIssues
                ? `<span class="ob-label ob-issues">⚠ ${(data.issues || []).length} 个问题</span>`
                : `<span class="ob-label ob-pass">✓ 无问题</span>`;
            if (hasIssues) {
                const issuesHTML = (data.issues || []).map(issue => `
                    <div class="ob-issue">
                        ${issue.type ? `<span class="ob-issue-type">${this._esc(issue.type)}</span>` : ''}
                        <span class="ob-issue-desc">${this._esc(issue.description || '')}</span>
                        ${issue.location ? `<span class="ob-issue-loc">${this._esc(issue.location)}</span>` : ''}
                    </div>`).join('');
                const revision = data.revision_instruction || '';
                bodyHTML = issuesHTML + (revision ? `<div class="ob-revision">💡 ${this._esc(revision)}</div>` : '');
            }

        } else if (fmt === 'validation' && data) {
            const valid = data.valid;
            const errors = data.errors || [];
            const warnings = data.warnings || [];
            labelHTML = valid
                ? `<span class="ob-label ob-pass">✓ 验证通过</span>`
                : `<span class="ob-label ob-issues">✗ ${errors.length} 个错误</span>`;
            bodyHTML = [
                ...errors.map(e => `<div class="ob-val-error">✗ ${this._esc(e)}</div>`),
                ...warnings.map(w => `<div class="ob-val-warn">⚠ ${this._esc(w)}</div>`)
            ].join('');
        }

        const isCollapsible = fmt === 'script';
        wrap.innerHTML = `
            <div class="ob-header ${isCollapsible ? 'ob-collapsible' : ''}">
                <span class="log-timestamp">[${timestamp}]</span>
                <span class="ob-agent">${this._esc(agent)}</span>
                ${labelHTML}
                ${isCollapsible ? '<span class="ob-toggle">▶</span>' : ''}
            </div>
            ${bodyHTML ? `<div class="ob-body" ${isCollapsible ? 'style="display:none"' : ''}>${bodyHTML}</div>` : ''}
        `;

        if (isCollapsible) {
            const header = wrap.querySelector('.ob-header');
            const body = wrap.querySelector('.ob-body');
            if (header && body) {
                header.addEventListener('click', () => {
                    const open = body.style.display !== 'none';
                    body.style.display = open ? 'none' : '';
                    header.querySelector('.ob-toggle').textContent = open ? '▶' : '▼';
                });
            }
        }

        logContent.appendChild(wrap);
        logContent.scrollTop = logContent.scrollHeight;
    },

    // HTML 转义辅助
    _esc(str) {
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
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
