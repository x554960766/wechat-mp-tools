const DyCollectionsPage = {
    videos: [],
    cursor: 0,
    loading: false,
    hasMore: true,

    render() {
        return `
            <div class="page-header">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h2 class="page-title">收藏视频</h2>
                        <p class="page-description">查看账号收藏的视频内容</p>
                    </div>
                    <button class="btn btn-primary" onclick="DyCollectionsPage.refresh()" id="dy-recommend-refresh">
                        <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px;">
                            <polyline points="23 4 23 10 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                        刷新
                    </button>
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
                        <svg viewBox="0 0 24 24" fill="none" style="width: 32px; height: 32px; color: #f44336;">
                            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </div>
                    <p style="font-size: 1.1rem; margin-bottom: 8px;">暂无收藏视频</p>
                    <p style="color: var(--text-muted);">需要登录后才能获取收藏视频</p>
                </div>

                <div id="dy-recommend-loadmore" style="display: none; text-align: center; padding: var(--spacing-lg);">
                    <button class="btn btn-secondary" onclick="DyCollectionsPage.loadMore()">加载更多</button>
                </div>
            </div>
        `;
    },

    async init() {
        this.videos = [];
        this.cursor = 0;
        this.hasMore = true;
        await this.loadFeed();
    },

    async loadFeed() {
        if (this.loading) return;

        this.loading = true;
        this.showLoading();

        try {
            const res = await fetch(`/api/douyin/collected?count=18&cursor=${this.cursor}`);
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
            const res = await fetch(`/api/douyin/collected?count=18&cursor=${this.cursor}`);
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
            <div class="video-card" style="border-radius: 12px; overflow: hidden; background: var(--bg-secondary); transition: transform 0.3s, box-shadow 0.3s; cursor: pointer;" onmouseenter="this.style.transform='translateY(-4px)'; this.style.boxShadow='0 8px 24px rgba(0,0,0,0.15)';" onmouseleave="this.style.transform=''; this.style.boxShadow='';">
                <div style="position: relative; padding-top: 56.25%; background: var(--bg-body);">
                    <img src="${cover}" alt="${title}" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover;" onerror="this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22400%22 height=%22300%22%3E%3Crect fill=%22%23f0f0f0%22 width=%22400%22 height=%22300%22/%3E%3C/svg%3E'">
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
                    <button class="btn btn-primary btn-sm" onclick="DyCollectionsPage.downloadVideo('${awemeId}')" style="width: 100%;">下载视频</button>
                </div>
            </div>
        `;
    },

    async downloadVideo(awemeId) {
        try {
            Toast.show('开始下载...', 'info');
            const res = await fetch('/api/douyin/download-single', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url: awemeId })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            Toast.show('下载完成！', 'success');
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

    formatDuration(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    },

    destroy() {
        this.videos = [];
    }
};
