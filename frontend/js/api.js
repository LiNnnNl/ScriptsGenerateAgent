// API调用模块
const API = {
    // 获取所有场景
    async getScenes() {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.SCENES}`);
        return await response.json();
    },

    // 获取角色库
    async getCharacters() {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.CHARACTERS}`);
        return await response.json();
    },

    // 永久添加角色到角色库
    async addCharacter(charData) {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.CHARACTERS}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(charData)
        });
        return await response.json();
    },

    // 生成角色档案
    async generateCharacters(data) {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.GENERATE_CHARACTERS}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        return await response.json();
    },

    // 稳定任务编号提交，断线只重连查询，不重复生成。
    async generateScript(data, onStream) {
        if (localStorage.getItem('scriptGenerationJob')) {
            throw new Error('有尚未收取结果的任务，请刷新页面恢复进度。');
        }
        const pending = {job_id: crypto.randomUUID(), params: data};
        localStorage.setItem('scriptGenerationJob', JSON.stringify(pending));
        return this.followGeneration(pending, onStream);
    },

    async followGeneration(pending, onStream) {
        const endpoint = `${API_CONFIG.BASE_URL}/api/generation_jobs`;
        const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
        let accepted = false, cursor = 0, failures = 0, terminalSeen = false;
        while (true) {
            let state;
            try {
                const response = await fetch(accepted ? `${endpoint}/${encodeURIComponent(pending.job_id)}?cursor=${cursor}` : endpoint,
                    accepted ? {signal: AbortSignal.timeout(20000)} : {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(pending), signal: AbortSignal.timeout(20000)
                    });
                state = await response.json();
                if (!response.ok) {
                    if ([400, 404, 409].includes(response.status)) {
                        localStorage.removeItem('scriptGenerationJob');
                        const error = new Error(state.error || '任务请求未通过');
                        error.definitive = true;
                        throw error;
                    }
                    throw new Error(state.error || `服务暂时不可用（${response.status}）`);
                }
                accepted = true;
                failures = 0;
            } catch (error) {
                if (error.definitive) throw error;
                failures += 1;
                if (failures === 1) onStream({type: 'log', level: 'warning', message: '连接暂时中断，正在恢复任务进度；服务器任务不会因页面断线取消。'});
                if (failures >= 12) throw new Error('暂时无法连接服务器。任务编号已保存，请稍后刷新页面恢复查询。');
                await pause(Math.min(1000 * 2 ** failures, 15000));
                continue;
            }
            for (const event of state.events || []) {
                onStream(event);
                if (['success', 'error'].includes(event.type)) terminalSeen = true;
            }
            cursor = state.cursor;
            if (state.state !== 'running' && !state.has_more) {
                localStorage.removeItem('scriptGenerationJob');
                if (!terminalSeen) onStream(state.result || {type: 'error', message: state.error || '任务未返回结果'});
                return;
            }
            await pause(state.has_more ? 0 : 2500);
        }
    },

    // 导演 Word 模式（只调用 DirectorAgent，流式返回）
    async generateDirectorWord(data, onStream) {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.GENERATE_DIRECTOR_WORD}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (line.trim()) {
                    try {
                        onStream(JSON.parse(line));
                    } catch (e) {
                        console.error('解析导演 Word 日志失败:', e, line);
                    }
                }
            }
        }

        if (buffer.trim()) {
            try {
                onStream(JSON.parse(buffer));
            } catch (e) {
                console.error('解析导演 Word 最后日志失败:', e, buffer);
            }
        }
    },

    // 获取剧本内容（供编辑器加载）
    async getScriptContent(filename) {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.SCRIPT_CONTENT}/${filename}`);
        return await response.json();
    },

    // 下载文件
    downloadFile(filename) {
        window.location.href = `${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.DOWNLOAD}/${filename}`;
    },

    // 下载 Word 版剧本
    downloadWord(filename) {
        window.location.href = `${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.DOWNLOAD_WORD}/${filename}`;
    },

    // 打包下载当前会话所有输出文件
    downloadSessionZip(sessionId) {
        window.location.href = `${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.DOWNLOAD_SESSION}/${sessionId}`;
    },

    // 获取 position_plan（供剧本编辑器做 Position N → 锚点名映射）
    async getPositionPlan(sessionId) {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.POSITION_PLAN}/${sessionId}`);
        return await response.json();
    },

    // 获取历史生成记录
    async getHistory() {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.HISTORY}`);
        return await response.json();
    },

    // 获取拍摄手法枚举
    async getShotTypes() {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.SHOT_TYPES}`);
        return await response.json();
    },

    // 获取动作库
    async getActions() {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.ACTIONS}`);
        return await response.json();
    },

    // 更新历史会话标签
    async updateHistoryLabel(sessionId, label) {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.HISTORY}/${sessionId}/label`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label })
        });
        return await response.json();
    }
};
