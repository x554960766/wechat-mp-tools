/**
 * API 调用封装
 * 统一处理请求、错误和 loading 状态
 */
const API = {
    baseUrl: '',

    async request(url, options = {}) {
        const { method = 'GET', body = null, showError = true } = options;

        // FormData 必须让浏览器自动补 multipart 的 Content-Type（含 boundary）：
        // 手工写 application/json，服务端收到的既不是合法 JSON 也不是合法 multipart，
        // 以图搜图这类文件上传会直接 422/400。
        const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
        const fetchOptions = isFormData
            ? { method }
            : { method, headers: { 'Content-Type': 'application/json' } };

        if (isFormData) {
            fetchOptions.body = body;
        } else if (body) {
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
                // 媒体服务的错误是 {"error":{"code","message"}}，直接拿 data.error 会把对象塞进
                // new Error()，Toast 里就变成 [object Object]。这里先摊平成字符串。
                const nested = data && data.error;
                const errMsg = (typeof nested === 'string' && nested)
                    || (nested && typeof nested === 'object' && (nested.message || nested.code))
                    || data.message
                    || `HTTP ${resp.status}`;
                if (data && (data.need_verify || data.waterwall)) {
                    if (window.WaterWall && typeof WaterWall.handleDetection === 'function') {
                        WaterWall.handleDetection(data.waterwall || data);
                    }
                }
                if (showError) Toast.error(errMsg);
                // 保留结构化错误码，便于调用方区分「未登录」「风控」等可引导的场景
                const error = new Error(errMsg);
                error.code = (nested && typeof nested === 'object' && nested.code) || data.code || '';
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

    // ── 按号归档 API（宿主侧跨平台适配层）─────────────
    archive: {
        platforms() { return API.get('/api/archive/platforms', { showError: false }); },
        accounts(platform) {
            return API.get(`/api/archive/accounts?platform=${encodeURIComponent(platform)}`);
        },
        start(payload) { return API.post('/api/archive/start', payload); },
        status(platform, taskId) {
            const qs = new URLSearchParams({ platform, task_id: taskId });
            return API.get(`/api/archive/status?${qs.toString()}`, { showError: false });
        },
        tasks(platform) {
            const qs = platform ? `?platform=${encodeURIComponent(platform)}` : '';
            return API.get(`/api/archive/tasks${qs}`, { showError: false });
        },
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
        /**
         * 以图搜图。body 必须是 FormData（file + roots[] + k/after/before/review/videos），
         * 后端走 multipart，见 API.request 对 FormData 的分支处理。
         */
        searchImage(formData, opts) { return API.post('/media/api/search/image', formData, opts); },
        // 两级 NSFW：flagged 列出仍判疑似敏感的图片，review 写人工复核结论（second 级）
        nsfwFlagged(params = {}, opts) {
            const query = new URLSearchParams();
            Object.entries(params).forEach(([key, value]) => {
                if (value !== undefined && value !== null && value !== '') query.set(key, value);
            });
            const qs = query.toString();
            return API.get(`/media/api/nsfw/flagged${qs ? '?' + qs : ''}`, opts);
        },
        nsfwReview(payload) { return API.post('/media/api/nsfw/review', payload); },
        // 全局检测：status 给覆盖率与模型可用性；scan 入队，按 job 轮询
        nsfwStatus(opts) { return API.get('/media/api/nsfw/status', opts); },
        nsfwScan(payload) { return API.post('/media/api/nsfw/scan', payload); },
        nsfwScanStatus(jobId) { return API.get(`/media/api/nsfw/scans/${jobId}`, { showError: false }); },
        cancelNsfwScan(jobId) { return API.post(`/media/api/nsfw/scans/${jobId}/cancel`); },
        // 配置面：阈值/开关/鉴黄方案。改阈值可 SQL 重算历史结论，换方案必须重扫
        nsfwSettings(opts) { return API.get('/media/api/settings/nsfw', opts); },
        nsfwSettingsSave(payload) { return API.put('/media/api/settings/nsfw', payload); },
        nsfwRecompute() { return API.post('/media/api/nsfw/recompute'); },
        // 标签：文件标签挂在内容摘要上（同内容副本共享），作品标签挂在 work_key 上
        tags(opts) { return API.get('/media/api/tags', opts); },
        imagesByTag(tag, params = {}, opts) {
            const query = new URLSearchParams({ tag });
            Object.entries(params).forEach(([key, value]) => {
                if (value !== undefined && value !== null && value !== '') query.set(key, value);
            });
            return API.get(`/media/api/tags/images?${query.toString()}`, opts);
        },
        contentsByTag(tag, params = {}, opts) {
            const query = new URLSearchParams({ tag });
            Object.entries(params).forEach(([key, value]) => {
                if (value !== undefined && value !== null && value !== '') query.set(key, value);
            });
            return API.get(`/media/api/tags/contents?${query.toString()}`, opts);
        },
        imageTags(root, relpath, opts) {
            const query = new URLSearchParams({ root, relpath });
            return API.get(`/media/api/tags/image?${query.toString()}`, opts);
        },
        tagImage(payload) { return API.post('/media/api/tags/image', payload); },
        contentTags(contentId, opts) {
            return API.get(`/media/api/tags/content?content_id=${encodeURIComponent(contentId)}`, opts);
        },
        tagContent(payload) { return API.post('/media/api/tags/content', payload); },
        renameTag(oldTag, newTag, opts) { return API.post('/media/api/tags/rename', { old: oldTag, new: newTag }, opts); },
        contentsSearch(params, opts) {
            const query = new URLSearchParams();
            Object.entries(params || {}).forEach(([key, value]) => {
                if (value !== undefined && value !== null && value !== '') query.set(key, value);
            });
            return API.get(`/media/api/contents/search?${query.toString()}`, opts);
        },
        contentsFacets() { return API.get('/media/api/contents/facets', { showError: false }); },
        contentsDetail(contentId) { return API.get(`/media/api/contents/${encodeURIComponent(contentId)}`); },
        scan(root = 'articles-full', rebuild = false) {
            return API.post('/media/api/media/scan', { root, rebuild });
        },
        scanStatus(jobId) { return API.get(`/media/api/media/scans/${jobId}`, { showError: false }); },
        cancelScan(jobId) { return API.post(`/media/api/media/scans/${jobId}/cancel`); },
        archiveJobs(limit = 10) { return API.get(`/media/api/archive/jobs?limit=${limit}`, { showError: false }); },
        archiveStatus(jobId) { return API.get(`/media/api/archive/jobs/${jobId}`, { showError: false }); },
        archiveFromMp(payload) { return API.post('/media/api/archive/from-mp', payload); },
        // 归档任务（/api/archive/jobs）：建任务、上传 txt/csv/zip、验证窗口、导出与恢复
        archiveCreate(payload) { return API.post('/media/api/archive/jobs', payload); },
        /**
         * 上传 txt/csv/zip 建任务。body 必须是 FormData（file + target_root + target_dir），
         * 后端收 multipart，见 API.request 的 FormData 分支。
         */
        archiveUpload(formData) { return API.post('/media/api/archive/jobs/upload', formData); },
        archiveVerify(jobId, articleIndex) {
            const qs = articleIndex === undefined || articleIndex === null
                ? ''
                : `?article_index=${encodeURIComponent(articleIndex)}`;
            return API.post(`/media/api/archive/jobs/${encodeURIComponent(jobId)}/verify${qs}`);
        },
        archiveRetry(jobId) { return API.post(`/media/api/archive/jobs/${encodeURIComponent(jobId)}/retry`); },
        archiveExportStatus(exportId) {
            return API.get(`/media/api/archive/exports/${encodeURIComponent(exportId)}`, { showError: false });
        },
        // 导出 ZIP：就绪直接回文件，未就绪回 202 + export_id，所以调用方自己 fetch 判 content-type
        archiveDownloadUrl(jobId) { return `/media/api/archive/jobs/${encodeURIComponent(jobId)}/download`; },
        // NAS 自动归档是宿主侧队列（backend/nas_archive.py），不在 /media 反代之下
        nasStatus() { return API.get('/api/media/nas-archive/status', { showError: false }); },
        nasJobs(limit = 20) { return API.get(`/api/media/nas-archive/jobs?limit=${limit}`, { showError: false }); },
        nasRetry(workKey) { return API.post('/api/media/nas-archive/retry', { work_key: workKey }); },
        // 手动批量登记某平台历史清单里已下载的作品，只入队不提交
        nasArchivePlatform(platform, account) {
            return API.post('/api/media/nas-archive/platform', {
                platform,
                account: account || '',
            });
        },
        // 索引返回的 thumb/file 是媒体服务内部路径，经宿主必须补 /media 前缀
        thumb(item) { return '/media' + item.thumb; },
        original(item) { return '/media' + item.file; },
        display(item) { return '/media' + item.file.split('&display=1')[0]; },
    },

    // ── 全局文件检索与查重 API（媒体服务 /api/files/*）──────────────
    // 只读：只取已入库索引（images / standalone_texts / videos），不做磁盘扫描。
    // 公共查询参数一律用 URLSearchParams 组装，空值跳过，避免后端收到空串被当成过滤条件。
    files: {
        list(params = {}, opts) {
            const qs = new URLSearchParams();
            Object.entries(params || {}).forEach(([key, value]) => {
                if (value !== undefined && value !== null && value !== '') qs.set(key, value);
            });
            return API.get(`/media/api/files/list?${qs.toString()}`, opts);
        },
        duplicates(params = {}, opts) {
            const qs = new URLSearchParams();
            Object.entries(params || {}).forEach(([key, value]) => {
                if (value !== undefined && value !== null && value !== '') qs.set(key, value);
            });
            return API.get(`/media/api/files/duplicates?${qs.toString()}`, opts);
        },
        group(sha, opts) {
            return API.get(`/media/api/files/group/${encodeURIComponent(sha)}`, opts);
        },
        stats(opts) { return API.get('/media/api/files/stats', { showError: false, ...opts }); },
        // 删除单个已入库文件：先删物理文件、成功后清索引记录。破坏性写操作，前端需逐条二次确认。
        deleteFile(payload, opts) { return API.post('/media/api/files/delete', payload, opts); },
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
