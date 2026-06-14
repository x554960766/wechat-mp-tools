const DyRecommendPage = {
    videos: [],
    cursor: 0,
    loading: false,
    hasMore: true,
    isSelectMode: false,

    render() {
        return `
            <div class="page-header">
                <div style="display: flex; justify-content: space-between; align-items: center; width: 100%; flex-wrap: wrap; gap: 16px;">
                    <div>
                        <h2 class="page-title">推荐视频</h2>
                        <p class="page-description">浏览抖音推荐流内容</p>
                    </div>
                    <div id="dy-recommend-header-actions" style="display: flex; gap: 8px; align-items: center;"></div>
                </div>
            </div>

            <div id="dy-recommend-container">
                <div id="dy-recommend-grid" class="video-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: var(--spacing-md);"></div>

                <div id="dy-recommend-loading" style="display: none; text-align: center; padding: var(--spacing-xl);">
                    <div class="spinner"></div>
                    <p style="margin-top: var(--spacing-md); color: var(--text-muted);">加载中...</p>
                </div>

                <div id="dy-recommend-empty" style="display: none; text-align: center; padding: var(--spacing-2xl);">
                    <div style="width: 64px; height: 64px; margin: 0 auto var(--spacing-md); background: rgba(156, 39, 176, 0.1); border-radius: 20px; display: flex; align-items: center; justify-content: center;">
                        <svg viewBox="0 0 24 24" fill="none" style="width: 32px; height: 32px; color: #9c27b0;">
                            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </div>
                    <p style="font-size: 1.1rem; margin-bottom: 8px;">暂无推荐内容</p>
                    <p style="color: var(--text-muted);">需要登录后才能获取推荐视频</p>
                </div>

                <div id="dy-recommend-loadmore" style="display: none; text-align: center; padding: var(--spacing-lg);">
                    <button class="btn btn-secondary" onclick="DyRecommendPage.loadMore()">加载更多</button>
                </div>
            </div>
        `;
    },

    async init() {
        this.videos = [];
        this.cursor = 0;
        this.hasMore = true;
        this.isSelectMode = false;
        await this.loadFeed();
    },

    async loadFeed() {
        if (this.loading) return;

        this.loading = true;
        this.showLoading();

        try {
            const res = await fetch(`/api/douyin/feed?count=18&cursor=${this.cursor}`);
            const data = await res.json();

            if (data.error) {
                throw new Error(data.error);
            }

            const videos = data.aweme_list || [];
            this.videos = videos;
            this.cursor = data.max_cursor || 0;
            this.hasMore = data.has_more || false;

            this.renderVideos();
        } catch (err) {
            Toast.show(err.message, 'error');
            this.showEmpty();
        } finally {
            this.loading = false;
            this.hideLoading();
        }
    },

    async loadMore() {
        if (this.loading || !this.hasMore) return;

        this.loading = true;
        const btn = document.querySelector('#dy-recommend-loadmore button');
        if (btn) {
            btn.disabled = true;
            btn.textContent = '加载中...';
        }

        try {
            const res = await fetch(`/api/douyin/feed?count=18&cursor=${this.cursor}`);
            const data = await res.json();

            if (data.error) {
                throw new Error(data.error);
            }

            const videos = data.aweme_list || [];
            this.videos = [...this.videos, ...videos];
            this.cursor = data.max_cursor || 0;
            this.hasMore = data.has_more || false;

            this.renderVideos();
        } catch (err) {
            Toast.show(err.message, 'error');
        } finally {
            this.loading = false;
            if (btn) {
                btn.disabled = false;
                btn.textContent = '加载更多';
            }
        }
    },

    async refresh() {
        this.videos = [];
        this.cursor = 0;
        this.hasMore = true;
        await this.loadFeed();
    },

    renderVideos() {
        const grid = document.getElementById('dy-recommend-grid');
        const loadmore = document.getElementById('dy-recommend-loadmore');
        const empty = document.getElementById('dy-recommend-empty');

        if (this.videos.length === 0) {
            grid.style.display = 'none';
            loadmore.style.display = 'none';
            empty.style.display = 'block';
            return;
        }

        empty.style.display = 'none';
        grid.style.display = 'grid';

        grid.innerHTML = this.videos.map(video => this.renderVideoCard(video)).join('');

        loadmore.style.display = this.hasMore ? 'block' : 'none';
        this.updateHeaderActions();
        this.updateDownloadButton();
    },

    renderVideoCard(video) {
        const title = video.desc || '无标题';
        const cover = video.video?.cover?.url_list?.[0] || '';
        const author = video.author?.nickname || '未知作者';
        const avatar = video.author?.avatar_thumb?.url_list?.[0] || '';
        const likes = this.formatNumber(video.statistics?.digg_count || 0);
        const comments = this.formatNumber(video.statistics?.comment_count || 0);
        const awemeId = video.aweme_id;

        return `
            <div class="video-card" style="border-radius: 12px; overflow: hidden; background: var(--bg-secondary); transition: transform 0.3s, box-shadow 0.3s; cursor: pointer;" onmouseenter="this.style.transform='translateY(-4px)'; this.style.boxShadow='0 8px 24px rgba(0,0,0,0.15)';" onmouseleave="this.style.transform=''; this.style.boxShadow='';" onclick="DyRecommendPage.handleCardClick(event, '${awemeId}')">
                <div style="position: relative; padding-top: 56.25%; background: var(--bg-body);">
                    <img src="${cover}" alt="${title}" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover;" onerror="this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22400%22 height=%22300%22%3E%3Crect fill=%22%23f0f0f0%22 width=%22400%22 height=%22300%22/%3E%3C/svg%3E'">
                    <input type="checkbox" id="dy-recommend-check-${awemeId}" class="dy-recommend-checkbox" style="position: absolute; top: 8px; left: 8px; width: 18px; height: 18px; cursor: pointer; z-index: 5; display: ${this.isSelectMode ? 'block' : 'none'};" onclick="event.stopPropagation(); DyRecommendPage.updateDownloadButton();" />
                    <div style="position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,0.6); color: white; padding: 4px 8px; border-radius: 4px; font-size: 0.75rem;">
                        ${this.formatDuration(video.video?.duration || 0)}
                    </div>
                </div>
                <div style="padding: var(--spacing-md);">
                    <h3 style="font-size: 0.95rem; margin-bottom: 8px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; line-height: 1.4; color: #ffffff;">${title}</h3>
                    <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 12px;">
                        <img src="${avatar}" alt="${author}" style="width: 24px; height: 24px; border-radius: 50%;" onerror="this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2224%22 height=%2224%22%3E%3Ccircle fill=%22%23ddd%22 cx=%2212%22 cy=%2212%22 r=%2212%22/%3E%3C/svg%3E'">
                        <span style="font-size: 0.85rem; color: var(--text-muted);">${author}</span>
                    </div>
                    <div style="display: flex; gap: var(--spacing-md); font-size: 0.85rem; color: var(--text-muted); margin-bottom: 12px;">
                        <span>❤️ ${likes}</span>
                        <span>💬 ${comments}</span>
                    </div>
                    <button class="btn btn-primary btn-sm" onclick="DyRecommendPage.downloadVideo('${awemeId}')" style="width: 100%;">下载视频</button>
                </div>
            </div>
        `;
    },

    async downloadVideo(awemeId) {
        const videoObj = this.videos.find(v => v.aweme_id === awemeId);
        if (!videoObj) {
            Toast.show('找不到该视频的数据', 'error');
            return;
        }
        try {
            Toast.show('已加入下载队列...', 'info');
            const res = await fetch('/api/douyin/download-batch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ items: [videoObj] })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            Toast.show('下载已在后台启动！', 'success');
            Router.navigate('dy_parse');
        } catch (err) {
            Toast.show(err.message, 'error');
        }
    },

    showLoading() {
        document.getElementById('dy-recommend-loading').style.display = 'block';
        document.getElementById('dy-recommend-grid').style.display = 'none';
        document.getElementById('dy-recommend-empty').style.display = 'none';
    },

    hideLoading() {
        document.getElementById('dy-recommend-loading').style.display = 'none';
    },

    showEmpty() {
        document.getElementById('dy-recommend-empty').style.display = 'block';
        document.getElementById('dy-recommend-grid').style.display = 'none';
    },

    formatNumber(num) {
        if (num >= 10000) {
            return (num / 10000).toFixed(1) + 'w';
        }
        return num.toString();
    },

    formatDuration(ms) {
        let seconds = Math.floor((ms || 0) / 1000);
        const hrs = Math.floor(seconds / 3600);
        seconds = seconds % 3600;
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;

        if (hrs > 0) {
            return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        }
        return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    },

    handleCardClick(event, awemeId) {
        if (event.target.tagName === 'BUTTON' || event.target.tagName === 'INPUT') return;
        if (this.isSelectMode) {
            this.toggleSelection(awemeId);
        } else {
            window.open(`https://www.douyin.com/video/${awemeId}`, '_blank');
        }
    },

    enterSelectMode() {
        this.isSelectMode = true;
        document.querySelectorAll('.dy-recommend-checkbox').forEach(cb => cb.style.display = 'block');
        this.updateHeaderActions();
        this.updateDownloadButton();
    },

    exitSelectMode() {
        this.isSelectMode = false;
        document.querySelectorAll('.dy-recommend-checkbox').forEach(cb => {
            cb.checked = false;
            cb.style.display = 'none';
        });
        this.updateHeaderActions();
    },

    updateHeaderActions() {
        const container = document.getElementById('dy-recommend-header-actions');
        if (!container) return;

        if (this.isSelectMode) {
            container.innerHTML = `
                <button class="btn btn-secondary btn-sm" id="dy-recommend-select-all-btn" onclick="DyRecommendPage.toggleSelectAll()" style="padding: 6px 12px; font-size: 0.85rem;">全选</button>
                <button class="btn btn-primary" onclick="DyRecommendPage.downloadSelected()" id="dy-recommend-download-btn" disabled>
                    <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px; display: inline-block; vertical-align: text-bottom;">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    开始下载 (0)
                </button>
                <button class="btn btn-secondary" onclick="DyRecommendPage.exitSelectMode()">
                    取消批量下载
                </button>
            `;
        } else {
            container.innerHTML = `
                <button class="btn btn-primary" onclick="DyRecommendPage.enterSelectMode()">
                    <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px; display: inline-block; vertical-align: text-bottom;">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    批量下载
                </button>
                <button class="btn btn-secondary" onclick="DyRecommendPage.refresh()" id="dy-recommend-refresh">
                    <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px;">
                        <polyline points="23 4 23 10 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    刷新
                </button>
            `;
        }
    },

    toggleSelectAll() {
        const checkboxes = document.querySelectorAll('.dy-recommend-checkbox');
        const selected = this.getSelectedVideos();
        const shouldSelectAll = selected.length < checkboxes.length;

        checkboxes.forEach(cb => cb.checked = shouldSelectAll);
        this.updateDownloadButton();
    },

    toggleSelection(awemeId) {
        const checkbox = document.getElementById(`dy-recommend-check-${awemeId}`);
        if (checkbox) {
            checkbox.checked = !checkbox.checked;
            this.updateDownloadButton();
        }
    },

    selectAll() {
        document.querySelectorAll('.dy-recommend-checkbox').forEach(cb => cb.checked = true);
        this.updateDownloadButton();
    },

    deselectAll() {
        document.querySelectorAll('.dy-recommend-checkbox').forEach(cb => cb.checked = false);
        this.updateDownloadButton();
    },

    updateDownloadButton() {
        const downloadBtn = document.getElementById('dy-recommend-download-btn');
        if (!downloadBtn) return;
        const selected = this.getSelectedVideos();
        downloadBtn.disabled = selected.length === 0;

        downloadBtn.innerHTML = `
            <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px; display: inline-block; vertical-align: text-bottom;">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            开始下载 (${selected.length})
        `;

        const selectAllBtn = document.getElementById('dy-recommend-select-all-btn');
        if (selectAllBtn) {
            const checkboxes = document.querySelectorAll('.dy-recommend-checkbox');
            if (checkboxes.length > 0 && selected.length === checkboxes.length) {
                selectAllBtn.textContent = '取消全选';
            } else {
                selectAllBtn.textContent = '全选';
            }
        }
    },

    getSelectedVideos() {
        const selected = [];
        document.querySelectorAll('.dy-recommend-checkbox').forEach(cb => {
            if (cb.checked) {
                const awemeId = cb.id.replace('dy-recommend-check-', '');
                const videoObj = this.videos.find(v => v.aweme_id === awemeId);
                if (videoObj) {
                    selected.push(videoObj);
                }
            }
        });
        return selected;
    },

    async downloadSelected() {
        const selected = this.getSelectedVideos();
        if (selected.length === 0) return;

        const btn = document.getElementById('dy-recommend-download-btn');
        let originalHTML = '';
        if (btn) {
            btn.disabled = true;
            originalHTML = btn.innerHTML;
            btn.textContent = '正在启动...';
        }

        try {
            const res = await fetch('/api/douyin/download-batch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ items: selected })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            Toast.show('批量下载已启动！', 'success');
            this.exitSelectMode();
            Router.navigate('dy_parse'); // 跳转到下载进度页面
        } catch (err) {
            Toast.show(err.message, 'error');
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalHTML;
            }
        }
    },

    destroy() {
        this.videos = [];
    }
};
