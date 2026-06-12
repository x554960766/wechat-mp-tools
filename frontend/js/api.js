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
            const text = await resp.text();
            let data = {};
            let isJson = true;
            try {
                data = JSON.parse(text);
            } catch (jsonErr) {
                isJson = false;
                data = { error: `服务响应解析失败 (HTTP ${resp.status})` };
            }

            if (!resp.ok || !isJson) {
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
        cancel() { return API.post('/api/auth/cancel'); },
        checkCredentials() { return API.get('/api/auth/check-credentials', { showError: false }); },
    },

    // ── Account Pool API ─────────────────────────────
    accountPool: {
        list()         { return API.get('/api/account-pool'); },
        summary()      { return API.get('/api/account-pool/summary', { showError: false }); },
        remove(id)     { return API.delete(`/api/account-pool/${id}`); },
        events()       { return API.get('/api/account-pool/events', { showError: false }); },
    },

    // ── Accounts API ─────────────────────────────────
    accounts: {
        list() { return API.get('/api/accounts'); },
        search(keyword) { return API.post('/api/accounts/search', { keyword }); },
        add(account) { return API.post('/api/accounts', account); },
        remove(fakeid) { return API.delete(`/api/accounts/${fakeid}`); },
        update(fakeid, data) { return API.put(`/api/accounts/${fakeid}`, data); },
        rssSubscribe(fakeid, interval_minutes = 60) {
            return API.post(`/api/accounts/${fakeid}/rss-subscribe`, { interval_minutes });
        },
        rssUnsubscribe(fakeid) {
            return API.delete(`/api/accounts/${fakeid}/rss-subscribe`);
        },
        rssSubscriptions() { return API.get('/api/accounts/rss-subscriptions'); },
        rssForceUpload(fakeid) { return API.post(`/api/accounts/${fakeid}/rss-force-upload`); },
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
        openFolder(account = '') { return API.post('/api/articles/open-folder', { account }); },
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
        download(url, description, createtime, decryptKey = null) { return API.post('/api/channels/download', { url, description, createtime, decrypt_key: decryptKey }); },
        downloadAsync(url, description, createtime, decryptKey = null) { return API.post('/api/channels/download/start', { url, description, createtime, decrypt_key: decryptKey }); },
        getDownloadStatus(taskId) { return API.get(`/api/channels/download/status/${taskId}`, { showError: false }); },
        cancelDownload(taskId) { return API.post(`/api/channels/download/cancel/${taskId}`); },
        openFolder() { return API.post('/api/channels/open-folder'); },
        startCookieAcquisition() { return API.post('/api/channels/start_cookie_acquisition'); },
        cookieAcquisitionStatus() { return API.get('/api/channels/cookie_acquisition_status', { showError: false }); },
        getHistory() { return API.get('/api/channels/history'); },
        clearHistory() { return API.delete('/api/channels/history'); },
        openFile(path) { return API.post('/api/channels/open-file', { path }); },
        openParent(path) { return API.post('/api/channels/open-parent', { path }); },
        getFavorites() { return API.get('/api/channels/favorites'); },
        addFavorite(author) { return API.post('/api/channels/favorites', author); },
        removeFavorite(username) { return API.delete(`/api/channels/favorites/${username}`); },
        getAuthorVideos(username) { return API.get(`/api/channels/author-videos/${username}`); },
        addAuthorVideo(username, feed) { return API.post(`/api/channels/author-videos/${username}`, feed); },
        getProxyStatus() { return API.get('/api/channels/proxy/status'); },
        startProxy() { return API.post('/api/channels/proxy/start'); },
        stopProxy() { return API.post('/api/channels/proxy/stop'); },
        installCert() { return API.post('/api/channels/proxy/install-cert'); },
        uninstallCert() { return API.post('/api/channels/proxy/uninstall-cert'); },
        clearCache() { return API.post('/api/channels/clear-cache'); },
    },

    // ── Video Transcoder API ─────────────────────────
    transcode: {
        checkFFmpeg() { return API.get('/api/transcode/check-ffmpeg', { showError: false }); },
        scanDownloads() { return API.get('/api/transcode/scan-downloads'); },
        videoInfo(path) { return API.post('/api/transcode/video-info', { path }); },
        start(inputPath, params) { return API.post('/api/transcode/start', { input_path: inputPath, params }); },
        status() { return API.get('/api/transcode/status', { showError: false }); },
        clearCompleted() { return API.post('/api/transcode/clear-completed'); },
        openParent(path) { return API.post('/api/transcode/open-parent', { path }); },
        async upload(formData, onProgress) {
            // 自定义上传逻辑以支持大文件进度汇报 (虽然 localhost 瞬间完成)
            try {
                const resp = await fetch('/api/transcode/upload', {
                    method: 'POST',
                    body: formData
                });
                const text = await resp.text();
                let data = {};
                let isJson = true;
                try {
                    data = JSON.parse(text);
                } catch (e) {
                    isJson = false;
                    throw new Error(`服务响应解析失败 (HTTP ${resp.status})`);
                }
                if (!resp.ok || !isJson) {
                    throw new Error(data.error || '上传文件失败');
                }
                return data;
            } catch (err) {
                Toast.error(err.message || '上传视频失败');
                throw err;
            }
        }
    }
};
