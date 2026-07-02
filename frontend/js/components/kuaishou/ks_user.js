const KsUserPage = {
    url: '',
    items: [],
    pcursor: '',
    hasMore: false,
    isSelectMode: false,
    author: null,
    loading: false,
    loadingMore: false,

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">用户主页</h2>
                <p class="page-description">粘贴快手用户主页链接，查看作品列表，多选或批量下载（需先扫码登录）</p>
            </div>

            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="form-group">
                    <label class="form-label">用户主页链接</label>
                    <div style="display: flex; gap: var(--spacing-md);">
                        <input type="text" id="ks-user-url-input" class="form-input" placeholder="粘贴主页链接 (https://v.kuaishou.com/... 或 https://www.kuaishou.com/profile/...)" style="flex: 1;">
                        <button class="btn btn-primary" onclick="KsUserPage.loadList()" id="ks-user-load-btn">加载列表</button>
                    </div>
                </div>
            </div>

            <div class="card" id="ks-user-author-card" style="display: none; margin-bottom: var(--spacing-lg);">
                <div style="display: flex; gap: var(--spacing-lg); align-items: center;">
                    <img id="ks-user-avatar" src="" alt="头像" style="width: 72px; height: 72px; border-radius: 50%; object-fit: cover; border: 2px solid var(--border-color);">
                    <div style="flex: 1;">
                        <h2 id="ks-user-nickname" style="font-size: 1.3rem; margin: 0;"></h2>
                    </div>
                    <button class="btn btn-primary" onclick="KsUserPage.downloadAll()" id="ks-user-all-btn">
                        <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px; display: inline-block; vertical-align: text-bottom;">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                        批量下载全部作品
                    </button>
                </div>
            </div>

            <div class="card" id="ks-user-list-card" style="display: none;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--spacing-md); flex-wrap: wrap; gap: 12px;">
                    <h3 style="margin: 0; font-size: 1.1rem;">作品列表</h3>
                    <div id="ks-user-header-actions" style="display: flex; gap: 8px; align-items: center;"></div>
                </div>
                <div id="ks-user-grid" class="video-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: var(--spacing-md);"></div>
                <div id="ks-user-grid-empty" style="display: none; text-align: center; padding: var(--spacing-xl); color: var(--text-muted);">暂无作品</div>
                <div id="ks-user-more-container" style="text-align: center; display: none; padding: var(--spacing-md) 0; margin-top: var(--spacing-lg);">
                    <button id="ks-user-more-btn" class="btn btn-secondary" onclick="KsUserPage.loadMore()" style="min-width: 150px;">加载更多</button>
                </div>
            </div>
        `;
    },

    init() {},
    onShow() {},

    // ── 列表加载 ──────────────────────────────────────────
    async loadList() {
        const url = document.getElementById('ks-user-url-input').value.trim();
        if (!url) {
            Toast.show('请填写用户主页链接', 'warning');
            return;
        }
        this.url = url;
        this.items = [];
        this.pcursor = '';
        this.hasMore = false;
        this.isSelectMode = false;

        const btn = document.getElementById('ks-user-load-btn');
        btn.disabled = true;
        btn.textContent = '加载中...';
        try {
            const data = await API.kuaishou.userFeed(url, '');
            if (data.error) throw new Error(data.error);

            this.author = data.author || {};
            this.items = data.items || [];
            this.pcursor = data.pcursor || '';
            this.hasMore = !!data.has_more;

            this.renderAuthor();
            this.renderGrid();
            this.updateHeaderActions();

            document.getElementById('ks-user-list-card').style.display = 'block';
            if (this.items.length === 0) Toast.show('该主页暂无公开作品', 'info');
        } catch (err) {
            Toast.show(err.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '加载列表';
        }
    },

    async loadMore() {
        if (this.loadingMore || !this.hasMore) return;
        this.loadingMore = true;
        const moreBtn = document.getElementById('ks-user-more-btn');
        if (moreBtn) { moreBtn.disabled = true; moreBtn.textContent = '加载中...'; }
        try {
            const data = await API.kuaishou.userFeed(this.url, this.pcursor);
            if (data.error) throw new Error(data.error);
            const startIndex = this.items.length;
            const newItems = data.items || [];
            this.items = this.items.concat(newItems);
            this.pcursor = data.pcursor || '';
            this.hasMore = !!data.has_more;
            this.appendCards(newItems, startIndex);
            this.updateMoreButton();
        } catch (err) {
            Toast.show(err.message, 'error');
        } finally {
            this.loadingMore = false;
            if (moreBtn) { moreBtn.disabled = false; moreBtn.textContent = '加载更多'; }
        }
    },

    // ── 渲染 ──────────────────────────────────────────────
    renderAuthor() {
        const card = document.getElementById('ks-user-author-card');
        if (!this.author || (!this.author.name && !this.author.avatar)) {
            card.style.display = 'none';
            return;
        }
        document.getElementById('ks-user-avatar').src = this.author.avatar || '';
        document.getElementById('ks-user-nickname').textContent = this.author.name || '快手用户';
        card.style.display = 'block';
    },

    renderGrid() {
        const grid = document.getElementById('ks-user-grid');
        const empty = document.getElementById('ks-user-grid-empty');
        if (this.items.length === 0) {
            grid.innerHTML = '';
            empty.style.display = 'block';
            this.updateMoreButton();
            return;
        }
        empty.style.display = 'none';
        grid.innerHTML = this.items.map((item, i) => this.renderCard(item, i)).join('');
        this.updateMoreButton();
    },

    appendCards(newItems, startIndex) {
        const grid = document.getElementById('ks-user-grid');
        const html = newItems.map((item, i) => this.renderCard(item, startIndex + i)).join('');
        grid.insertAdjacentHTML('beforeend', html);
    },

    renderCard(item, index) {
        const title = item.title || '无标题';
        const cover = item.cover || '';
        const isVideo = item.type === 'video';
        const badge = isVideo ? '视频' : '图文';
        const badgeColor = isVideo ? 'rgba(254,44,85,0.85)' : 'rgba(76,175,80,0.85)';
        const safeTitle = title.replace(/"/g, '&quot;');
        return `
            <div class="video-card" style="border-radius: 12px; overflow: hidden; background: var(--bg-secondary); transition: transform 0.3s, box-shadow 0.3s; cursor: pointer;" onmouseenter="this.style.transform='translateY(-4px)'; this.style.boxShadow='0 8px 24px rgba(0,0,0,0.15)';" onmouseleave="this.style.transform=''; this.style.boxShadow='';" onclick="KsUserPage.handleCardClick(${index}, event)">
                <div style="position: relative; padding-top: 133%; background: var(--bg-body);">
                    <img src="${cover}" alt="${safeTitle}" loading="lazy" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover;">
                    <input type="checkbox" id="ks-user-check-${index}" class="ks-user-checkbox" data-index="${index}" style="position: absolute; top: 8px; left: 8px; width: 18px; height: 18px; cursor: pointer; z-index: 5; display: ${this.isSelectMode ? 'block' : 'none'};" onclick="event.stopPropagation(); KsUserPage.updateDownloadButton();" />
                    <div style="position: absolute; top: 8px; right: 8px; background: ${badgeColor}; color: white; padding: 3px 8px; border-radius: 4px; font-size: 0.72rem; z-index: 2;">${badge}</div>
                </div>
                <div style="padding: var(--spacing-md);">
                    <h3 style="font-size: 0.9rem; margin-bottom: 10px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; line-height: 1.4; min-height: 2.5em;" title="${safeTitle}">${title}</h3>
                    <button class="btn btn-primary btn-sm" onclick="KsUserPage.downloadOne(${index})" style="width: 100%;">下载</button>
                </div>
            </div>
        `;
    },

    updateMoreButton() {
        const c = document.getElementById('ks-user-more-container');
        if (c) c.style.display = this.hasMore ? 'block' : 'none';
    },

    // ── 多选模式 ──────────────────────────────────────────
    updateHeaderActions() {
        const container = document.getElementById('ks-user-header-actions');
        if (!container) return;
        if (this.isSelectMode) {
            container.innerHTML = `
                <button class="btn btn-secondary btn-sm" id="ks-user-select-all-btn" onclick="KsUserPage.toggleSelectAll()" style="padding: 6px 12px; font-size: 0.85rem;">全选</button>
                <button class="btn btn-primary" onclick="KsUserPage.downloadSelected()" id="ks-user-batch-btn" disabled>开始下载 (0)</button>
                <button class="btn btn-secondary" onclick="KsUserPage.exitSelectMode()">取消</button>
            `;
        } else {
            container.innerHTML = `
                <button class="btn btn-primary" onclick="KsUserPage.enterSelectMode()">批量选择下载</button>
            `;
        }
    },

    enterSelectMode() {
        this.isSelectMode = true;
        document.querySelectorAll('.ks-user-checkbox').forEach(cb => { cb.style.display = 'block'; cb.checked = false; });
        this.updateHeaderActions();
        this.updateDownloadButton();
    },
    exitSelectMode() {
        this.isSelectMode = false;
        document.querySelectorAll('.ks-user-checkbox').forEach(cb => { cb.style.display = 'none'; cb.checked = false; });
        this.updateHeaderActions();
    },
    toggleSelectAll() {
        const checkboxes = document.querySelectorAll('.ks-user-checkbox');
        const selected = this.getSelected();
        const selectAll = selected.length < checkboxes.length;
        checkboxes.forEach(cb => { cb.checked = selectAll; });
        this.updateDownloadButton();
    },
    getSelected() {
        const selected = [];
        document.querySelectorAll('.ks-user-checkbox').forEach(cb => {
            if (cb.checked) {
                const item = this.items[parseInt(cb.dataset.index)];
                if (item) selected.push(item);
            }
        });
        return selected;
    },
    updateDownloadButton() {
        const btn = document.getElementById('ks-user-batch-btn');
        if (!btn) return;
        const selected = this.getSelected();
        btn.disabled = selected.length === 0;
        btn.textContent = `开始下载 (${selected.length})`;
        const selectAllBtn = document.getElementById('ks-user-select-all-btn');
        if (selectAllBtn) {
            const checkboxes = document.querySelectorAll('.ks-user-checkbox');
            selectAllBtn.textContent = (checkboxes.length > 0 && selected.length === checkboxes.length) ? '取消全选' : '全选';
        }
    },

    // ── 卡片点击 ──────────────────────────────────────────
    handleCardClick(index, event) {
        // 点击下载按钮时不拦截
        if (event.target.closest('button')) return;
        // 点击 checkbox 本身时不拦截（已有自己的 onclick）
        if (event.target.type === 'checkbox') return;

        if (this.isSelectMode) {
            // 多选模式：切换 checkbox
            const cb = document.getElementById(`ks-user-check-${index}`);
            if (cb) {
                cb.checked = !cb.checked;
                this.updateDownloadButton();
            }
        } else {
            // 非多选模式：在新标签页打开作品详情页
            const item = this.items[index];
            if (!item || !item.photo_id) return;
            window.open(`https://www.kuaishou.com/short-video/${item.photo_id}`, '_blank');
        }
    },

    // ── 下载 ──────────────────────────────────────────────
    async downloadOne(index) {
        const item = this.items[index];
        if (!item) return;
        await this._startDownload([item]);
    },
    async downloadSelected() {
        const selected = this.getSelected();
        if (selected.length === 0) return;
        await this._startDownload(selected);
        this.exitSelectMode();
    },
    async _startDownload(items) {
        try {
            const data = await API.kuaishou.downloadSelected(items);
            if (data.error) throw new Error(data.error);
            Toast.show(data.message || '下载已启动，正在跳转到进度页面...', 'success');
            Router.navigate('ks_parse'); // 跳转到下载进度页面
        } catch (err) {
            Toast.show(err.message, 'error');
        }
    },
    async downloadAll() {
        const btn = document.getElementById('ks-user-all-btn');
        btn.disabled = true;
        try {
            const data = await API.kuaishou.downloadProfile(this.url, 0);
            if (data.error) throw new Error(data.error);
            Toast.show('批量下载全部已启动，正在跳转到进度页面...', 'success');
            Router.navigate('ks_parse'); // 跳转到下载进度页面
        } catch (err) {
            Toast.show(err.message, 'error');
        } finally {
            btn.disabled = false;
        }
    }
};
