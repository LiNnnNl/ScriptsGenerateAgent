// API调用模块
const API = {
    // 获取所有画风
    async getStyles() {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.STYLES}`);
        return await response.json();
    },

    // 获取指定画风的场景
    async getScenes(styleTag) {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.SCENES}/${styleTag}`);
        return await response.json();
    },

    // 获取指定画风的角色
    async getCharacters(styleTag) {
        const response = await fetch(`${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.CHARACTERS}/${styleTag}`);
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

    // 下载文件
    downloadFile(filename) {
        window.location.href = `${API_CONFIG.BASE_URL}${API_CONFIG.ENDPOINTS.DOWNLOAD}/${filename}`;
    }
};
