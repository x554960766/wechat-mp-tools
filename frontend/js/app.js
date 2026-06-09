/**
 * 微信公众号文章下载管理工具 — 全局应用管理器
 */
const App = {
    async init() {
        console.log('App initializing...');

        // 初始化基础组件
        Toast.init();
        Modal.init();

        // 首次加载认证状态（非阻塞，不影响页面首屏渲染速度）
        this.checkAuthStatus();

        // 初始化路由
        Router.init();

        // 初始化侧边栏手风琴折叠菜单
        this.initSidebarAccordion();

        // 启动定时状态检查 (每 30 秒检查一次登录态)
        setInterval(() => this.checkAuthStatus(), 30000);
    },

    async checkAuthStatus() {
        try {
            // 微信登录状态
            const wechatData = await API.auth.status();
            
            // 抖音登录状态
            const dyRes = await fetch('/api/douyin-auth/status');
            const dyText = await dyRes.text();
            let dyData = {};
            try {
                dyData = JSON.parse(dyText);
            } catch (e) {
                dyData = { logged_in: false };
            }
            
            const prevLoggedIn = this.isDouyinLoggedIn;
            this.isDouyinLoggedIn = !!dyData.logged_in;
            this.douyinAccountInfo = dyData.account_info || null;
            
            this.updateLoginStatus(
                wechatData.logged_in, 
                wechatData.expired || wechatData.may_expired,
                this.isDouyinLoggedIn
            );

            if (this.isDouyinLoggedIn && !prevLoggedIn) {
                const promptEl = document.getElementById('dy-login-prompt-page');
                if (promptEl && promptEl.style.display === 'block') {
                    promptEl.style.display = 'none';
                    Router.refreshCurrent();
                }
            }
        } catch (err) {
            console.error('Failed to fetch auth status:', err);
            this.updateLoginStatus(false, false, false);
        }
    },

    updateLoginStatus(loggedIn, mayExpired = false, dyLoggedIn = false) {
        const wechatDot = document.getElementById('login-status-dot');
        const wechatIndicator = document.getElementById('status-indicator');
        const wechatText = document.getElementById('status-text');

        const dyIndicator = document.getElementById('dy-status-indicator');
        const dyText = document.getElementById('dy-status-text');

        // 更新微信状态显示
        if (loggedIn) {
            if (mayExpired) {
                if (wechatIndicator) wechatIndicator.className = 'status-dot expired';
                if (wechatText) wechatText.textContent = '登录过期';
                if (wechatDot) wechatDot.style.backgroundColor = 'var(--warning)';
            } else {
                if (wechatIndicator) wechatIndicator.className = 'status-dot online';
                if (wechatText) wechatText.textContent = '已登录';
                if (wechatDot) wechatDot.style.backgroundColor = 'var(--success)';
            }
        } else {
            if (wechatIndicator) wechatIndicator.className = 'status-dot offline';
            if (wechatText) wechatText.textContent = '未登录';
            if (wechatDot) wechatDot.style.backgroundColor = 'transparent';
        }

        // 更新抖音状态显示
        if (dyLoggedIn) {
            if (dyIndicator) dyIndicator.className = 'status-dot online';
            if (dyText) dyText.textContent = 'Cookie 已配置';
        } else {
            if (dyIndicator) dyIndicator.className = 'status-dot warning';
            if (dyText) dyText.textContent = '需要登录 Cookie';
        }
    },

    initSidebarAccordion() {
        const headers = document.querySelectorAll('.nav-group-title');
        headers.forEach(header => {
            header.addEventListener('click', () => {
                const group = header.getAttribute('data-group');
                if (group) {
                    this.toggleNavGroup(group);
                }
            });
        });
    },

    toggleNavGroup(targetGroup) {
        const groups = ['wechat', 'wechat_channels', 'douyin', 'common'];
        groups.forEach(g => {
            const itemsEl = document.getElementById(`items-${g}`);
            const titleEl = document.querySelector(`.nav-group-title[data-group="${g}"]`);
            const arrowEl = titleEl ? titleEl.querySelector('.group-arrow') : null;
            
            if (g === targetGroup) {
                if (itemsEl) {
                    const isCollapsed = itemsEl.classList.toggle('collapsed');
                    if (arrowEl) {
                        arrowEl.textContent = isCollapsed ? '▶' : '▼';
                    }
                }
            } else {
                if (itemsEl) {
                    itemsEl.classList.add('collapsed');
                    if (arrowEl) {
                        arrowEl.textContent = '▶';
                    }
                }
            }
        });
    },

    async downloadChannelsVideo(videoUrl, description, createtime, decryptKey, onSuccessCallback) {
        if (!videoUrl) {
            Toast.error('下载链接无效');
            return;
        }

        let taskId = null;
        let isDownloading = true;

        Modal.open({
            title: '📥 正在下载视频到本地',
            content: `
                <div style="padding: 10px 0;">
                    <p style="font-size: 0.95rem; color: var(--text-secondary); margin-bottom: 20px; line-height: 1.5; word-break: break-all;">
                        视频: <strong style="color: var(--text-primary); font-size: 1rem;">${this.esc(description || '视频号视频')}</strong>
                    </p>
                    <div style="background: #eee; border-radius: 8px; height: 16px; overflow: hidden; margin-bottom: 12px; position: relative;">
                        <div id="single-download-progress-bar" style="background: var(--primary); height: 100%; width: 0%; transition: width 0.3s; border-radius: 8px;"></div>
                    </div>
                    <div style="display: flex; justify-content: space-between; font-size: 0.9rem; margin-bottom: var(--spacing-md);">
                        <span id="single-download-progress-text" style="font-weight: 500; color: var(--text-primary);">正在连接视频服务器...</span>
                        <span id="single-download-progress-percent" style="font-weight: 600; color: var(--primary);">0%</span>
                    </div>
                </div>
            `,
            footer: `
                <button class="btn btn-secondary" id="btn-single-download-cancel" style="background: #ff3b30; color: white; border-color: rgba(255,59,48,0.2); font-weight: 500;">终止下载</button>
            `,
            onClose: () => {
                isDownloading = false;
                if (taskId) {
                    API.channels.cancelDownload(taskId).catch(e => console.error("Cancel failed:", e));
                }
            }
        });

        try {
            const startRes = await API.channels.downloadAsync(videoUrl, description, createtime, decryptKey);
            if (!startRes.success || !startRes.task_id) {
                throw new Error(startRes.error || '无法启动下载任务');
            }
            taskId = startRes.task_id;
        } catch (err) {
            isDownloading = false;
            Modal.close();
            Toast.error('启动下载失败: ' + err.message);
            return;
        }

        const cancelBtn = document.getElementById('btn-single-download-cancel');
        if (cancelBtn) {
            cancelBtn.onclick = async () => {
                cancelBtn.disabled = true;
                cancelBtn.textContent = '正在取消...';
                try {
                    await API.channels.cancelDownload(taskId);
                } catch (e) {
                    console.error(e);
                }
            };
        }

        const pollInterval = setInterval(async () => {
            if (!isDownloading) {
                clearInterval(pollInterval);
                return;
            }

            try {
                const res = await API.channels.getDownloadStatus(taskId);
                if (res.status === 'downloading') {
                    const pct = res.progress || 0;
                    const pb = document.getElementById('single-download-progress-bar');
                    const pt = document.getElementById('single-download-progress-text');
                    const pp = document.getElementById('single-download-progress-percent');
                    if (pb) pb.style.width = `${pct}%`;
                    if (pp) pp.textContent = `${pct}%`;
                    if (pt) pt.textContent = `已下载 ${pct}%`;
                } else if (res.status === 'success') {
                    clearInterval(pollInterval);
                    isDownloading = false;
                    Modal.close();
                    Toast.success('视频下载成功！');
                    if (onSuccessCallback) {
                        onSuccessCallback(res.result);
                    }
                } else if (res.status === 'failed') {
                    clearInterval(pollInterval);
                    isDownloading = false;
                    Modal.close();
                    Toast.error('下载失败: ' + (res.error || '未知错误'));
                } else if (res.status === 'cancelled') {
                    clearInterval(pollInterval);
                    isDownloading = false;
                    Modal.close();
                    Toast.info('下载已取消');
                }
            } catch (err) {
                console.error("Polling error:", err);
            }
        }, 1000);
    },

    esc(s) {
        if (!s) return "";
        const div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
    }
};

// 页面加载完成后启动应用
document.addEventListener('DOMContentLoaded', () => {
    App.init();
});
