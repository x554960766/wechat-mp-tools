/**
 * 文章下载页面组件
 */
const ArticlesPage = {
    currentFakeid: '',
    currentName: '',
    articles: [],
    selectedArticles: new Set(),
    pageCache: {},
    loadedArticlesMap: new Map(),
    selectionMode: 'single',
    currentPage: 0,
    pageSize: 20,
    batchSize: 100,
    buffer: [],
    bufferBegin: 0,
    bufferCanContinue: true,
    _batchPromise: null,
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
                <div class="card-header" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                    <h3 class="card-title">📌 选择公众号</h3>
                    <button class="btn btn-warning btn-sm" onclick="ArticlesPage.collectAllAccounts()" title="一键按顺序采集所有已关注公众号最近50篇推文（自动跳过已下载存量，深度优化风控）">
                        ⚡ 全部关注公众号一键采集 (各50篇)
                    </button>
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
                        <select class="form-input" id="article-quick-date" onchange="ArticlesPage.onQuickDateChange(this.value)" style="width: auto; padding: 4px 8px; font-size: 0.85rem; cursor: pointer;">
                            <option value="">快捷日期 ▾</option>
                            <option value="today">今天</option>
                            <option value="3days">近3天</option>
                            <option value="7days">近7天</option>
                            <option value="30days">近30天</option>
                            <option value="this_month">本月</option>
                        </select>
                        <button class="btn btn-primary btn-sm" onclick="ArticlesPage.downloadDateRange()">按时间下载</button>
                        <button class="btn btn-warning btn-sm" onclick="ArticlesPage.collectCurrentAccount()" title="一键采集当前公众号最近50篇推文（自动跳过已下载，保护风控）">
                            ⚡ 采集最新50篇
                        </button>
                        <button class="btn btn-secondary btn-sm" onclick="ArticlesPage.clearDateFilter()">重置</button>
                        
                        <!-- 多选操作控制按钮组 -->
                        <div id="multi-select-actions" style="display: none; align-items: center; gap: 8px; margin-left: auto;">
                            <button class="btn btn-secondary btn-sm" onclick="ArticlesPage.selectAllLoaded()" title="全选所有已翻页加载的文章">
                                ☑️ 全选已加载 (<span id="loaded-total-count">0</span>)
                            </button>
                            <button class="btn btn-secondary btn-sm" onclick="ArticlesPage.clearSelection()">
                                ❌ 取消全选
                            </button>
                            <button class="btn btn-primary btn-sm" id="btn-download-selected" onclick="ArticlesPage.downloadSelected()" disabled>
                                📥 下载选中 (<span id="selected-count">0</span>)
                            </button>
                        </div>
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

    resetListState() {
        this.currentPage = 0;
        this.selectedArticles.clear();
        this.pageCache = {};
        this.loadedArticlesMap.clear();
        this.buffer = [];
        this.bufferBegin = 0;
        this.bufferCanContinue = true;
        this._batchPromise = null;
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
        this.resetListState();
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

    async ensureBatchFor(start) {
        const bufferEnd = this.bufferBegin + this.buffer.length;

        // 当前缓冲已覆盖目标位置
        if (start < bufferEnd) return;

        // 后端已明确没有更多文章
        if (!this.bufferCanContinue) return;

        // 已有相同批次的请求在飞行中，等待它完成后再判断
        if (this._batchPromise) {
            await this._batchPromise;
            if (start < this.bufferBegin + this.buffer.length) return;
        }

        this._batchPromise = (async () => {
            const nextBegin = this.bufferBegin + this.buffer.length;
            const data = await API.articles.list(
                this.currentFakeid,
                nextBegin,
                this.batchSize,
                this.keyword
            );
            const batch = data.articles || [];

            this.buffer.push(...batch);
            this.bufferCanContinue = !!data.can_msg_continue;
            this.total = data.total || 0;
            this.canMsgContinue = this.bufferCanContinue ? 1 : 0;
        })();

        try {
            await this._batchPromise;
        } finally {
            this._batchPromise = null;
        }
    },

    async loadArticles() {
        const container = document.getElementById('articles-list');

        // ⚡ 页码缓存：已渲染过的页直接复用
        if (this.pageCache[this.currentPage]) {
            const cached = this.pageCache[this.currentPage];
            this.articles = cached.articles;
            this.total = cached.total;
            this.canMsgContinue = cached.canMsgContinue;
            this.renderArticles();
            this.updatePagination();
            this.updateSelectedCount();
            return;
        }

        container.innerHTML = '<div class="loading-screen" style="min-height: 200px;"><div class="spinner"></div><p>加载文章列表...</p></div>';

        try {
            const start = this.currentPage * this.pageSize;
            const end = start + this.pageSize;

            // 缓冲不足时，向后端拉取下一批 100 篇
            await this.ensureBatchFor(start);

            const localStart = start - this.bufferBegin;
            const localEnd = end - this.bufferBegin;
            this.articles = this.buffer.slice(localStart, localEnd);

            // 写入页码缓存
            this.pageCache[this.currentPage] = {
                articles: this.articles,
                total: this.total,
                canMsgContinue: this.canMsgContinue
            };

            // 索引到已加载列表映射，供跨页多选与全选使用
            this.articles.forEach((article, idx) => {
                const globalIdx = this.currentPage * this.pageSize + idx;
                this.loadedArticlesMap.set(globalIdx, article);
            });

            this.renderArticles();
            this.updatePagination();
            this.updateSelectedCount();
        } catch (err) {
            const isWaterwall = err.message && (err.message.includes('防水墙') || err.message.includes('安全验证') || err.message.includes('tcaptcha') || err.message.includes('人机验证'));
            const isAuthError = !isWaterwall && err.message && (err.message.includes('失效') || err.message.includes('未找到有效凭证') || err.message.includes('ret=-3') || err.message.includes('401') || err.message.includes('过期') || err.message.includes('会话'));
            const accountName = this.currentAccount ? (this.currentAccount.nickname || '') : '';
            
            if (isWaterwall) {
                container.innerHTML = `
                    <div class="empty-state" style="padding: 40px 20px;">
                        <div style="font-size: 2.8rem; margin-bottom: 12px;">🛡️</div>
                        <h3 class="empty-state-title" style="margin-bottom: 8px; color: #f59e0b;">触发腾讯安全验证（防水墙）</h3>
                        <p class="empty-state-desc" style="max-width: 540px; margin: 0 auto 20px; line-height: 1.6; color: var(--text-secondary, #666);">
                            微信读书上游接口检测到频率较高，触发了人机验证。<br>
                            👉 <strong>方法 1（推荐）</strong>：点击下方按钮直接在网页中滑动完成验证码；<br>
                            👉 <strong>方法 2</strong>：在手机微信读书 App 中打开任意书籍阅读片刻后，点击“已在手机处理”。
                        </p>
                        <div style="display: flex; justify-content: center; gap: 10px; flex-wrap: wrap;">
                            <button class="btn btn-primary" onclick="WaterWall.triggerCaptcha({ onSuccess: () => ArticlesPage.loadArticles() })">🧩 立即网页滑动验证</button>
                            <button class="btn btn-secondary" onclick="fetch('/api/waterwall/clear', {method:'POST'}).then(() => ArticlesPage.loadArticles())">✅ 已在App处理并重试</button>
                        </div>
                    </div>
                `;
            } else {
                container.innerHTML = `
                    <div class="empty-state" style="padding: 40px 20px;">
                        <div style="font-size: 2.5rem; margin-bottom: 12px;">${isAuthError ? '🔐' : '⚠️'}</div>
                        <h3 class="empty-state-title" style="margin-bottom: 8px;">${isAuthError ? (accountName ? `【${accountName}】尚未建立阅读会话` : '微信采集凭证已过期') : '加载文章列表失败'}</h3>
                        <p class="empty-state-desc" style="max-width: 540px; margin: 0 auto 20px; line-height: 1.6; color: var(--text-secondary, #666);">
                            ${isAuthError ? `微信 PC 客户端要求每个公众号首次抓取前建立独立阅读 Session（需打开公众号『主页』，仅点开文章不够）。<br>👉 <strong>方法 1</strong>：点击下方按钮，由系统自动在电脑微信中打开该号主页；<br>👉 <strong>方法 2</strong>：在电脑微信中进入该公众号任一文章 → 点击文章顶部的<strong>公众号名称/头像</strong>进入主页（出现历史文章列表即成功）。` : (err.message || '网络连接超时')}
                        </p>
                        <div style="display: flex; justify-content: center; gap: 10px; flex-wrap: wrap;">
                            ${accountName ? `<button class="btn btn-primary" onclick="ArticlesPage.syncTargetAccount('${accountName}')">🤖 自动为【${accountName}】同步凭证</button>` : ''}
                            <button class="btn btn-secondary" onclick="ArticlesPage.loadArticles()">🔄 重新检测并加载</button>
                        </div>
                    </div>
                `;
            }
        }
    },

    async syncTargetAccount(accountName) {
        if (!accountName) return;
        Toast.info(`正在尝试为【${accountName}】自动在微信中打开文章，请稍候...`);
        try {
            const resp = await fetch('/api/accounts/sync-pc-wechat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ keyword: accountName })
            });
            const data = await resp.json();
            if (data.success) {
                Toast.success(`【${accountName}】文章已定位，正在重新拉取列表...`);
                setTimeout(() => this.loadArticles(), 2500);
            } else {
                Toast.warning(`自动化指令已发送，若未成功请手动在微信点开一篇文章后点击重试。`);
            }
        } catch (e) {
            Toast.error(`触发自动同步失败: ${e.message}`);
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
        const multiActions = document.getElementById('multi-select-actions');
        if (singleBtn) {
            singleBtn.classList.toggle('btn-primary', this.selectionMode === 'single');
            singleBtn.classList.toggle('btn-secondary', this.selectionMode !== 'single');
        }
        if (multiBtn) {
            multiBtn.classList.toggle('btn-primary', this.selectionMode === 'multi');
            multiBtn.classList.toggle('btn-secondary', this.selectionMode !== 'multi');
        }
        if (multiActions) {
            multiActions.style.display = this.selectionMode === 'multi' ? 'flex' : 'none';
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

        const formatArticleTime = (ts) => {
            if (!ts) return '';
            const d = new Date(ts * 1000);
            if (isNaN(d.getTime())) return '';
            const y = d.getFullYear();
            const m = String(d.getMonth() + 1).padStart(2, '0');
            const day = String(d.getDate()).padStart(2, '0');
            const hh = String(d.getHours()).padStart(2, '0');
            const mm = String(d.getMinutes()).padStart(2, '0');
            return `${y}-${m}-${day} ${hh}:${mm}`;
        };

        container.innerHTML = this.articles.map((article, idx) => {
            const globalIdx = this.currentPage * this.pageSize + idx;
            const isSelected = this.selectedArticles.has(globalIdx);
            const time = formatArticleTime(article.update_time);

            return `
                <div class="article-item ${isSelected ? 'selected' : ''}" data-idx="${globalIdx}" onclick="ArticlesPage.handleArticleClick(${globalIdx}, ${idx}, event)">
                    ${this.selectionMode === 'multi' ? `
                        <div class="article-checkbox">
                            <input type="checkbox" ${isSelected ? 'checked' : ''}
                                   onchange="event.stopPropagation(); ArticlesPage.toggleArticle(${globalIdx}, ${idx}, this.checked)">
                        </div>
                    ` : ''}
                    ${article.cover
                        ? `<img class="article-cover" src="${article.cover}" alt="" loading="lazy" onerror="this.style.display='none'">`
                        : ''
                    }
                    <div class="article-info">
                        <div class="article-title">
                            <a href="${article.link || '#'}" target="_blank" rel="noopener noreferrer"
                               style="color: var(--text-primary); text-decoration: none; font-weight: 600; transition: color 0.2s;"
                               onmouseenter="this.style.color='var(--primary)';this.style.textDecoration='underline'"
                               onmouseleave="this.style.color='var(--text-primary)';this.style.textDecoration='none'"
                               onclick="event.stopPropagation()">
                                ${article.title || '无标题'} ↗
                            </a>
                        </div>
                        ${article.digest ? `<div class="article-digest">${article.digest}</div>` : ''}
                        <div class="article-meta">
                            ${time ? `<span>📅 ${time}</span>` : ''}
                            ${article.author ? `<span>✍️ ${article.author}</span>` : ''}
                            ${article.is_original ? '<span class="badge badge-info" style="font-size: 0.7rem;">原创</span>' : ''}
                        </div>
                    </div>
                    <div class="article-actions" style="display: flex; gap: 8px; align-items: center;">
                        ${article.link ? `
                            <a class="btn btn-secondary btn-sm" href="${article.link}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()" style="font-size: 0.8rem; padding: 4px 10px; display: inline-flex; align-items: center; gap: 4px;">
                                🌐 打开原文
                            </a>
                        ` : ''}
                        <button class="btn btn-primary btn-sm" onclick="event.stopPropagation(); ArticlesPage.downloadSingle(${idx})">下载</button>
                    </div>
                </div>
            `;
        }).join('');
    },

    handleArticleClick(globalIdx, localIdx, event) {
        // 如果点击的是操作按钮或复选框等，不处理跳转/选中
        if (event.target.closest('.article-actions') || event.target.closest('.article-checkbox')) {
            return;
        }

        if (this.selectionMode === 'multi') {
            const isSelected = this.selectedArticles.has(globalIdx);
            const nextChecked = !isSelected;
            
            // 同步更新复选框 DOM 状态
            const item = document.querySelector(`.article-item[data-idx="${globalIdx}"]`);
            if (item) {
                const checkbox = item.querySelector('.article-checkbox input[type="checkbox"]');
                if (checkbox) checkbox.checked = nextChecked;
            }
            this.toggleArticle(globalIdx, localIdx, nextChecked);
        } else {
            const article = this.articles[localIdx];
            if (article && article.link) {
                window.open(article.link, '_blank');
            }
        }
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
        const loadedTotalEl = document.getElementById('loaded-total-count');
        const btn = document.getElementById('btn-download-selected');
        if (countEl) countEl.textContent = this.selectedArticles.size;
        if (loadedTotalEl) loadedTotalEl.textContent = this.loadedArticlesMap.size;
        if (btn) btn.disabled = this.selectedArticles.size === 0;
    },

    selectAllLoaded() {
        if (this.loadedArticlesMap.size === 0) {
            Toast.warning('当前暂无已加载的文章');
            return;
        }
        this.loadedArticlesMap.forEach((article, globalIdx) => {
            this.selectedArticles.add(globalIdx);
        });
        this.renderArticles();
        this.updateSelectedCount();
        Toast.success(`已选中所有已加载的 ${this.selectedArticles.size} 篇文章`);
    },

    clearSelection() {
        this.selectedArticles.clear();
        this.renderArticles();
        this.updateSelectedCount();
        Toast.info('已取消选择');
    },

    searchArticles() {
        const input = document.getElementById('search-article-input');
        this.keyword = input?.value.trim() || '';
        this.resetListState();
        this.loadArticles();
    },

    clearSearch() {
        document.getElementById('search-article-input').value = '';
        this.keyword = '';
        const startDateEl = document.getElementById('article-start-date');
        const endDateEl = document.getElementById('article-end-date');
        if (startDateEl) startDateEl.value = '';
        if (endDateEl) endDateEl.value = '';
        this.resetListState();
        this.loadArticles();
    },

    onQuickDateChange(type) {
        if (!type) return;
        const now = new Date();
        const startInput = document.getElementById('article-start-date');
        const endInput = document.getElementById('article-end-date');
        if (!startInput || !endInput) return;

        const fmt = d => {
            const year = d.getFullYear();
            const month = String(d.getMonth() + 1).padStart(2, '0');
            const day = String(d.getDate()).padStart(2, '0');
            return `${year}-${month}-${day}`;
        };
        const endStr = fmt(now);
        let startStr = endStr;

        if (type === 'today') {
            startStr = endStr;
        } else if (type === '3days') {
            const d = new Date(now.getTime() - 2 * 86400000);
            startStr = fmt(d);
        } else if (type === '7days') {
            const d = new Date(now.getTime() - 6 * 86400000);
            startStr = fmt(d);
        } else if (type === '30days') {
            const d = new Date(now.getTime() - 29 * 86400000);
            startStr = fmt(d);
        } else if (type === 'this_month') {
            const d = new Date(now.getFullYear(), now.getMonth(), 1);
            startStr = fmt(d);
        }

        startInput.value = startStr;
        endInput.value = endStr;
    },

    getDateRange() {
        const startVal = document.getElementById('article-start-date')?.value;
        const endVal = document.getElementById('article-end-date')?.value;
        if (!startVal && !endVal) {
            return null;
        }
        const now = new Date();
        const year = now.getFullYear();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        const day = String(now.getDate()).padStart(2, '0');
        const todayStr = `${year}-${month}-${day}`;

        const effectiveStart = startVal || '2000-01-01';
        const effectiveEnd = endVal || todayStr;

        const startTimestamp = Math.floor(new Date(effectiveStart + 'T00:00:00').getTime() / 1000);
        const endTimestamp = Math.floor(new Date(effectiveEnd + 'T23:59:59').getTime() / 1000);
        if (isNaN(startTimestamp) || isNaN(endTimestamp)) {
            return { error: '日期格式不正确' };
        }
        if (startTimestamp > endTimestamp) {
            return { error: '开始日期不能晚于结束日期' };
        }
        return { startTimestamp, endTimestamp };
    },

    clearDateFilter() {
        const startInput = document.getElementById('article-start-date');
        const endInput = document.getElementById('article-end-date');
        const quickSelect = document.getElementById('article-quick-date');
        if (startInput) startInput.value = '';
        if (endInput) endInput.value = '';
        if (quickSelect) quickSelect.value = '';
    },

    async collectCurrentAccount(limit = 50) {
        if (!this.currentFakeid) {
            Toast.warning('请先选择要采集的公众号');
            return;
        }
        if (!confirm(`确定一键采集【${this.currentName || '当前公众号'}】最近 ${limit} 篇推文吗？\n\n📌 保护机制：系统会自动跳过本地已下载的推文，仅抓取最新增量，保护风控。`)) {
            return;
        }
        try {
            const data = await API.articles.collectLatest({
                fakeid: this.currentFakeid,
                account_name: this.currentName,
                limit: limit,
                skip_existing: true
            });
            Toast.info(data.message || '已启动一键采集任务');
            this.startTask(data);
        } catch (err) {
            Toast.error(`启动一键采集失败: ${err.message}`);
        }
    },

    async collectAllAccounts(limit = 50) {
        if (!confirm(`确定一键采集【全部已关注公众号】各最近 ${limit} 篇推文吗？\n\n📌 运行说明：\n1. 系统将自动按顺序依次采集每个公众号；\n2. 已下载文章自动秒级跳过，杜绝重复网络请求与风控封控；\n3. 多号流转内置 2~3 秒平滑缓冲间隔。`)) {
            return;
        }
        try {
            const data = await API.articles.collectAll({
                limit: limit,
                skip_existing: true
            });
            Toast.info(data.message || '已启动全量一键采集任务');
            this.startTask(data);
        } catch (err) {
            Toast.error(`启动全量采集失败: ${err.message}`);
        }
    },

    updatePagination() {
        const paginationEl = document.getElementById('articles-pagination');
        const infoEl = document.getElementById('pagination-info');
        const prevBtn = document.getElementById('btn-prev-page');
        const nextBtn = document.getElementById('btn-next-page');

        const currentPageNum = this.currentPage + 1;
        const bufferEnd = this.bufferBegin + this.buffer.length;
        const nextPageStart = (this.currentPage + 1) * this.pageSize;

        const hasBufferedNext = nextPageStart < bufferEnd;
        const hasRemoteNext = !!this.bufferCanContinue;

        paginationEl.style.display = (this.articles.length > 0 || this.currentPage > 0) ? 'flex' : 'none';
        infoEl.textContent = `第 ${currentPageNum} 页 · 本页 ${this.articles.length} 篇`;

        prevBtn.disabled = this.currentPage <= 0;
        nextBtn.disabled = !(hasBufferedNext || hasRemoteNext);
    },

    prevPage() {
        if (this.currentPage > 0) {
            this.currentPage--;
            this.loadArticles();
        }
    },

    nextPage() {
        if (this.articles.length > 0) {
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
        await this.downloadArticles([{ title: article.title, link: article.link, id: article.id, aid: article.id }]);
    },

    async downloadSelected() {
        const selectedList = [];
        this.selectedArticles.forEach(globalIdx => {
            const article = this.loadedArticlesMap.get(globalIdx);
            if (article && article.link) {
                selectedList.push({ title: article.title, link: article.link, id: article.id, aid: article.id });
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
        let currentText = task.current || '-';
        if (task.total_accounts && task.current_account_idx) {
            currentText = `[${task.current_account_idx}/${task.total_accounts}] ${currentText}`;
        }
        document.getElementById('download-current').textContent = currentText;
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
            await API.articles.openFolder(this.currentName);
            Toast.success('下载目录已打开');
        } catch (err) {
            // shown by API
        }
    },
};
