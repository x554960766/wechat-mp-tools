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
                if (data && (data.need_verify || data.waterwall)) {
                    if (window.WaterWall && typeof WaterWall.handleDetection === 'function') {
                        WaterWall.handleDetection(data.waterwall || data);
                    }
                }
                if (showError) Toast.error(errMsg);
                // 保留结构化错误码，便于调用方区分「未登录」「风控」等可引导的场景
                const error = new Error(errMsg);
                error.code = data.code || '';
                error.status = resp.status;
                error.data = data;
                throw error;
            }

            if (data && (data.need_verify || (data.waterwall && data.waterwall.active))) {
                if (window.WaterWall && typeof WaterWall.handleDetection === 'function') {
                    WaterWall.handleDetection(data.waterwall || data);
                }
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
        list()                     { return API.get('/api/account-pool'); },
        summary()                  { return API.get('/api/account-pool/summary', { showError: false }); },
        remove(id)                 { return API.delete(`/api/account-pool/${id}`); },
        events()                   { return API.get('/api/account-pool/events', { showError: false }); },
        getAutoRefreshConfig()     { return API.get('/api/account-pool/auto-refresh-config', { showError: false }); },
        toggleAutoRefresh(enabled) { return API.post('/api/account-pool/auto-refresh-toggle', { enabled }); },
        syncManual()               { return API.post('/api/account-pool/sync-manual'); },
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
        rssUploadLog(account, limit = 30) {
            const qs = new URLSearchParams({ limit });
            if (account) qs.set('account', account);
            return API.get(`/api/accounts/rss-upload-log?${qs.toString()}`);
        },
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
        collectLatest(payload) {
            return API.post('/api/articles/collect-latest', payload);
        },
        collectAll(payload = {}) {
            return API.post('/api/articles/collect-all', payload);
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
        openFile(path, type = null) { return API.post('/api/articles/open-file', { path, type }); },
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

    // ── Kuaishou Downloader API ──────────────────────
    kuaishou: {
        auth: {
            start: () => API.post('/api/kuaishou/auth/start'),
            cancel: () => API.post('/api/kuaishou/auth/cancel'),
            status: () => API.get('/api/kuaishou/auth/status', { showError: false })
        },
        parseSingle(url) { return API.post('/api/kuaishou/parse-single', { url }); },
        downloadSingle(payload) {
            if (typeof payload === 'string') return API.post('/api/kuaishou/download-single', { url: payload });
            return API.post('/api/kuaishou/download-single', { item: payload });
        },
        userFeed(url, pcursor) { return API.post('/api/kuaishou/user-feed', { url, pcursor }); },
        downloadSelected(items) { return API.post('/api/kuaishou/download-selected', { items }); },
        downloadProfile(url, max_pages) { return API.post('/api/kuaishou/download-profile', { url, max_pages }); },
        cancelDownload() { return API.post('/api/kuaishou/cancel-download'); },
        progress() { return API.get('/api/kuaishou/progress', { showError: false }); },
        getHistory() { return API.get('/api/kuaishou/history'); },
        clearHistory() { return API.delete('/api/kuaishou/history'); },
        openFolder() { return API.post('/api/kuaishou/open-folder'); },
        openAuthorFolder(author) { return API.post('/api/kuaishou/open-author-folder', { author }); },
        openFile(path) { return API.post('/api/kuaishou/open-file', { path }); },
        openParent(path) { return API.post('/api/kuaishou/open-parent', { path }); },
        deleteHistoryItem(index, path) { return API.delete('/api/kuaishou/history/item', { body: { index, path } }); },
        // 作者管理
        listAccounts() { return API.get('/api/kuaishou/accounts'); },
        addAccount(account) { return API.post('/api/kuaishou/accounts', account); },
        removeAccount(userId) { return API.delete(`/api/kuaishou/accounts/${encodeURIComponent(userId)}`); },
        parseAccount(url) { return API.post('/api/kuaishou/accounts/parse', { url }); },
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
        downloadFFmpeg() { return API.post('/api/transcode/download-ffmpeg'); },
        downloadFFmpegStatus() { return API.get('/api/transcode/download-ffmpeg-status', { showError: false }); },
        scanDownloads() { return API.get('/api/transcode/scan-downloads'); },
        videoInfo(path) { return API.post('/api/transcode/video-info', { path }); },
        start(inputPath, params) {
            if (typeof inputPath === 'object' && inputPath !== null && !params) {
                return API.post('/api/transcode/start', inputPath);
            }
            return API.post('/api/transcode/start', { input_path: inputPath, params });
        },
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
    },

    // ── XiaoHongShu API ──────────────────────────────
    xhs: {
        auth: {
            status()        { return API.get('/api/xhs-auth/status', { showError: false }); },
            login()         { return API.post('/api/xhs-auth/login'); },
            cancel()        { return API.post('/api/xhs-auth/cancel'); },
            saveCookie(c)   { return API.post('/api/xhs-auth/save-cookie', { cookie: c }); },
            logout()        { return API.post('/api/xhs-auth/logout'); },
        },
        parse(url, opts)            { return API.post('/api/xhs/parse', { url }, opts); },
        download(urls, opts)        { return API.post('/api/xhs/download', { urls }, opts); },
        listAccounts()              { return API.get('/api/xhs/accounts'); },
        parseUser(url)              { return API.post('/api/xhs/accounts/parse', { url }); },
        addAccount(user)            { return API.post('/api/xhs/accounts', user); },
        removeAccount(userId)       { return API.delete(`/api/xhs/accounts/${userId}`); },
        listNotes(userId)           { return API.get(`/api/xhs/accounts/${userId}/notes`); },
        loadMoreNotes(userId, loaded) { return API.post(`/api/xhs/accounts/${userId}/notes/more`, { loaded }); },
        downloadNotes(notes, name)  { return API.post('/api/xhs/download-notes', { notes, account_name: name }); },
        downloadStatus(taskId)      { return API.get(`/api/xhs/download-status/${taskId}`, { showError: false }); },
        cancelDownload(taskId)      { return API.post(`/api/xhs/download-cancel/${taskId}`); },
        getHistory(limit = 100)     { return API.get(`/api/xhs/history?limit=${limit}`); },
        clearHistory()              { return API.delete('/api/xhs/history'); },
        deleteHistory(index)        { return API.delete(`/api/xhs/history/${index}`); },
        openFolder(account = '')    { return API.post('/api/xhs/open-folder', { account }); },
        openFile(path)              { return API.post('/api/xhs/open-file', { path }); },
        openParent(path)            { return API.post('/api/xhs/open-parent', { path }); },
    },

    // ── Media Service API（经宿主 /media 反代到 ai-media）──
    // 所有路径都带 /media 前缀：宿主 Flask 只转发该前缀，媒体服务自身不认识它。
    media: {
        health() { return API.get('/media/api/health', { showError: false }); },
        roots() { return API.get('/media/api/media/roots', { showError: false }); },
        stats() { return API.get('/media/api/search/stats', { showError: false }); },
        searchText(payload, opts) { return API.post('/media/api/search/text', payload, opts); },
        scan(root = 'articles-full', rebuild = false) {
            return API.post('/media/api/media/scan', { root, rebuild });
        },
        scanStatus(jobId) { return API.get(`/media/api/media/scans/${jobId}`, { showError: false }); },
        cancelScan(jobId) { return API.post(`/media/api/media/scans/${jobId}/cancel`); },
        archiveJobs(limit = 10) { return API.get(`/media/api/archive/jobs?limit=${limit}`, { showError: false }); },
        archiveStatus(jobId) { return API.get(`/media/api/archive/jobs/${jobId}`, { showError: false }); },
        archiveFromMp(payload) { return API.post('/media/api/archive/from-mp', payload); },
        // 索引返回的 thumb/file 是媒体服务内部路径，经宿主必须补 /media 前缀
        thumb(item) { return '/media' + item.thumb; },
        original(item) { return '/media' + item.file; },
        display(item) { return '/media' + item.file.split('&display=1')[0]; },
    },

    // ── Version Update API ──────────────────────────────
    version: {
        check()         { return API.get('/api/version/check', { showError: false }); },
        download(url)   { return API.post('/api/version/download', { url }); },
        progress()      { return API.get('/api/version/download-progress', { showError: false }); },
        openFolder()    { return API.post('/api/version/open-update-folder'); },
    },

    // ── Bilibili Downloader API ──────────────────────────
    bili: {
        auth: {
            status()            { return API.get('/api/bilibili-auth/status', { showError: false }); },
            generateQrcode()    { return API.get('/api/bilibili-auth/qrcode/generate'); },
            pollQrcode(key)     { return API.post('/api/bilibili-auth/qrcode/poll', { qrcode_key: key }); },
            saveCookie(c)       { return API.post('/api/bilibili-auth/save-cookie', { cookie: c }); },
            logout()            { return API.post('/api/bilibili-auth/logout'); },
        },
        detectUrl(url)          { return API.post('/api/bilibili/detect-url', { url }); },
        downloadSingle(url, p, quality)  { return API.post('/api/bilibili/download-single', { url, pages: p, quality }); },
        listAccounts()          { return API.get('/api/bilibili/accounts'); },
        parseUser(url)          { return API.post('/api/bilibili/accounts/parse', { url }); },
        addAccount(user)        { return API.post('/api/bilibili/accounts', user); },
        removeAccount(mid)      { return API.delete(`/api/bilibili/accounts/${mid}`); },
        listVideos(mid, page=1) { return API.get(`/api/bilibili/accounts/${mid}/videos?page=${page}`); },
        downloadBatch(items, quality)    { return API.post('/api/bilibili/download-batch', { items, quality }); },
        progress()              { return API.get('/api/bilibili/progress', { showError: false }); },
        cancelDownload()        { return API.post('/api/bilibili/cancel-download'); },
        getHistory()            { return API.get('/api/bilibili/history'); },
        clearHistory()          { return API.delete('/api/bilibili/history'); },
        deleteHistory(index)    { return API.delete(`/api/bilibili/history/${index}`); },
        openFolder()            { return API.post('/api/bilibili/open-folder'); },
        openFile(path)          { return API.post('/api/bilibili/open-file', { path }); },
        openParent(path)        { return API.post('/api/bilibili/open-parent', { path }); },
    }
};
