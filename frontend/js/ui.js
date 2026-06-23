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
            meeting: '创意会议',
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
    showSuccess(filename, actorsProfileFilename, positionFilename, warnings = [], positionPlanFilename = null, positionDetailFilename = null, cameraScriptFilename = null, estimatedDuration = null, title = null) {
        const resultPanel = document.getElementById('resultPanel');
        const messageEl = document.getElementById('resultMessage');
        const durationEl = document.getElementById('resultDuration');

        messageEl.textContent = title ? `片名：《${title}》 · 文件：${filename}` : `剧本：${filename}`;
        if (estimatedDuration) {
            const secs = estimatedDuration.estimated_duration_seconds;
            const lowSecs = Math.max(0, secs - 30);
            const highSecs = secs + 30;
            const lowMin = Math.floor(secs / 60);
            const highMin = Math.ceil((secs + 30) / 60);
            durationEl.textContent = `⏱ 估算时长：约 ${lowMin}-${highMin}分钟（对白${estimatedDuration.dialogue_lines}句，共${estimatedDuration.dialogue_chars}字）`;
            durationEl.style.display = '';
        } else {
            durationEl.style.display = 'none';
        }
        resultPanel.style.display = 'block';

        APP_STATE.currentFilename = filename;
        APP_STATE.currentActorsProfileFilename = actorsProfileFilename || null;
        APP_STATE.currentPositionFilename = positionFilename || null;
        APP_STATE.currentPositionPlanFilename = positionPlanFilename || null;
        APP_STATE.currentPositionDetailFilename = positionDetailFilename || null;
        APP_STATE.currentCameraScriptFilename = cameraScriptFilename || null;

        const actorsBtn = document.getElementById('downloadActorsBtn');
        if (actorsBtn) {
            actorsBtn.style.display = actorsProfileFilename ? '' : 'none';
        }

        const wordBtn = document.getElementById('downloadWordBtn');
        if (wordBtn) wordBtn.style.display = filename ? '' : 'none';

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

        const cameraBtn = document.getElementById('downloadCameraScriptBtn');
        if (cameraBtn) {
            cameraBtn.style.display = cameraScriptFilename ? '' : 'none';
        }

        const zipBtn = document.getElementById('downloadSessionZipBtn');
        if (zipBtn) {
            zipBtn.style.display = APP_STATE.currentSessionId ? '' : 'none';
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

    // 渲染场景池（多选复选框）：有锚点（regions 非空）才可勾选，无锚点禁用并标注
    renderScenes(scenes) {
        const list = document.getElementById('scenePoolList');
        list.innerHTML = scenes.map(scene => {
            const hasAnchor = scene.regions && scene.regions.length > 0;
            const disabledAttr = hasAnchor ? '' : 'disabled';
            const note = hasAnchor ? '' : '<span class="scene-pool-note">暂无坐标锚点，不可选</span>';
            return `
            <label class="scene-pool-item ${hasAnchor ? '' : 'is-disabled'}">
                <input type="checkbox" class="scene-pool-checkbox" value="${scene.id}" ${disabledAttr}>
                <span class="scene-pool-name">${scene.name}</span>
                ${note}
            </label>`;
        }).join('');
    },

    // 渲染「每幕 → 场景」分配下拉（仅多场景时显示）；选项=场景池内场景
    renderActSceneMap(poolIds, actCount) {
        const wrap = document.getElementById('actSceneMap');
        const listEl = document.getElementById('actSceneList');
        if (!poolIds || poolIds.length <= 1) {
            wrap.style.display = 'none';
            listEl.innerHTML = '';
            return;
        }
        const sceneById = {};
        (APP_STATE.scenes || []).forEach(s => { sceneById[s.id] = s; });
        const options = poolIds
            .map(id => `<option value="${id}">${(sceneById[id] || {}).name || id}</option>`)
            .join('');
        let html = '';
        for (let i = 0; i < actCount; i++) {
            // 默认按顺序轮转分配
            const current = APP_STATE.actScenes[i] || poolIds[i % poolIds.length];
            APP_STATE.actScenes[i] = current;
            html += `
            <div class="act-scene-row">
                <span class="act-scene-label">第 ${i + 1} 幕</span>
                <select class="act-scene-select" data-act-index="${i}">
                    ${options.replace(`value="${current}"`, `value="${current}" selected`)}
                </select>
            </div>`;
        }
        APP_STATE.actScenes = APP_STATE.actScenes.slice(0, actCount);
        listEl.innerHTML = html;
        wrap.style.display = 'block';
    },

    // 显示场景信息（支持单个或多个已选场景；多场景时逐个分块展示）
    showSceneInfo(scenes) {
        const info = document.getElementById('sceneInfo');
        const scroll = info.querySelector('.scene-info-scroll');
        const list = Array.isArray(scenes) ? scenes.filter(Boolean) : (scenes ? [scenes] : []);

        if (list.length === 0) {
            info.style.display = 'none';
            return;
        }

        const multi = list.length > 1;
        let html = '';
        list.forEach(scene => {
            html += `<div class="scene-info-block">`;
            if (multi) html += `<div class="scene-info-title">${scene.name}</div>`;
            html += `<div class="info-label">场景描述</div>`;
            html += `<p class="scene-description">${scene.description || ''}</p>`;
            html += `<div class="info-label">场景区域与锚点</div>`;
            html += `<div class="positions-list">`;
            if (scene.regions && scene.regions.length > 0) {
                for (const region of scene.regions) {
                    html += `<div class="position-group">`;
                    html += `<div class="position-group-title">${region.name}</div>`;
                    html += `<p class="region-description">${region.description}</p>`;
                    if (region.markers && region.markers.length > 0) {
                        html += `<p class="region-markers"><span class="markers-label">标志性物体：</span>${region.markers.join('、')}</p>`;
                    }
                    html += `</div>`;
                }
            } else {
                html += '<p>暂无区域信息</p>';
            }
            html += `</div></div>`;
        });

        scroll.innerHTML = html;
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
                        ${this._buildCharDropdown('', i, 'cast-select editor-select')}
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
        // 自定义下拉（悬停预览角色信息/图片）
        this._attachDropdown(container);
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
    // 构建下拉选项的悬停提示（角色信息摘要）
    _buildCharTitle(char) {
        const lines = [];
        const app = char.appearance || {};
        const basics = [char.gender, char.ip, app.height].filter(Boolean).join(' · ');
        if (basics) lines.push(basics);
        const traits = Array.isArray(char.traits) && char.traits.length
            ? char.traits.join(' · ')
            : (char.personality_traits || '');
        if (traits) lines.push(`性格：${traits}`);
        const bg = (char.background || '').trim();
        if (bg) lines.push(`背景：${bg.slice(0, 120)}${bg.length > 120 ? '…' : ''}`);
        // title 属性用换行符分隔多行，并转义引号
        return lines.join('\n').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },

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
                const title = this._buildCharTitle(char);
                html += `<option value="${char.name}"${sel} title="${title}">${char.name}${traits}</option>`;
            }
            html += '</optgroup>';
        }
        return html;
    },

    // 构建自定义下拉（保留隐藏的原生 <select> 以兼容既有 change/value 逻辑，
    // 额外提供可在选项上悬停、显示角色信息与图片的菜单）
    _buildCharDropdown(selectedName, i, selectClass) {
        const sel = APP_STATE.characters.find(c => c.name === selectedName);
        const label = sel ? sel.name : '请选择角色…';
        return `
        <div class="cast-dd" data-index="${i}">
            <select class="${selectClass}" data-index="${i}" style="display:none">
                ${this._buildCharSelectOptions(selectedName)}
            </select>
            <button type="button" class="cast-dd-trigger${sel ? '' : ' placeholder'}">
                <span class="cast-dd-trigger-text">${this._esc(label)}</span>
                <span class="cast-dd-arrow">▾</span>
            </button>
            <div class="cast-dd-menu" style="display:none">
                ${this._buildCharMenuItems(selectedName)}
            </div>
        </div>`;
    },

    // 构建自定义下拉的选项列表
    _buildCharMenuItems(selectedName) {
        const grouped = {};
        for (const char of APP_STATE.characters) {
            const group = char.gender || 'other';
            if (!grouped[group]) grouped[group] = [];
            grouped[group].push(char);
        }
        const groupLabels = { female: '女性', male: '男性', none: '机械/无性别', other: '其他' };
        let html = `<div class="cast-dd-item${selectedName ? '' : ' selected'}" data-name=""><span class="cast-dd-thumb cast-dd-thumb-empty"></span><span class="cast-dd-name">请选择角色…</span></div>`;
        for (const [group, chars] of Object.entries(grouped)) {
            html += `<div class="cast-dd-group-label">${groupLabels[group] || group}</div>`;
            for (const char of chars) {
                const traits = Array.isArray(char.traits) && char.traits.length
                    ? ` · ${char.traits.slice(0, 2).join('/')}` : '';
                const sel = char.name === selectedName ? ' selected' : '';
                const hasImg = char.gameobject_name ? ' has-img' : '';
                const imgURL = char.gameobject_name ? this._charImageURL(char.gameobject_name) : '';
                const thumb = imgURL
                    ? `<img class="cast-dd-thumb" src="${imgURL}" alt="" loading="lazy" onerror="this.classList.add('cast-dd-thumb-empty');this.removeAttribute('src')">`
                    : `<span class="cast-dd-thumb cast-dd-thumb-empty"></span>`;
                html += `<div class="cast-dd-item${sel}${hasImg}" data-name="${this._esc(char.name)}">${thumb}<span class="cast-dd-name">${this._esc(char.name + traits)}</span></div>`;
            }
        }
        return html;
    },

    // 挂载自定义下拉的交互（展开/收起、悬停预览、选择同步到隐藏 select）
    _attachDropdown(container) {
        container.querySelectorAll('.cast-dd').forEach(dd => {
            const trigger = dd.querySelector('.cast-dd-trigger');
            const menu = dd.querySelector('.cast-dd-menu');
            const select = dd.querySelector('select');
            menu._ddParent = dd; // 记住归位的父节点（打开时会移到 body）

            trigger.addEventListener('click', (e) => {
                e.stopPropagation();
                const isOpen = menu.style.display !== 'none';
                this._closeAllMenus();
                if (!isOpen) {
                    // 移到 body 下，避开 .main-card 的 transform 形成的 fixed 包含块
                    document.body.appendChild(menu);
                    menu.style.display = '';
                    this._positionMenu(trigger, menu);
                }
            });

            menu.addEventListener('click', (e) => e.stopPropagation());

            menu.querySelectorAll('.cast-dd-item').forEach(item => {
                item.addEventListener('mouseenter', () => this._showHoverCard(item));
                item.addEventListener('mouseleave', () => this._hideHoverCard());
                item.addEventListener('click', () => {
                    const name = item.dataset.name;
                    select.value = name;
                    dd.querySelector('.cast-dd-trigger-text').textContent = name || '请选择角色…';
                    trigger.classList.toggle('placeholder', !name);
                    menu.querySelectorAll('.cast-dd-item').forEach(it => it.classList.toggle('selected', it === item));
                    this._closeAllMenus();
                    // 触发原生 change，复用既有处理逻辑
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                });
            });
        });

        // 点击空白处 / 滚动 / 缩放时关闭（全局只绑定一次）
        if (!this._ddGlobalBound) {
            document.addEventListener('click', () => this._closeAllMenus());
            // 捕获阶段监听页面滚动：fixed 菜单不随页面移动故关闭；但在菜单内部滚动列表时不关
            window.addEventListener('scroll', (e) => {
                const t = e.target;
                if (t && t.nodeType === 1 && t.closest && t.closest('.cast-dd-menu')) return;
                this._closeAllMenus();
            }, true);
            window.addEventListener('resize', () => this._closeAllMenus());
            this._ddGlobalBound = true;
        }
    },

    // 关闭所有下拉菜单，并把（曾移到 body 的）菜单归位到原 .cast-dd 内
    _closeAllMenus() {
        document.querySelectorAll('.cast-dd-menu').forEach(m => {
            m.style.display = 'none';
            if (m._ddParent && m.parentElement !== m._ddParent) {
                m._ddParent.appendChild(m);
            }
        });
        this._hideHoverCard();
    },

    // 将 fixed 定位的菜单对齐到触发按钮下方；下方空间不足时向上展开
    _positionMenu(trigger, menu) {
        const r = trigger.getBoundingClientRect();
        const gap = 2;
        const margin = 8;
        menu.style.width = r.width + 'px';
        menu.style.left = r.left + 'px';
        // 先按内容测量需要的高度（受 CSS max-height 限制）
        menu.style.maxHeight = '';
        menu.style.top = '-9999px';
        const needed = menu.offsetHeight;
        const spaceBelow = window.innerHeight - r.bottom - gap - margin;
        const spaceAbove = r.top - gap - margin;
        if (spaceBelow >= needed || spaceBelow >= spaceAbove) {
            // 向下展开
            menu.style.top = (r.bottom + gap) + 'px';
            menu.style.maxHeight = Math.min(360, Math.max(120, spaceBelow)) + 'px';
        } else {
            // 向上展开
            const h = Math.min(360, Math.max(120, spaceAbove));
            menu.style.maxHeight = h + 'px';
            menu.style.top = Math.max(margin, r.top - gap - Math.min(needed, h)) + 'px';
        }
    },

    // 悬停某个选项时，在旁边显示该角色的信息与图片
    _showHoverCard(item) {
        const char = APP_STATE.characters.find(c => c.name === item.dataset.name);
        if (!char) { this._hideHoverCard(); return; }
        let card = document.getElementById('castDdHoverCard');
        if (!card) {
            card = document.createElement('div');
            card.id = 'castDdHoverCard';
            card.className = 'cast-dd-hovercard';
            document.body.appendChild(card);
        }
        card.innerHTML = this._buildCharPreviewHTML(char);
        card.style.display = '';
        // 定位：默认在选项右侧，空间不足则放到左侧
        const r = item.getBoundingClientRect();
        const cardW = card.offsetWidth || 260;
        const cardH = card.offsetHeight || 200;
        let left = r.right + 10;
        if (left + cardW > window.innerWidth - 8) left = r.left - cardW - 10;
        if (left < 8) left = 8;
        let top = r.top;
        if (top + cardH > window.innerHeight - 8) top = window.innerHeight - cardH - 8;
        if (top < 8) top = 8;
        card.style.left = left + 'px';
        card.style.top = top + 'px';
    },

    _hideHoverCard() {
        const card = document.getElementById('castDdHoverCard');
        if (card) card.style.display = 'none';
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
                ${this._buildCharDropdown(slot.selectedName, i, 'cast-select')}
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
        // 自定义下拉（悬停预览角色信息/图片）
        this._attachDropdown(container);
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
        this._redrawScriptViewer();
    },

    _redrawScriptViewer() {
        const viewer = document.getElementById('scriptViewer');
        const scrollTop = viewer.scrollTop;
        const data = APP_STATE.currentScriptData;

        const allChars   = this._getCharList(data);
        const actionsFlat = this._flatActions();
        const sceneOpts  = this._getSceneOpts();

        viewer.innerHTML = data.map((scene, si) => {
            const info      = scene['scene information'] || {};
            const who       = (info.who || []).join('、');
            const initPos   = (scene['initial position'] || [])
                .map(p => {
                    const anchor = (APP_STATE.positionPlanMap || {})[p.position] || p.position;
                    return `${p.character} → ${anchor}`;
                }).join('　');
            const sceneChars = (info.who && info.who.length) ? info.who : allChars;

            const beats = (scene['scene'] || []).map((beat, bi) => {
                if (beat.speaker !== undefined) {
                    const positions = (beat['current position'] || [])
                        .map(p => {
                            const pos = p.position;
                            const anchor = (APP_STATE.positionPlanMap || {})[pos] || pos;
                            return `${p.character}→${anchor}`;
                        }).join('　');
                    // 说话人下拉
                    const speakerOpts = sceneChars.map(c =>
                        `<option value="${this._esc(c)}"${c === beat.speaker ? ' selected' : ''}>${this._esc(c)}</option>`
                    ).join('');
                    const actionsHtml = this._renderActionEditor(si, bi, beat.actions || [], sceneChars, actionsFlat);
                    const shotHtml    = this._renderShotSelects(si, bi, beat);
                    return `
                    <div class="sv-beat sv-beat-dialogue" data-scene="${si}" data-beat="${bi}">
                        <div class="sv-beat-top">
                            <select class="sv-speaker-select" data-scene="${si}" data-beat="${bi}">
                                ${speakerOpts}
                            </select>
                            <button class="sv-del-beat" data-scene="${si}" data-beat="${bi}" title="删除此对话">✕</button>
                        </div>
                        <textarea class="sv-content sv-editable"
                            data-scene="${si}" data-beat="${bi}" data-field="content"
                            rows="3" placeholder="对话内容..."></textarea>
                        ${shotHtml}
                        ${actionsHtml}
                        ${positions ? `<div class="sv-meta"><span class="sv-label">站位</span><span class="sv-value">${this._esc(positions)}</span></div>` : ''}
                    </div>`;
                } else if (beat.move) {
                    const moves = (beat.move || [])
                        .map(m => `${m.character} 移至 ${m.destination}`).join('　');
                    return `
                    <div class="sv-beat sv-beat-move" data-scene="${si}" data-beat="${bi}">
                        <span class="sv-move-arrow">▶</span>
                        <span class="sv-move-text">${this._esc(moves)}</span>
                        <button class="sv-del-beat" data-scene="${si}" data-beat="${bi}" title="删除">✕</button>
                    </div>`;
                }
                return '';
            }).join('');

            return `
            <div class="sv-scene" data-scene="${si}">
                <div class="sv-scene-header">
                    <span class="sv-scene-num">第 ${si + 1} 幕</span>
                    <span class="sv-scene-who">${this._esc(who)}</span>
                    <button class="sv-del-scene" data-scene="${si}" title="删除此幕">✕</button>
                </div>
                <textarea class="sv-what-input"
                    data-scene="${si}" data-field="what"
                    rows="2" placeholder="场景核心事件描述...">${this._esc(info.what || '')}</textarea>
                ${initPos ? `<div class="sv-init-pos"><span class="sv-label">初始站位</span> ${this._esc(initPos)}</div>` : ''}
                <div class="sv-beats">${beats}</div>
                <button class="sv-add-beat" data-scene="${si}">＋ 添加对话</button>
            </div>`;
        }).join('');

        viewer.innerHTML += `<button class="sv-add-scene">＋ 添加幕</button>`;

        // content textarea 赋值（避免 HTML 转义）
        viewer.querySelectorAll('.sv-editable').forEach(el => {
            const si    = parseInt(el.dataset.scene);
            const bi    = parseInt(el.dataset.beat);
            const field = el.dataset.field;
            const val   = (APP_STATE.currentScriptData[si]?.['scene']?.[bi]?.[field]) ?? '';
            el.value = val;
        });

        viewer.scrollTop = scrollTop;
        this._attachScriptViewerListeners(viewer);
    },

    // 获取角色列表：优先 generatedCharacters，否则从剧本提取
    _getCharList(data) {
        if (APP_STATE.generatedCharacters?.length) {
            return APP_STATE.generatedCharacters.map(c => c.name).filter(Boolean);
        }
        return this._collectChars(data);
    },

    // 获取场景选项（仅从剧本提取，不允许切换场景类型）
    _getSceneOpts() {
        const set = new Set();
        (APP_STATE.currentScriptData || []).forEach(scene => {
            const w = scene['scene information']?.where;
            if (w) set.add(w);
        });
        return [...set].map(w => ({ id: w, label: w }));
    },

    _collectChars(data) {
        const set = new Set();
        data.forEach(scene => {
            (scene['scene information']?.who || []).forEach(c => set.add(c));
            (scene['scene'] || []).forEach(beat => { if (beat.speaker) set.add(beat.speaker); });
        });
        return [...set];
    },

    _flatActions() {
        const result = [];
        const groups = APP_STATE.availableActions || {};
        Object.values(groups).forEach(list => list.forEach(a => {
            if (!result.find(x => x.trigger === a.trigger)) result.push(a);
        }));
        return result;
    },

    // 动作下拉的显示名：取中文描述的第一个分句作为简短中文名；无描述则回退英文 ID
    _actionLabel(a) {
        const d = (a.description || '').trim();
        if (!d) return a.trigger;
        const short = d.split(/[，,。（(]/)[0].trim();
        return short || a.trigger;
    },

    // 构建 trigger → 显示名 映射；同名动作按出场顺序加序号 1/2/3 区分。底层 value 仍是英文 ID
    _buildActionLabelMap(list) {
        const base = list.map(a => ({ trigger: a.trigger, name: this._actionLabel(a) }));
        const counts = {};
        base.forEach(b => { counts[b.name] = (counts[b.name] || 0) + 1; });
        const seen = {};
        const map = {};
        base.forEach(b => {
            if (counts[b.name] > 1) {
                seen[b.name] = (seen[b.name] || 0) + 1;
                map[b.trigger] = `${b.name}${seen[b.name]}`;
            } else {
                map[b.trigger] = b.name;
            }
        });
        return map;
    },

    _renderShotSelects(si, bi, beat) {
        const shotTypes  = APP_STATE.shotTypes  || [];
        const curType  = beat.shot_type  || '';

        const typeOpts  = shotTypes.map(t =>
            `<option value="${this._esc(t)}"${t === curType  ? ' selected' : ''}>${this._esc(t)}</option>`
        ).join('');

        return `
        <div class="sv-shot-editor">
            <span class="sv-label">镜头</span>
            <select class="sv-shot-type" data-scene="${si}" data-beat="${bi}">${typeOpts}</select>
            <textarea class="sv-shot-desc sv-editable"
                data-scene="${si}" data-beat="${bi}" data-field="shot_description"
                rows="2" placeholder="镜头描述..."></textarea>
        </div>`;
    },

    _renderActionEditor(si, bi, actions, sceneChars, actionsFlat) {
        const labelMap = this._buildActionLabelMap(actionsFlat);
        const rows = actions.map((act, ai) => `
            <div class="sv-action-row" data-ai="${ai}">
                <select class="sv-action-char" data-scene="${si}" data-beat="${bi}" data-ai="${ai}">
                    ${sceneChars.map(c => `<option value="${this._esc(c)}"${c === act.character ? ' selected' : ''}>${this._esc(c)}</option>`).join('')}
                </select>
                <select class="sv-action-name" data-scene="${si}" data-beat="${bi}" data-ai="${ai}">
                    ${actionsFlat.map(a => `<option value="${this._esc(a.trigger)}"${a.trigger === act.action ? ' selected' : ''} title="${this._esc(a.description || a.trigger)}">${this._esc(labelMap[a.trigger] || this._actionLabel(a))}</option>`).join('')}
                </select>
                <button class="sv-del-action" data-scene="${si}" data-beat="${bi}" data-ai="${ai}">✕</button>
            </div>`).join('');

        return `
        <div class="sv-actions-editor">
            <span class="sv-label">动作</span>
            <div class="sv-action-list">${rows}</div>
            <button class="sv-add-action" data-scene="${si}" data-beat="${bi}">＋ 动作</button>
        </div>`;
    },

    _attachScriptViewerListeners(viewer) {
        const data = APP_STATE.currentScriptData;

        // 对话内容 textarea
        viewer.querySelectorAll('.sv-editable').forEach(el => {
            el.addEventListener('input', () => {
                const si = parseInt(el.dataset.scene);
                const bi = parseInt(el.dataset.beat);
                if (data[si]?.['scene']?.[bi]) {
                    data[si]['scene'][bi][el.dataset.field] = el.value;
                }
            });
        });

        // 场景概述 textarea
        viewer.querySelectorAll('.sv-what-input').forEach(el => {
            el.addEventListener('input', () => {
                const si = parseInt(el.dataset.scene);
                if (data[si]?.['scene information']) {
                    data[si]['scene information'].what = el.value;
                }
            });
        });

        // 说话人下拉
        viewer.querySelectorAll('.sv-speaker-select').forEach(sel => {
            sel.addEventListener('change', () => {
                const si = parseInt(sel.dataset.scene);
                const bi = parseInt(sel.dataset.beat);
                if (data[si]?.['scene']?.[bi]) {
                    data[si]['scene'][bi].speaker = sel.value;
                }
            });
        });

        // 拍摄手法下拉
        viewer.querySelectorAll('.sv-shot-type').forEach(sel => {
            sel.addEventListener('change', () => {
                const si = parseInt(sel.dataset.scene), bi = parseInt(sel.dataset.beat);
                if (data[si]?.['scene']?.[bi]) data[si]['scene'][bi].shot_type = sel.value;
            });
        });

        // 动作角色下拉
        viewer.querySelectorAll('.sv-action-char').forEach(sel => {
            sel.addEventListener('change', () => {
                const si = parseInt(sel.dataset.scene);
                const bi = parseInt(sel.dataset.beat);
                const ai = parseInt(sel.dataset.ai);
                if (data[si]?.['scene']?.[bi]?.actions?.[ai]) {
                    data[si]['scene'][bi].actions[ai].character = sel.value;
                }
            });
        });

        // 动作名称下拉
        viewer.querySelectorAll('.sv-action-name').forEach(sel => {
            sel.addEventListener('change', () => {
                const si = parseInt(sel.dataset.scene);
                const bi = parseInt(sel.dataset.beat);
                const ai = parseInt(sel.dataset.ai);
                if (data[si]?.['scene']?.[bi]?.actions?.[ai]) {
                    const found = this._flatActions().find(a => a.trigger === sel.value);
                    data[si]['scene'][bi].actions[ai].action = sel.value;
                    if (found) data[si]['scene'][bi].actions[ai].state = found.state;
                }
            });
        });

        // 删除动作
        viewer.querySelectorAll('.sv-del-action').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.scene), bi = parseInt(btn.dataset.beat), ai = parseInt(btn.dataset.ai);
                data[si]?.['scene']?.[bi]?.actions?.splice(ai, 1);
                this._redrawScriptViewer();
            });
        });

        // 添加动作（默认：第一个角色 + 第一个动作）
        viewer.querySelectorAll('.sv-add-action').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.scene), bi = parseInt(btn.dataset.beat);
                if (!data[si]?.['scene']?.[bi]) return;
                const chars = this._getCharList(data);
                const flat  = this._flatActions();
                if (!data[si]['scene'][bi].actions) data[si]['scene'][bi].actions = [];
                data[si]['scene'][bi].actions.push({
                    character: chars[0] || '',
                    action: flat[0]?.trigger || '',
                    state: flat[0]?.state || 'standing',
                    motion_detail: '',
                });
                this._redrawScriptViewer();
            });
        });

        // 删除对话行
        viewer.querySelectorAll('.sv-del-beat').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.scene), bi = parseInt(btn.dataset.beat);
                data[si]?.['scene']?.splice(bi, 1);
                this._redrawScriptViewer();
            });
        });

        // 添加对话行（默认：第一个角色 + 第一个动作）
        viewer.querySelectorAll('.sv-add-beat').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.scene);
                const chars = this._getCharList(data);
                const flat  = this._flatActions();
                if (!data[si]['scene']) data[si]['scene'] = [];
                data[si]['scene'].push({
                    speaker: chars[0] || '',
                    content: '',
                    shot: 'character',
                    shot_type: '中景',
                    shot_blend: 'Cut',
                    Follow: 0,
                    shot_description: '',
                    actions: flat[0] ? [{
                        character: chars[0] || '',
                        action: flat[0].trigger,
                        state: flat[0].state || 'standing',
                        motion_detail: '',
                    }] : [],
                    'current position': [],
                });
                this._redrawScriptViewer();
            });
        });

        // 删除幕
        viewer.querySelectorAll('.sv-del-scene').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.scene);
                if (data.length <= 1) return;
                data.splice(si, 1);
                this._redrawScriptViewer();
            });
        });

        // 添加幕（默认：第一个场景 + 所有角色 + 第一条对话）
        viewer.querySelector('.sv-add-scene')?.addEventListener('click', () => {
            const chars     = this._getCharList(data);
            const sceneOpts = this._getSceneOpts();
            const flat      = this._flatActions();
            const defaultWhere = sceneOpts[0]?.id || '';
            data.push({
                'scene information': { who: chars, where: defaultWhere, what: '' },
                'initial position': chars.map((c, i) => ({ character: c, position: `Position ${i + 1}` })),
                'scene': [{
                    speaker: chars[0] || '',
                    content: '',
                    shot: 'character',
                    shot_type: '中景',
                    shot_blend: 'Cut',
                    Follow: 0,
                    shot_description: '',
                    actions: flat[0] ? [{
                        character: chars[0] || '',
                        action: flat[0].trigger,
                        state: flat[0].state || 'standing',
                        motion_detail: '',
                    }] : [],
                    'current position': chars.map((c, i) => ({ character: c, position: `Position ${i + 1}` })),
                }],
            });
            this._redrawScriptViewer();
            const scenes = viewer.querySelectorAll('.sv-scene');
            scenes[scenes.length - 1]?.scrollIntoView({ behavior: 'smooth' });
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
                    .map(p => {
                        const anchor = (APP_STATE.positionPlanMap || {})[p.position] || p.position || '';
                        return `${p.character || ''} → ${anchor}`;
                    })
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
                            .map(p => {
                                const anchor = (APP_STATE.positionPlanMap || {})[p.position] || p.position || '';
                                return `${p.character || ''}→${anchor}`;
                            })
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
                            .map(p => {
                                const anchor = (APP_STATE.positionPlanMap || {})[p.position] || p.position || '';
                                return `${p.character || ''}→${anchor}`;
                            })
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
        } else if (fmt === 'meeting') {
            const content = typeof data === 'string' ? data : JSON.stringify(data);
            wrap.className = 'log-entry output-block-entry ob-meeting-entry';
            wrap.innerHTML = `
                <div class="ob-meeting-row">
                    <span class="log-timestamp">[${timestamp}]</span>
                    <span class="ob-meeting-agent">${this._esc(agent)}</span>
                </div>
                <div class="ob-meeting-content">${this._esc(content)}</div>
            `;
            logContent.appendChild(wrap);
            logContent.scrollTop = logContent.scrollHeight;
            return;
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
    },

    // 显示/隐藏版本命名行
    showVersionLabelSection(show) {
        const el = document.getElementById('versionLabelSection');
        if (el) {
            el.style.display = show ? 'flex' : 'none';
            if (show) {
                const input = document.getElementById('versionLabelInput');
                if (input) input.value = '';
            }
        }
    },

    // 渲染历史记录面板
    renderHistoryPanel(sessions) {
        const list = document.getElementById('historyList');
        if (!list) return;

        if (!sessions || sessions.length === 0) {
            list.innerHTML = '<div class="history-empty">暂无历史记录</div>';
            return;
        }

        list.innerHTML = sessions.map(s => {
            const label = s.label || '（未命名）';
            const dt = s.created_at ? s.created_at.replace('T', ' ') : '';
            const scene = s.scene_id || '';
            const acts = s.act_count || '?';
            const files = s.files || {};
            const scriptFile = files.script || '';
            const wordFile = s.word_export || '';

            const loadBtn = scriptFile
                ? `<button class="history-action-btn" onclick="loadHistoryScript('${scriptFile}')">加载剧本</button>`
                : '';
            const wordBtn = scriptFile
                ? `<button class="history-action-btn" onclick="API.downloadWord('${scriptFile}')">下载 Word</button>`
                : '';

            return `
<div class="history-session-item">
  <div class="history-session-header">
    <span class="history-session-label" contenteditable="true"
          data-sid="${s.session_id}"
          onblur="saveHistoryLabel(this)">${this._esc(label)}</span>
    <span class="history-session-meta">${acts} 幕 · ${scene}</span>
  </div>
  <div class="history-session-time">${dt}</div>
  <div class="history-session-actions">${loadBtn}${wordBtn}</div>
</div>`;
        }).join('');
    }
};
