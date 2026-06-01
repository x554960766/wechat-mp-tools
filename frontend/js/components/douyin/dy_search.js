const DySearchPage = {
    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">搜索用户</h2>
                <p class="page-description">通过关键词或抖音号查询创作者并进入主页</p>
            </div>
            
            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="search-box" style="max-width: 100%; display: flex; gap: var(--spacing-md);">
                    <div style="position: relative; flex: 1;">
                        <svg class="search-icon" viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2"/><line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2"/></svg>
                        <input type="text" id="dy-search-input" class="form-input" placeholder="输入用户名、抖音号或主页链接..." style="width: 100%;" onkeydown="if(event.key === 'Enter') DySearchPage.doSearch()">
                    </div>
                    <button class="btn btn-primary" onclick="DySearchPage.doSearch()" id="dy-search-btn">搜索</button>
                </div>
            </div>

            <div id="dy-search-results" class="card-grid" style="display: none;"></div>
            
            <div id="dy-search-empty" class="empty-state">
                <svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <div class="empty-state-title">暂无数据</div>
                <div class="empty-state-desc">请输入关键词进行搜索</div>
            </div>
        `;
    },
    async init() {
        document.getElementById('dy-search-input').focus();
    },
    async doSearch() {
        const keyword = document.getElementById('dy-search-input').value.trim();
        if (!keyword) {
            Toast.show('请输入搜索内容', 'warning');
            return;
        }

        const btn = document.getElementById('dy-search-btn');
        btn.disabled = true;
        btn.textContent = '搜索中...';

        try {
            const res = await fetch(`/api/douyin/search?keyword=${encodeURIComponent(keyword)}`);
            const data = await res.json();
            
            if (data.error) throw new Error(data.error);

            this.renderResults(data);
        } catch (err) {
            Toast.show(err.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '搜索';
        }
    },
    renderResults(data) {
        const container = document.getElementById('dy-search-results');
        const empty = document.getElementById('dy-search-empty');
        
        container.innerHTML = '';
        
        // Handle Rust struct response (we might need to adapt to the Python response format here if it diverges, but assuming JSON parity for now)
        // Python search_user returns direct API JSON, we need to parse it.
        const users = (data.user_list || []).map(item => item.user_info || item);
        
        if (users.length === 0) {
            container.style.display = 'none';
            empty.style.display = 'block';
            empty.querySelector('.empty-state-desc').textContent = '未找到相关用户，请更换关键词。注意：搜索功能可能需要登录。';
            return;
        }

        empty.style.display = 'none';
        container.style.display = 'grid';

        users.forEach(user => {
            const avatar = (user.avatar_thumb && user.avatar_thumb.url_list && user.avatar_thumb.url_list[0]) || '';
            const nickname = user.nickname || '未知用户';
            const signature = user.signature || '暂无签名';
            const sec_uid = user.sec_uid;
            
            const card = document.createElement('div');
            card.className = 'card';
            card.style.cursor = 'pointer';
            card.onclick = () => {
                // Navigate to user detail page and pass sec_uid via hash params or global state
                window.location.hash = `#dy_user?sec_uid=${sec_uid}`;
            };
            
            card.innerHTML = `
                <div style="display: flex; gap: 16px; align-items: center;">
                    <img src="${avatar}" style="width: 64px; height: 64px; border-radius: 50%; object-fit: cover; background: var(--bg-input);">
                    <div style="flex: 1; overflow: hidden;">
                        <h3 style="font-size: 1.1rem; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${nickname}</h3>
                        <p style="font-size: 0.85rem; color: var(--text-muted); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">${signature}</p>
                    </div>
                </div>
                <div style="display: flex; gap: 16px; margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border-color);">
                    <div style="flex: 1; text-align: center;">
                        <div style="font-size: 1.1rem; font-weight: 600; color: var(--text-primary);">${user.aweme_count || 0}</div>
                        <div style="font-size: 0.75rem; color: var(--text-muted);">作品</div>
                    </div>
                    <div style="flex: 1; text-align: center;">
                        <div style="font-size: 1.1rem; font-weight: 600; color: var(--text-primary);">${user.follower_count || 0}</div>
                        <div style="font-size: 0.75rem; color: var(--text-muted);">粉丝</div>
                    </div>
                    <div style="flex: 1; text-align: center;">
                        <div style="font-size: 1.1rem; font-weight: 600; color: var(--text-primary);">${user.total_favorited || 0}</div>
                        <div style="font-size: 0.75rem; color: var(--text-muted);">获赞</div>
                    </div>
                </div>
            `;
            container.appendChild(card);
        });
    }
};