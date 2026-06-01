const DyDashboardPage = {
    render() {
        return `
            <div class="page-header" style="text-align: center; margin-bottom: var(--spacing-2xl);">
                <div class="logo-icon" style="margin: 0 auto 16px; width: 60px; height: 60px; border-radius: 20px;">
                    <svg viewBox="0 0 24 24" fill="none" style="width: 32px; height: 32px;"><path d="M9 18V5l12-2v13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="6" cy="18" r="3" stroke="currentColor" stroke-width="2"/><circle cx="18" cy="16" r="3" stroke="currentColor" stroke-width="2"/></svg>
                </div>
                <h1 class="page-title" style="font-size: 2.5rem; justify-content: center;">Douyin Downloader</h1>
                <p class="page-description" style="font-size: 1.1rem; margin-top: var(--spacing-sm);">搜索用户、粘贴链接、浏览推荐<br>一站式视频解析与下载</p>
            </div>

            <div class="card-grid" style="grid-template-columns: repeat(2, 1fr); max-width: 900px; margin: 0 auto;">
                <div class="card" style="cursor: pointer;" onclick="Router.navigate('dy_search')">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--spacing-md);">
                        <div class="btn-icon" style="background: rgba(245, 87, 108, 0.1); color: #f5576c; display: flex; align-items: center; justify-content: center; width: 48px; height: 48px; border-radius: 14px;">
                            <svg viewBox="0 0 24 24" fill="none" style="width: 24px; height: 24px;"><circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2"/><line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2"/></svg>
                        </div>
                    </div>
                    <h3 style="font-size: 1.2rem; margin-bottom: 4px;">搜索用户</h3>
                    <p style="color: var(--text-muted); font-size: 0.9rem;">通过用户名或抖音号查找创作者</p>
                </div>

                <div class="card" style="cursor: pointer;" onclick="Router.navigate('dy_parse')">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--spacing-md);">
                        <div class="btn-icon" style="background: rgba(102, 126, 234, 0.1); color: var(--primary); display: flex; align-items: center; justify-content: center; width: 48px; height: 48px; border-radius: 14px;">
                            <svg viewBox="0 0 24 24" fill="none" style="width: 24px; height: 24px;"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
                        </div>
                    </div>
                    <h3 style="font-size: 1.2rem; margin-bottom: 4px;">解析链接</h3>
                    <p style="color: var(--text-muted); font-size: 0.9rem;">解析分享链接，一键下载视频</p>
                </div>

                <div class="card" style="cursor: pointer;" onclick="Router.navigate('dy_recommend')">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--spacing-md);">
                        <div class="btn-icon" style="background: rgba(156, 39, 176, 0.1); color: #9c27b0; display: flex; align-items: center; justify-content: center; width: 48px; height: 48px; border-radius: 14px;">
                            <svg viewBox="0 0 24 24" fill="none" style="width: 24px; height: 24px;"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
                        </div>
                    </div>
                    <h3 style="font-size: 1.2rem; margin-bottom: 4px;">推荐视频</h3>
                    <p style="color: var(--text-muted); font-size: 0.9rem;">浏览抖音推荐流内容</p>
                </div>

                <div class="card" style="cursor: pointer;" onclick="Router.navigate('dy_collections')">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--spacing-md);">
                        <div class="btn-icon" style="background: rgba(244, 67, 54, 0.1); color: #f44336; display: flex; align-items: center; justify-content: center; width: 48px; height: 48px; border-radius: 14px;">
                            <svg viewBox="0 0 24 24" fill="none" style="width: 24px; height: 24px;"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
                        </div>
                    </div>
                    <h3 style="font-size: 1.2rem; margin-bottom: 4px;">收藏视频</h3>
                    <p style="color: var(--text-muted); font-size: 0.9rem;">查看账号收藏的视频内容</p>
                </div>
            </div>

            <div style="max-width: 900px; margin: var(--spacing-xl) auto 0; display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--spacing-md);">
                <div class="card" style="text-align: center; padding: var(--spacing-md);">
                    <div style="color: var(--text-muted); font-size: 0.8rem; margin-bottom: 8px;">已下载</div>
                    <div style="font-size: 1.5rem; font-weight: 700; color: var(--text-primary);" id="dy-stat-count">-</div>
                </div>
                <div class="card" style="text-align: center; padding: var(--spacing-md);">
                    <div style="color: var(--text-muted); font-size: 0.8rem; margin-bottom: 8px;">占用空间</div>
                    <div style="font-size: 1.5rem; font-weight: 700; color: var(--text-primary);" id="dy-stat-size">-</div>
                </div>
                <div class="card" style="text-align: center; padding: var(--spacing-md);" onclick="Router.navigate('dy_downloads')" style="cursor:pointer;">
                    <div style="color: var(--text-muted); font-size: 0.8rem; margin-bottom: 8px;">进入管理</div>
                    <div style="font-size: 1.2rem; font-weight: 600; color: var(--primary); margin-top: 8px;">我的下载 &rarr;</div>
                </div>
                <div class="card" style="text-align: center; padding: var(--spacing-md);" onclick="Router.navigate('settings')" style="cursor:pointer;">
                    <div style="color: var(--text-muted); font-size: 0.8rem; margin-bottom: 8px;">抖音登录</div>
                    <div style="font-size: 1.2rem; font-weight: 600; color: var(--success); margin-top: 8px;">设置 &rarr;</div>
                </div>
            </div>
        `;
    },
    async init() {
        // Here we could fetch real stats from backend/history
        try {
            const res = await fetch('/api/douyin/history');
            const data = await res.json();
            document.getElementById('dy-stat-count').textContent = data.length + ' 个';
            let size = data.reduce((acc, item) => acc + (item.size_bytes || 0), 0);
            document.getElementById('dy-stat-size').textContent = (size / 1024 / 1024).toFixed(1) + ' MB';
        } catch(e) {}
    }
};