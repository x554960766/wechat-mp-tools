/**
 * API 调用封装
 * 统一处理请求、错误和 loading 状态
 */
const API = {
    baseUrl: '',

    async request(url, options = {}) {
        const { method = 'GET', body = null, showError = true } = options;

        const fetchOptions = {
            method,
            headers: { 'Content-Type': 'application/json' },
        };

        if (body) {
            fetchOptions.body = JSON.stringify(body);
        }

        try {
            const resp = await fetch(`${this.baseUrl}${url}`, fetchOptions);
            const data = await resp.json();

            if (!resp.ok) {
                const errMsg = data.error || data.message || `HTTP ${resp.status}`;
                if (showError) Toast.error(errMsg);
                throw new Error(errMsg);
            }

            return data;
        } catch (err) {
            if (err.name === 'TypeError' && err.message.includes('fetch')) {
                if (showError) Toast.error('网络连接失败，请检查服务是否运行');
            }
            throw err;
        }
    },

    get(url, opts) {
        return this.request(url, { method: 'GET', ...opts });
    },

    post(url, body, opts) {
        return this.request(url, { method: 'POST', body, ...opts });
    },

    put(url, body, opts) {
        return this.request(url, { method: 'PUT', body, ...opts });
    },

    delete(url, opts) {
        return this.request(url, { method: 'DELETE', ...opts });
    },

    // ── Auth API ──────────────────────────────────────
    auth: {
        status() { return API.get('/api/auth/status', { showError: false }); },
        login() { return API.post('/api/auth/login'); },
        logout() { return API.post('/api/auth/logout'); },
        checkCredentials() { return API.get('/api/auth/check-credentials', { showError: false }); },
    },

    // ── Accounts API ─────────────────────────────────
    accounts: {
        list() { return API.get('/api/accounts'); },
        search(keyword) { return API.post('/api/accounts/search', { keyword }); },
        add(account) { return API.post('/api/accounts', account); },
        remove(fakeid) { return API.delete(`/api/accounts/${fakeid}`); },
        update(fakeid, data) { return API.put(`/api/accounts/${fakeid}`, data); },
    },

    // ── Articles API ─────────────────────────────────
    articles: {
        list(fakeid, begin = 0, count = 10, keyword = '') {
            const params = new URLSearchParams({ begin, count, keyword });
            return API.get(`/api/articles/list/${fakeid}?${params}`);
        },
        download(articles, accountName) {
            return API.post('/api/articles/download', { articles, account_name: accountName });
        },
        downloadRange(payload) {
            return API.post('/api/articles/download-range', payload);
        },
        cancelDownload(taskId) {
            return API.post(`/api/articles/download-cancel/${taskId}`);
        },
        downloadByUrl(urls) {
            return API.post('/api/articles/download-url', { urls });
        },
        downloadStatus(taskId) {
            return API.get(`/api/articles/download-status/${taskId}`, { showError: false });
        },
        history(limit = 50) {
            return API.get(`/api/articles/history?limit=${limit}`);
        },
        clearHistory() {
            return API.delete('/api/articles/history');
        },
        deleteHistory(index) {
            return API.delete(`/api/articles/history/${index}`);
        },
        openFolder() { return API.post('/api/articles/open-folder'); },
        openFile(path) { return API.post('/api/articles/open-file', { path }); },
        openParent(path) { return API.post('/api/articles/open-parent', { path }); },
    },

    // ── Proxy API ────────────────────────────────────
    proxy: {
        getConfig() { return API.get('/api/proxy/config'); },
        saveConfig(config) { return API.post('/api/proxy/config', config); },
        test(config) { return API.post('/api/proxy/test', config); },
        getPool() { return API.get('/api/proxy/pool'); },
        addToPool(proxy) { return API.post('/api/proxy/pool', proxy); },
        removeFromPool(index) { return API.delete(`/api/proxy/pool/${index}`); },
    },

    // ── Settings API ─────────────────────────────────
    settings: {
        get() { return API.get('/api/settings'); },
        save(settings) { return API.post('/api/settings', settings); },
    },

    // ── Douyin Downloader API ────────────────────────
    douyin: {
        auth: {
            start: () => API.post('/api/douyin/auth/start'),
            cancel: () => API.post('/api/douyin/auth/cancel'),
            status: () => API.get('/api/douyin/auth/status', { showError: false })
        },
        downloadSingle(url) { return API.post('/api/douyin/download-single', { url }); },
        downloadProfile(url, scroll_depth) { return API.post('/api/douyin/download-profile', { url, scroll_depth }); },
        cancelDownload() { return API.post('/api/douyin/cancel-download'); },
        progress() { return API.get('/api/douyin/progress', { showError: false }); },
        getHistory() { return API.get('/api/douyin/history'); },
        clearHistory() { return API.delete('/api/douyin/history'); },
        openFolder() { return API.post('/api/douyin/open-folder'); },
        openFile(path) { return API.post('/api/douyin/open-file', { path }); },
        openParent(path) { return API.post('/api/douyin/open-parent', { path }); },
    },

    // ── WeChat Channels API ──────────────────────────
    channels: {
        fetchVideoProfile(url) { return API.post('/api/channels/fetch_video_profile', { url }); },
        download(url, description, createtime) { return API.post('/api/channels/download', { url, description, createtime }); },
        openFolder() { return API.post('/api/channels/open-folder'); },
        startCookieAcquisition() { return API.post('/api/channels/start_cookie_acquisition'); },
        cookieAcquisitionStatus() { return API.get('/api/channels/cookie_acquisition_status', { showError: false }); },
    },
};
