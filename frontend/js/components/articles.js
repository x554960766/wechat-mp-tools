/**
 * 文章下载页面组件
 */
const ArticlesPage = {
    currentFakeid: '',
    currentName: '',
    articles: [],
    selectedArticles: new Set(),
    selectionMode: 'single',
    currentPage: 0,
    pageSize: 10,
    total: 0,
    keyword: '',
    downloadTaskId: null,
    _pollTimer: null,

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">文章下载</h2>
                <p class="page-description">选择公众号，浏览并下载文章</p>
            </div>

            <!-- 公众号选择器 -->
            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="card-header">
                    <h3 class="card-title">📌 选择公众号</h3>
                </div>
                <div id="account-selector" style="display: flex; flex-wrap: wrap; gap: 8px;">
                    <div class="spinner" style="margin: 0 auto;"></div>
                </div>
            </div>

            <!-- 文章列表区域 -->
            <div id="articles-section" style="display: none;">
                <div class="article-controls">
                    <div class="article-control-row">
                        <div class="search-box">
                            <svg class="search-icon" viewBox="0 0 24 24" fill="none">
                                <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2"/>
                                <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                            </svg>
                            <input type="text" class="form-input" id="search-article-input"
                                   placeholder="搜索文章标题..."
                                   onkeydown="if(event.key==='Enter') ArticlesPage.searchArticles()">
                        </div>
                        <button class="btn btn-secondary btn-sm" onclick="ArticlesPage.searchArticles()">搜索</button>
                        <button class="btn btn-secondary btn-sm" onclick="ArticlesPage.clearSearch()">清除</button>
                        <div class="article-mode-switch">
                            <button class="btn btn-primary btn-sm" id="btn-mode-single" onclick="ArticlesPage.setSelectionMode('single')">单篇</button>
                            <button class="btn btn-secondary btn-sm" id="btn-mode-multi" onclick="ArticlesPage.setSelectionMode('multi')">多选</button>
                        </div>
                    </div>
                    <div class="article-control-row article-date-row">
                        <span class="article-date-label">时间范围</span>
                        <input type="date" class="form-input" id="article-start-date">
                        <span class="article-date-separator">至</span>
                        <input type="date" class="form-input" id="article-end-date">
                        <button class="btn btn-primary btn-sm" onclick="ArticlesPage.downloadDateRange()">按时间下载</button>
                        <button class="btn btn-secondary btn-sm" onclick="ArticlesPage.clearDateFilter()">重置</button>
                        <button class="btn btn-primary btn-sm" id="btn-download-selected" onclick="ArticlesPage.downloadSelected()" style="display: none;" disabled>
                            下载选中 (<span id="selected-count">0</span>)
                        </button>
                    </div>
                </div>

                <!-- 文章列表 -->
                <div id="articles-list" class="article-list">
                    <div class="empty-state">
                        <p class="empty-state-desc">请先选择一个公众号</p>
                    </div>
                </div>

                <!-- 分页 -->
                <div class="pagination" id="articles-pagination" style="display: none;">
                    <button class="btn btn-secondary btn-sm" onclick="ArticlesPage.prevPage()" id="btn-prev-page">上一页</button>
                    <span class="pagination-info" id="pagination-info">第 1 页</span>
                    <button class="btn btn-secondary btn-sm" onclick="ArticlesPage.nextPage()" id="btn-next-page">下一页</button>
                </div>
            </div>

            <!-- 下载进度 -->
            <div id="download-progress-section" style="display: none;">
                <div class="download-progress-card">
                    <div class="download-progress-header" style="display: flex; align-items: center; justify-content: space-between; gap: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <h3 class="card-title" style="color: #000000;">📥 下载进度</h3>
                            <span class="badge badge-info" id="download-status-badge">下载中</span>
                        </div>
                        <button class="btn btn-primary btn-sm" onclick="ArticlesPage.openFolder()" style="padding: 4px 10px; font-size: 0.85rem;">
                            📂 打开下载目录
                        </button>
                        <button class="btn btn-danger btn-sm" id="btn-cancel-download" onclick="ArticlesPage.cancelDownload()" style="padding: 4px 10px; font-size: 0.85rem; display: none;">
                            停止
                        </button>
                    </div>
                    <div class="progress-bar" style="margin-top: 12px;">
                        <div class="progress-fill" id="download-progress-bar" style="width: 0%"></div>
                    </div>
                    <div class="download-progress-stats">
                        <span>当前: <strong id="download-current">-</strong></span>
                        <span>完成: <strong id="download-completed">0</strong></span>
                        <span>失败: <strong id="download-failed">0</strong></span>
                        <span>总计: <strong id="download-total">0</strong></span>
                        <span id="download-scanned-wrap" style="display: none;">已扫描: <strong id="download-scanned">0</strong></span>
                    </div>
                    <div id="download-stop-reason" style="display: none; margin-top: 8px; color: var(--text-muted); font-size: 0.85rem;"></div>
                    <div id="download-note" style="margin-top: 12px; color: var(--text-muted); font-size: 0.85rem;">
                        下载完成后可在“下载历史”中查看文件列表。
                    </div>
                </div>
            </div>
        `;
    },

    async init(params = {}) {
        await this.loadAccountSelector();
        if (params.fakeid && params.name) {
            this.selectAccount(params.fakeid, params.name);
        }
    },

    destroy() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    },

    async loadAccountSelector() {
        const container = document.getElementById('account-selector');
        try {
            const data = await API.accounts.list();
            const accounts = data.accounts || [];

            if (accounts.length === 0) {
                container.innerHTML = `
                    <div style="text-align: center; width: 100%; color: var(--text-muted); padding: 12px;">
                        还没有收藏公众号，<a href="#accounts" style="color: var(--primary); text-decoration: none;">去添加</a>
                    </div>
                `;
                return;
            }

            container.innerHTML = accounts.map(acc => `
                <button class="btn btn-secondary btn-sm account-selector-btn"
                        data-fakeid="${acc.fakeid}"
                        onclick="ArticlesPage.selectAccount('${acc.fakeid}', '${acc.nickname}')">
                    ${acc.nickname}
                </button>
            `).join('');
        } catch (err) {
            container.innerHTML = '<span style="color: var(--error);">加载失败</span>';
        }
    },

    async selectAccount(fakeid, name) {
        this.currentFakeid = fakeid;
        this.currentName = name;
        this.currentPage = 0;
        this.selectedArticles.clear();
        this.selectionMode = 'single';
        this.keyword = '';

        // 高亮选中的公众号
        document.querySelectorAll('.account-selector-btn').forEach(btn => {
            btn.classList.toggle('btn-primary', btn.dataset.fakeid === fakeid);
            btn.classList.toggle('btn-secondary', btn.dataset.fakeid !== fakeid);
        });

        document.getElementById('articles-section').style.display = 'block';
        document.getElementById('search-article-input').value = '';
        
        const startDateEl = document.getElementById('article-start-date');
        const endDateEl = document.getElementById('article-end-date');
        if (startDateEl) startDateEl.value = '';
        if (endDateEl) endDateEl.value = '';
        
        this.updateSelectedCount();
        this.updateModeUI();

        await this.loadArticles();
    },

    async loadArticles() {
        const container = document.getElementById('articles-list');
        container.innerHTML = '<div class="loading-screen" style="min-height: 200px;"><div class="spinner"></div><p>加载文章列表...</p></div>';

        try {
            const begin = this.currentPage * this.pageSize;
            const data = await API.articles.list(this.currentFakeid, begin, this.pageSize, this.keyword);

            this.articles = data.articles || [];
            this.total = data.total || 0;

            this.renderArticles();
            this.updatePagination();
        } catch (err) {
            container.innerHTML = `<div class="empty-state"><p class="empty-state-desc">加载失败: ${err.message}</p></div>`;
        }
    },

    setSelectionMode(mode) {
        this.selectionMode = mode;
        if (mode === 'single') {
            this.selectedArticles.clear();
        }
        this.renderArticles();
        this.updateModeUI();
        this.updateSelectedCount();
    },

    updateModeUI() {
        const singleBtn = document.getElementById('btn-mode-single');
        const multiBtn = document.getElementById('btn-mode-multi');
        const downloadSelectedBtn = document.getElementById('btn-download-selected');
        if (singleBtn) {
            singleBtn.classList.toggle('btn-primary', this.selectionMode === 'single');
            singleBtn.classList.toggle('btn-secondary', this.selectionMode !== 'single');
        }
        if (multiBtn) {
            multiBtn.classList.toggle('btn-primary', this.selectionMode === 'multi');
            multiBtn.classList.toggle('btn-secondary', this.selectionMode !== 'multi');
        }
        if (downloadSelectedBtn) {
            downloadSelectedBtn.style.display = this.selectionMode === 'multi' ? 'inline-flex' : 'none';
        }
    },

    renderArticles() {
        const container = document.getElementById('articles-list');

        if (this.articles.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <h3 class="empty-state-title">没有找到文章</h3>
                    <p class="empty-state-desc">${this.keyword ? '尝试其他搜索关键字' : '该公众号暂无文章'}</p>
                </div>
            `;
            return;
        }

        container.innerHTML = this.articles.map((article, idx) => {
            const globalIdx = this.currentPage * this.pageSize + idx;
            const isSelected = this.selectedArticles.has(globalIdx);
            const time = article.update_time
                ? new Date(article.update_time * 1000).toLocaleDateString('zh-CN')
                : '';

            return `
                <div class="article-item ${isSelected ? 'selected' : ''}" data-idx="${globalIdx}">
                    ${this.selectionMode === 'multi' ? `
                        <div class="article-checkbox">
                            <input type="checkbox" ${isSelected ? 'checked' : ''}
                                   onchange="ArticlesPage.toggleArticle(${globalIdx}, ${idx}, this.checked)">
                        </div>
                    ` : ''}
                    ${article.cover
                        ? `<img class="article-cover" src="${article.cover}" alt="" loading="lazy"
                                onerror="this.style.display='none'">`
                        : ''
                    }
                    <div class="article-info">
                        <div class="article-title">${article.title || '无标题'}</div>
                        ${article.digest ? `<div class="article-digest">${article.digest}</div>` : ''}
                        <div class="article-meta">
                            ${time ? `<span>📅 ${time}</span>` : ''}
                            ${article.author ? `<span>✍️ ${article.author}</span>` : ''}
                            ${article.is_original ? '<span class="badge badge-info" style="font-size: 0.7rem;">原创</span>' : ''}
                        </div>
                    </div>
                    <div class="article-actions">
                        <button class="btn btn-primary btn-sm" onclick="ArticlesPage.downloadSingle(${idx})">下载</button>
                    </div>
                </div>
            `;
        }).join('');
    },

    toggleArticle(globalIdx, localIdx, checked) {
        if (checked) {
            this.selectedArticles.add(globalIdx);
            this.articles[localIdx]._selected = true;
        } else {
            this.selectedArticles.delete(globalIdx);
            this.articles[localIdx]._selected = false;
        }

        // 更新视觉状态
        const item = document.querySelector(`.article-item[data-idx="${globalIdx}"]`);
        if (item) item.classList.toggle('selected', checked);

        this.updateSelectedCount();
    },

    updateSelectedCount() {
        const countEl = document.getElementById('selected-count');
        const btn = document.getElementById('btn-download-selected');
        if (countEl) countEl.textContent = this.selectedArticles.size;
        if (btn) btn.disabled = this.selectedArticles.size === 0;
    },

    searchArticles() {
        const input = document.getElementById('search-article-input');
        this.keyword = input?.value.trim() || '';
        this.currentPage = 0;
        this.loadArticles();
    },

    clearSearch() {
        document.getElementById('search-article-input').value = '';
        this.keyword = '';
        const startDateEl = document.getElementById('article-start-date');
        const endDateEl = document.getElementById('article-end-date');
        if (startDateEl) startDateEl.value = '';
        if (endDateEl) endDateEl.value = '';
        this.currentPage = 0;
        this.loadArticles();
    },

    getDateRange() {
        const startVal = document.getElementById('article-start-date')?.value;
        const endVal = document.getElementById('article-end-date')?.value;
        if (!startVal || !endVal) {
            return null;
        }
        const startTimestamp = Math.floor(new Date(startVal + 'T00:00:00').getTime() / 1000);
        const endTimestamp = Math.floor(new Date(endVal + 'T23:59:59').getTime() / 1000);
        if (startTimestamp > endTimestamp) {
            return { error: '开始日期不能晚于结束日期' };
        }
        return { startTimestamp, endTimestamp };
    },

    clearDateFilter() {
        const startInput = document.getElementById('article-start-date');
        const endInput = document.getElementById('article-end-date');
        if (startInput) startInput.value = '';
        if (endInput) endInput.value = '';
    },

    updatePagination() {
        const paginationEl = document.getElementById('articles-pagination');
        const infoEl = document.getElementById('pagination-info');
        const prevBtn = document.getElementById('btn-prev-page');
        const nextBtn = document.getElementById('btn-next-page');

        const totalPages = Math.ceil(this.total / this.pageSize) || 1;
        const currentPageNum = this.currentPage + 1;

        if (this.total > this.pageSize) {
            paginationEl.style.display = 'flex';
            infoEl.textContent = `第 ${currentPageNum} / ${totalPages} 页 · 共 ${this.total} 篇`;
            prevBtn.disabled = this.currentPage <= 0;
            nextBtn.disabled = currentPageNum >= totalPages;
        } else {
            paginationEl.style.display = this.total > 0 ? 'flex' : 'none';
            infoEl.textContent = `共 ${this.total} 篇`;
            prevBtn.disabled = true;
            nextBtn.disabled = true;
        }
    },

    prevPage() {
        if (this.currentPage > 0) {
            this.currentPage--;
            this.loadArticles();
        }
    },

    nextPage() {
        const totalPages = Math.ceil(this.total / this.pageSize);
        if (this.currentPage + 1 < totalPages) {
            this.currentPage++;
            this.loadArticles();
        }
    },

    startTask(data) {
        this.downloadTaskId = data.task_id;
        Toast.success(data.message);
        this.showDownloadProgress();
        this.startProgressPolling();
    },

    async downloadArticles(articles) {
        try {
            const data = await API.articles.download(articles, this.currentName);
            this.startTask(data);
        } catch (err) {
            // error shown by API
        }
    },

    async downloadSingle(localIdx) {
        const article = this.articles[localIdx];
        if (!article) return;
        await this.downloadArticles([{ title: article.title, link: article.link }]);
    },

    async downloadSelected() {
        const selectedList = [];
        this.articles.forEach((article, localIdx) => {
            const globalIdx = this.currentPage * this.pageSize + localIdx;
            if (this.selectedArticles.has(globalIdx)) {
                selectedList.push({ title: article.title, link: article.link });
            }
        });

        if (selectedList.length === 0) {
            Toast.warning('请先选择要下载的文章');
            return;
        }
        await this.downloadArticles(selectedList);
    },

    async downloadDateRange() {
        if (!this.currentFakeid) {
            Toast.warning('请先选择公众号');
            return;
        }
        const range = this.getDateRange();
        if (!range) {
            Toast.warning('请选择完整的开始和结束日期');
            return;
        }
        if (range.error) {
            Toast.warning(range.error);
            return;
        }

        try {
            const data = await API.articles.downloadRange({
                fakeid: this.currentFakeid,
                account_name: this.currentName,
                start_time: range.startTimestamp,
                end_time: range.endTimestamp,
                keyword: this.keyword,
                page_size: this.pageSize,
            });
            this.startTask(data);
        } catch (err) {
            // error shown by API
        }
    },

    showDownloadProgress() {
        document.getElementById('download-progress-section').style.display = 'block';
        document.getElementById('download-progress-section').scrollIntoView({ behavior: 'smooth' });
        const cancelBtn = document.getElementById('btn-cancel-download');
        if (cancelBtn) cancelBtn.style.display = 'inline-flex';
    },

    startProgressPolling() {
        if (this._pollTimer) clearInterval(this._pollTimer);

        this._pollTimer = setInterval(async () => {
            try {
                const task = await API.articles.downloadStatus(this.downloadTaskId);
                this.updateDownloadProgress(task);

                if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                }
            } catch (err) {
                clearInterval(this._pollTimer);
                this._pollTimer = null;
            }
        }, 1000);
    },

    updateDownloadProgress(task) {
        const total = task.total || 0;
        const completed = task.completed || 0;
        const failed = task.failed || 0;
        const progress = total > 0 ? Math.round(((completed + failed) / total) * 100) : 0;

        document.getElementById('download-progress-bar').style.width = `${progress}%`;
        document.getElementById('download-current').textContent = task.current || '-';
        document.getElementById('download-completed').textContent = completed;
        document.getElementById('download-failed').textContent = failed;
        document.getElementById('download-total').textContent = total;

        const scannedWrap = document.getElementById('download-scanned-wrap');
        const scannedEl = document.getElementById('download-scanned');
        if (scannedWrap && scannedEl) {
            scannedWrap.style.display = task.scanned ? 'inline' : 'none';
            scannedEl.textContent = task.scanned || 0;
        }

        const reasonEl = document.getElementById('download-stop-reason');
        if (reasonEl) {
            reasonEl.style.display = task.stop_reason ? 'block' : 'none';
            reasonEl.textContent = task.stop_reason ? `停止原因：${task.stop_reason}` : '';
        }

        const badge = document.getElementById('download-status-badge');
        if (task.status === 'completed') {
            badge.className = 'badge badge-success';
            badge.textContent = '已完成';
            Toast.success(`下载完成！成功 ${completed} 篇，失败 ${failed} 篇`);
        } else if (task.status === 'failed') {
            badge.className = 'badge badge-error';
            badge.textContent = '失败';
        } else if (task.status === 'cancelled') {
            badge.className = 'badge badge-warning';
            badge.textContent = '已停止';
        } else if (task.status === 'cancelling') {
            badge.className = 'badge badge-warning';
            badge.textContent = '停止中';
        } else {
            badge.className = 'badge badge-info';
            badge.textContent = '下载中';
        }

        const cancelBtn = document.getElementById('btn-cancel-download');
        if (cancelBtn) {
            cancelBtn.style.display = (task.status === 'running' || task.status === 'cancelling') ? 'inline-flex' : 'none';
            cancelBtn.disabled = task.status === 'cancelling';
        }

        const note = document.getElementById('download-note');
        if (note && task.status === 'completed') {
            note.innerHTML = '下载完成。请到 <a href="#history" style="color: var(--primary); text-decoration: none;">下载历史</a> 查看文件列表。';
        }
    },

    async cancelDownload() {
        if (!this.downloadTaskId) return;
        try {
            await API.articles.cancelDownload(this.downloadTaskId);
            Toast.info('正在停止下载...');
        } catch (err) {
            // shown by API
        }
    },

    async openFolder() {
        try {
            await API.articles.openFolder();
            Toast.success('下载目录已打开');
        } catch (err) {
            // shown by API
        }
    },
};
