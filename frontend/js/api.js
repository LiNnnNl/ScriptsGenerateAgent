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

    // 生成剧本（流式）
    async generateScript(data, onStream) {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.GENERATE}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        // 读取流式响应
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            
            if (done) break;
            
            buffer += decoder.decode(value, { stream: true });
            
            // 处理完整的JSON行
            const lines = buffer.split('\n');
            buffer = lines.pop();
            
            for (const line of lines) {
                if (line.trim()) {
                    try {
                        const data = JSON.parse(line);
                        onStream(data);
                    } catch (e) {
                        console.error('解析日志失败:', e, line);
                    }
                }
            }
        }

        // 处理剩余buffer
        if (buffer.trim()) {
            try {
                const data = JSON.parse(buffer);
                onStream(data);
            } catch (e) {
                console.error('解析最后的日志失败:', e, buffer);
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
    }
};
