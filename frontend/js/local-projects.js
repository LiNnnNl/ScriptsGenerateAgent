const LocalProjects = {
    FORMAT: 'scriptagent-project',
    SCHEMA_VERSION: 1,
    saveTimer: null,
    saving: false,
    dirty: false,

    init() {
        document.getElementById('localProjectOpenBtn')?.addEventListener('click', () => this.open());
        document.getElementById('localProjectSaveBtn')?.addEventListener('click', () => this.save());
        document.getElementById('localVersionBtn')?.addEventListener('click', () => this.toggleVersions());
        document.getElementById('localVersionCreateBtn')?.addEventListener('click', () => this.createVersion());

        const viewer = document.getElementById('scriptViewer');
        viewer?.addEventListener('input', () => this.markDirty());
        viewer?.addEventListener('change', () => this.markDirty());
        viewer?.addEventListener('click', (event) => {
            if (event.target.closest('.sv-del-scene,.sv-del-beat,.sv-add-scene,.sv-add-beat,.sv-del-action,.sv-add-action')) {
                setTimeout(() => this.markDirty(), 0);
            }
        });

        window.addEventListener('beforeunload', (event) => {
            if (!this.isDirty()) return;
            event.preventDefault();
            event.returnValue = '';
        });
    },

    async open() {
        try {
            let file;
            let openedHandle = null;
            if ('showOpenFilePicker' in window) {
                const [handle] = await window.showOpenFilePicker({
                    multiple: false,
                    types: [{ description: 'ScriptAgent 项目或剧本', accept: { 'application/json': ['.json'] } }]
                });
                file = await handle.getFile();
                openedHandle = handle;
            } else {
                file = await this._chooseFileFallback();
            }
            const parsed = JSON.parse(await file.text());
            const isRawScript = Array.isArray(parsed);
            const project = isRawScript ? this._projectFromScript(parsed, file.name) : parsed;
            if (!isRawScript) this._validate(project);

            // 普通剧本导入后必须“另存为项目”，绝不直接把原剧本覆盖成项目格式。
            APP_STATE.localProjectHandle = isRawScript ? null : openedHandle;
            APP_STATE.localProject = project;
            APP_STATE.currentScriptData = this._clone(project.screenplay.data);
            APP_STATE.currentScriptFilename = project.screenplay.filename || 'screenplay.json';
            APP_STATE.currentScriptTitle = project.project.title || null;
            APP_STATE.generatedCharacters = this._clone(project.characters || []);
            document.getElementById('scriptEditorPanel').style.display = 'block';
            UI.renderScriptViewer(APP_STATE.currentScriptData);
            this._setDirty(isRawScript);
            this._status(
                isRawScript ? `已导入剧本：${file.name} · 请保存为项目` : `已打开：${project.project.title}`,
                isRawScript ? 'dirty' : 'saved'
            );
            this.renderVersions();
        } catch (error) {
            if (error?.name !== 'AbortError') alert(`打开本地项目失败：${error.message}`);
        }
    },

    async save() {
        if (!APP_STATE.currentScriptData) {
            alert('请先生成或打开一个剧本。');
            return;
        }
        if (!APP_STATE.localProject) APP_STATE.localProject = this._newProject();
        this._syncFromState();

        if (!APP_STATE.localProjectHandle && 'showSaveFilePicker' in window) {
            try {
                APP_STATE.localProjectHandle = await window.showSaveFilePicker({
                    suggestedName: `${this._safeName(APP_STATE.localProject.project.title)}.scriptagent.json`,
                    types: [{ description: 'ScriptAgent 项目', accept: { 'application/json': ['.json'] } }]
                });
            } catch (error) {
                if (error?.name !== 'AbortError') alert(`选择保存位置失败：${error.message}`);
                return;
            }
        }
        await this._write(false);
    },

    markDirty() {
        if (!APP_STATE.currentScriptData) return;
        this._setDirty(true);
        this._status(APP_STATE.localProject ? '有未保存修改' : '尚未保存为本地项目', 'dirty');
        clearTimeout(this.saveTimer);
        if (APP_STATE.localProjectHandle) {
            this.saveTimer = setTimeout(() => this._autosave(), 1200);
        }
    },

    async _autosave() {
        if (!APP_STATE.localProject || !APP_STATE.localProjectHandle || this.saving) return;
        this._maybeAutoSnapshot();
        this._syncFromState();
        await this._write(true);
    },

    _maybeAutoSnapshot() {
        const project = APP_STATE.localProject;
        const versions = project?.versions || [];
        const lastAuto = versions.find(item => item.type === 'auto');
        const baseline = new Date(lastAuto?.createdAt || project.project.createdAt).getTime();
        if (Date.now() - baseline < 10 * 60 * 1000) return;
        if (JSON.stringify(project.screenplay.data) === JSON.stringify(APP_STATE.currentScriptData)) return;
        versions.unshift(this._snapshot('自动快照', 'auto'));
        let autoCount = 0;
        project.versions = versions.filter(item => item.type !== 'auto' || ++autoCount <= 30);
    },

    async _write(isAuto) {
        if (this.saving) return;
        this.saving = true;
        this._status(isAuto ? '正在自动保存…' : '正在保存…', 'saving');
        try {
            const text = JSON.stringify(APP_STATE.localProject, null, 2);
            if (APP_STATE.localProjectHandle) {
                const writable = await APP_STATE.localProjectHandle.createWritable();
                await writable.write(text);
                await writable.close();
            } else {
                this._download(text, `${this._safeName(APP_STATE.localProject.project.title)}.scriptagent.json`);
            }
            this._setDirty(false);
            this._status(`已保存 · ${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`, 'saved');
        } catch (error) {
            this._setDirty(true);
            this._status('保存失败', 'error');
            if (!isAuto) alert(`保存本地项目失败：${error.message}`);
        } finally {
            this.saving = false;
        }
    },

    toggleVersions() {
        if (!APP_STATE.currentScriptData) {
            alert('请先生成或打开一个剧本。');
            return;
        }
        const panel = document.getElementById('localVersionPanel');
        const opening = panel.style.display === 'none';
        panel.style.display = opening ? 'block' : 'none';
        if (opening) this.renderVersions();
    },

    async createVersion() {
        if (!APP_STATE.currentScriptData) return;
        if (!APP_STATE.localProject) APP_STATE.localProject = this._newProject();
        const input = document.getElementById('localVersionName');
        const name = (input.value || '').trim();
        if (!name) {
            input.focus();
            return;
        }
        this._syncFromState();
        APP_STATE.localProject.versions.unshift(this._snapshot(name, 'manual'));
        input.value = '';
        this.renderVersions();
        this._setDirty(true);
        await this.save();
    },

    async restoreVersion(id) {
        const project = APP_STATE.localProject;
        const version = project?.versions?.find(item => item.id === id);
        if (!version || !confirm(`恢复版本“${version.name}”？当前内容会先保存为恢复前快照。`)) return;
        this._syncFromState();
        project.versions.unshift(this._snapshot('恢复前自动快照', 'auto'));
        APP_STATE.currentScriptData = this._clone(version.screenplay);
        project.screenplay.data = this._clone(version.screenplay);
        UI.renderScriptViewer(APP_STATE.currentScriptData);
        this.renderVersions();
        this._setDirty(true);
        await this.save();
    },

    deleteVersion(id) {
        const project = APP_STATE.localProject;
        const version = project?.versions?.find(item => item.id === id);
        if (!version || !confirm(`删除版本“${version.name}”？`)) return;
        project.versions = project.versions.filter(item => item.id !== id);
        this.renderVersions();
        this.markDirty();
    },

    renderVersions() {
        const list = document.getElementById('localVersionList');
        const versions = APP_STATE.localProject?.versions || [];
        if (!versions.length) {
            list.innerHTML = '<div class="history-empty">暂无本地版本。输入名称即可保存当前快照。</div>';
            return;
        }
        list.innerHTML = versions.map(version => `
            <div class="local-version-item">
                <div>
                    <strong>${this._escape(version.name)}</strong>
                    <span class="local-version-type">${version.type === 'auto' ? '自动' : '命名版本'}</span>
                    <div class="local-version-time">${new Date(version.createdAt).toLocaleString('zh-CN')}</div>
                </div>
                <div class="local-version-actions">
                    <button data-restore-version="${version.id}">恢复</button>
                    <button data-delete-version="${version.id}">删除</button>
                </div>
            </div>`).join('');
        list.querySelectorAll('[data-restore-version]').forEach(button => {
            button.addEventListener('click', () => this.restoreVersion(button.dataset.restoreVersion));
        });
        list.querySelectorAll('[data-delete-version]').forEach(button => {
            button.addEventListener('click', () => this.deleteVersion(button.dataset.deleteVersion));
        });
    },

    _newProject() {
        const now = new Date().toISOString();
        const base = APP_STATE.currentScriptTitle || (APP_STATE.currentScriptFilename || '未命名剧本').replace(/\.json$/i, '');
        return {
            format: this.FORMAT,
            schemaVersion: this.SCHEMA_VERSION,
            project: { id: crypto.randomUUID(), title: base, createdAt: now, updatedAt: now },
            screenplay: { filename: APP_STATE.currentScriptFilename || 'screenplay.json', data: this._clone(APP_STATE.currentScriptData) },
            characters: this._clone(APP_STATE.generatedCharacters || []),
            config: {},
            versions: []
        };
    },

    _projectFromScript(script, filename) {
        const now = new Date().toISOString();
        const title = (filename || '未命名剧本').replace(/\.json$/i, '') || '未命名剧本';
        return {
            format: this.FORMAT,
            schemaVersion: this.SCHEMA_VERSION,
            project: { id: crypto.randomUUID(), title, createdAt: now, updatedAt: now },
            screenplay: { filename: filename || 'screenplay.json', data: this._clone(script) },
            characters: [],
            config: {},
            versions: []
        };
    },

    _syncFromState() {
        const project = APP_STATE.localProject;
        project.project.updatedAt = new Date().toISOString();
        project.screenplay.filename = APP_STATE.currentScriptFilename || project.screenplay.filename || 'screenplay.json';
        project.screenplay.data = this._clone(APP_STATE.currentScriptData);
        project.characters = this._clone(APP_STATE.generatedCharacters || []);
        project.config = {
            scenePool: [...(APP_STATE.scenePool || [])],
            actScenes: [...(APP_STATE.actScenes || [])],
            actCount: APP_STATE.actCount,
            scriptStyleId: APP_STATE.scriptStyleId || '',
            scriptToneId: APP_STATE.scriptToneId || '',
            creativeIdea: document.getElementById('creativeIdea')?.value || ''
        };
    },

    _snapshot(name, type) {
        return {
            id: crypto.randomUUID(),
            name,
            type,
            createdAt: new Date().toISOString(),
            screenplay: this._clone(APP_STATE.currentScriptData)
        };
    },

    _validate(project) {
        if (project?.format !== this.FORMAT || project?.schemaVersion !== this.SCHEMA_VERSION) {
            throw new Error('文件既不是 ScriptAgent 项目，也不是剧本 JSON 数组');
        }
        if (!Array.isArray(project?.screenplay?.data)) throw new Error('项目中的剧本数据无效');
        if (!Array.isArray(project.versions)) project.versions = [];
    },

    _chooseFileFallback() {
        return new Promise((resolve, reject) => {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = '.json,.scriptagent.json';
            input.addEventListener('change', () => input.files[0] ? resolve(input.files[0]) : reject(new DOMException('取消', 'AbortError')), { once: true });
            input.click();
        });
    },

    _download(text, filename) {
        const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.click();
        URL.revokeObjectURL(url);
    },

    _setDirty(value) {
        this.dirty = value;
    },
    isDirty() { return this.dirty; },
    _clone(value) { return JSON.parse(JSON.stringify(value)); },
    _safeName(value) { return (value || '未命名项目').replace(/[\\/:*?"<>|]/g, '_'); },
    _escape(value) {
        const div = document.createElement('div');
        div.textContent = value || '';
        return div.innerHTML;
    },
    _status(text, state) {
        const el = document.getElementById('localSaveStatus');
        if (!el) return;
        el.textContent = text;
        el.dataset.state = state;
    }
};
