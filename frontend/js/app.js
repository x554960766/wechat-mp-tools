/**
 * 微信公众号文章下载管理工具 — 全局应用管理器
 */
const App = {
    ffmpegAvailable: true,
    isDouyinLoggedIn: false,
    douyinAccountInfo: null,
    _updateInfo: null,

    async init() {
        console.log('App initializing...');

        // 初始化基础组件
        Toast.init();
        Modal.init();

        // 首次加载认证状态（非阻塞，不影响页面首屏渲染速度）
        this.checkAuthStatus();

        // 检查 ffmpeg 可用状态
        try {
            await this.checkFFmpegStatus();
        } catch (e) {
            console.error('Failed to run initial checkFFmpegStatus:', e);
        }

        // 初始化路由
        Router.init();

        // 初始化侧边栏手风琴折叠菜单
        this.initSidebarAccordion();

        // 启动定时状态检查 (每 30 秒检查一次登录态)
        setInterval(() => this.checkAuthStatus(), 30000);

        // 延迟 3 秒检查版本更新（非阻塞）
        setTimeout(() => this.checkForUpdates(), 3000);
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
            
            // 小红书登录状态
            let xhsData = { logged_in: false };
            try {
                xhsData = await API.xhs.auth.status();
            } catch (e) {
                // ignore
            }
            
            const prevLoggedIn = this.isDouyinLoggedIn;
            this.isDouyinLoggedIn = !!dyData.logged_in;
            this.douyinAccountInfo = dyData.account_info || null;
            
            this.updateLoginStatus(
                wechatData.logged_in, 
                wechatData.expired || wechatData.may_expired,
                this.isDouyinLoggedIn,
                !!xhsData.logged_in
            );

            if (this.isDouyinLoggedIn && !prevLoggedIn) {
                const promptEl = document.getElementById('dy-login-prompt-page');
                if (promptEl && promptEl.style.display === 'block') {
                    promptEl.style.display = 'none';
                    Router.refreshCurrent();
                }
            }

            // 账号池踢出事件轮询（全局提示）
            try {
                const evData = await API.accountPool.events();
                if (evData.events && evData.events.length > 0) {
                    for (const ev of evData.events) {
                        Toast.warning(`账号【${ev.nickname || '未知'}】${ev.reason}，已被移出账号池`);
                    }
                }
            } catch (e) { /* silent */ }
        } catch (err) {
            console.error('Failed to fetch auth status:', err);
            this.updateLoginStatus(false, false, false);
        }
    },

    async checkFFmpegStatus() {
        try {
            const data = await API.transcode.checkFFmpeg();
            const transcodeNav = document.getElementById('nav-transcode');
            if (data && data.available) {
                if (transcodeNav) transcodeNav.style.display = 'flex';
                this.ffmpegAvailable = true;
            } else {
                if (transcodeNav) transcodeNav.style.display = 'none';
                this.ffmpegAvailable = false;
            }
        } catch (err) {
            console.error('Failed to check ffmpeg status:', err);
            const transcodeNav = document.getElementById('nav-transcode');
            if (transcodeNav) transcodeNav.style.display = 'none';
            this.ffmpegAvailable = false;
        }
    },

    updateLoginStatus(loggedIn, mayExpired = false, dyLoggedIn = false, xhsLoggedIn = false) {
        const wechatDot = document.getElementById('login-status-dot');
        const wechatIndicator = document.getElementById('status-indicator');
        const wechatText = document.getElementById('status-text');

        const dyIndicator = document.getElementById('dy-status-indicator');
        const dyText = document.getElementById('dy-status-text');

        const xhsIndicator = document.getElementById('xhs-status-indicator');
        const xhsText = document.getElementById('xhs-status-text');

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

        // 更新小红书状态显示
        if (xhsLoggedIn) {
            if (xhsIndicator) xhsIndicator.className = 'status-dot online';
            if (xhsText) xhsText.textContent = 'Cookie 已配置';
        } else {
            if (xhsIndicator) xhsIndicator.className = 'status-dot warning';
            if (xhsText) xhsText.textContent = '需要登录 Cookie';
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
        const groups = ['wechat', 'wechat_channels', 'douyin', 'xiaohongshu', 'common'];
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

    async checkForUpdates() {
        try {
            const data = await API.version.check();
            if (data && data.has_update) {
                this._updateInfo = data;
                const dot = document.getElementById('update-dot');
                if (dot) dot.style.display = 'inline-block';
                const versionText = document.getElementById('version-text');
                if (versionText) {
                    versionText.textContent = `Media Tools v${data.current_version} (有新版本)`;
                    versionText.style.color = '#ff9500';
                }
            }
        } catch (e) {
            console.log('Version check skipped:', e.message);
        }
    },

    showUpdateModal() {
        const info = this._updateInfo;
        if (!info || !info.has_update) {
            Modal.open({
                title: '🔄 版本检查',
                content: `
                    <div style="padding: 10px 0; text-align: center;">
                        <p style="font-size: 1.1rem; font-weight: 600; color: var(--success); margin-bottom: 8px;">✅ 已是最新版本</p>
                        <p style="color: var(--text-secondary);">当前版本 v${(info && info.current_version) || '1.0.6'}</p>
                    </div>
                `,
                footer: '<button class="btn btn-primary" onclick="Modal.close()" style="width: 100%;">关闭</button>'
            });
            return;
        }

        const notes = (info.release_notes || '暂无更新说明').replace(/\n/g, '<br>');
        const sizeStr = info.asset_size ? `(${(info.asset_size / 1024 / 1024).toFixed(1)} MB)` : '';

        Modal.open({
            title: '🎉 发现新版本',
            content: `
                <div style="padding: 10px 0;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding: 12px 16px; background: var(--bg-glass); border-radius: var(--radius-md); border: 1px solid var(--border-color);">
                        <div>
                            <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 4px;">当前版本</div>
                            <div style="font-size: 1rem; font-weight: 600;">v${info.current_version}</div>
                        </div>
                        <div style="font-size: 1.2rem; color: var(--text-muted);">→</div>
                        <div>
                            <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 4px;">最新版本</div>
                            <div style="font-size: 1rem; font-weight: 600; color: var(--success);">v${info.latest_version}</div>
                        </div>
                    </div>
                    <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 16px; max-height: 150px; overflow-y: auto; padding: 12px; background: var(--bg-input); border-radius: var(--radius-sm); line-height: 1.6;">
                        <strong style="color: var(--text-primary);">更新说明：</strong><br>${notes}
                    </div>
                    <div id="update-download-area">
                        ${info.download_url ? `<button class="btn btn-primary" onclick="App.startUpdateDownload()" id="btn-start-update" style="width: 100%;">📥 下载更新包 ${sizeStr}</button>` : `<a href="${info.release_url}" target="_blank" class="btn btn-primary" style="width: 100%; text-decoration: none;">前往 GitHub 下载</a>`}
                    </div>
                    <div id="update-progress-area" style="display: none;">
                        <div style="background: var(--bg-input); border-radius: 8px; height: 16px; overflow: hidden; margin-bottom: 12px;">
                            <div id="update-progress-bar" style="background: var(--gradient-primary); height: 100%; width: 0%; transition: width 0.3s; border-radius: 8px;"></div>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem;">
                            <span id="update-progress-text" style="color: var(--text-secondary);">准备下载...</span>
                            <span id="update-progress-pct" style="font-weight: 600; color: var(--primary);">0%</span>
                        </div>
                    </div>
                    <div id="update-done-area" style="display: none; text-align: center;">
                        <p style="color: var(--success); font-weight: 600; margin-bottom: 12px;">✅ 下载完成！</p>
                        <p style="color: var(--text-secondary); font-size: 0.85rem; margin-bottom: 16px;">请关闭当前程序后，解压新版本覆盖旧文件即可完成更新。</p>
                        <button class="btn btn-primary" onclick="App.openUpdateFolder()" style="width: 100%;">📂 打开下载目录</button>
                    </div>
                </div>
            `,
            footer: `
                <div style="display: flex; gap: 8px; width: 100%;">
                    ${info.release_url ? `<a href="${info.release_url}" target="_blank" class="btn btn-secondary" style="flex: 1; text-decoration: none; text-align: center;">GitHub 页面</a>` : ''}
                    <button class="btn btn-secondary" onclick="Modal.close()" style="flex: 1;">关闭</button>
                </div>
            `
        });
    },

    async startUpdateDownload() {
        const info = this._updateInfo;
        if (!info || !info.download_url) return;

        const btn = document.getElementById('btn-start-update');
        if (btn) btn.style.display = 'none';
        const progressArea = document.getElementById('update-progress-area');
        if (progressArea) progressArea.style.display = 'block';

        try {
            await API.version.download(info.download_url);
        } catch (e) {
            Toast.error('启动下载失败: ' + e.message);
            return;
        }

        const pollId = setInterval(async () => {
            try {
                const st = await API.version.progress();
                const bar = document.getElementById('update-progress-bar');
                const text = document.getElementById('update-progress-text');
                const pct = document.getElementById('update-progress-pct');

                if (st.status === 'downloading') {
                    const p = st.progress || 0;
                    if (bar) bar.style.width = `${p}%`;
                    if (pct) pct.textContent = `${p}%`;
                    const dlMB = (st.downloaded / 1024 / 1024).toFixed(1);
                    const totalMB = st.total_size ? (st.total_size / 1024 / 1024).toFixed(1) : '?';
                    if (text) text.textContent = `${dlMB} MB / ${totalMB} MB`;
                } else if (st.status === 'done') {
                    clearInterval(pollId);
                    if (progressArea) progressArea.style.display = 'none';
                    const doneArea = document.getElementById('update-done-area');
                    if (doneArea) doneArea.style.display = 'block';
                } else if (st.status === 'error') {
                    clearInterval(pollId);
                    Toast.error('下载失败: ' + (st.error || '未知错误'));
                    if (progressArea) progressArea.style.display = 'none';
                    const downloadArea = document.getElementById('update-download-area');
                    if (downloadArea) downloadArea.innerHTML = `<button class="btn btn-primary" onclick="App.startUpdateDownload()" style="width: 100%;">🔄 重试下载</button>`;
                }
            } catch (e) {
                console.error('Update progress poll error:', e);
            }
        }, 500);
    },

    async openUpdateFolder() {
        try {
            await API.version.openFolder();
        } catch (e) {
            Toast.error('打开目录失败');
        }
    },

    showDisclaimer() {
        Modal.open({
            title: '免责声明',
            content: `
                <div style="font-size: 0.9rem; line-height: 1.6; color: var(--text-secondary);">
                    <p style="margin-bottom: 12px; font-weight: 500; color: var(--text-primary);">在使用本工具前，请仔细阅读以下免责声明：</p>
                    <p style="margin-top: 8px; margin-bottom: 8px; text-indent: -1.2em; padding-left: 1.2em;">1. 本项目所有功能仅用于个人学习、研究与本地备份，请勿用于任何商业用途或非法牟利。</p>
                    <p style="margin-top: 8px; margin-bottom: 8px; text-indent: -1.2em; padding-left: 1.2em;">2. 使用本工具下载资源时需遵守平台的用户服务协议及相关法律法规。用户因滥用本工具造成的账号风控、限制或法律纠纷，由用户本人承担，与本项目作者无关。</p>
                    <p style="margin-top: 8px; margin-bottom: 8px; text-indent: -1.2em; padding-left: 1.2em;">3. 本项目为开源软件，不提供任何形式的担保或售后承诺。</p>
                </div>
            `,
            footer: '<button class="btn btn-primary" onclick="Modal.close()" style="width: 100%;">我已阅知</button>'
        });
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
