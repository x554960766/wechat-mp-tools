/**
 * 视频号下载与在线解析页面组件
 */
const ChannelsPage = {
    currentVideoData: null,
    isParsing: false,
    isDownloading: false,

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">视频号下载</h2>
                <p class="page-description">输入微信中“复制链接”得到的视频号分享网址，解析获取高清无水印无加密视频直链并高速下载到本地</p>
            </div>

            <!-- 主输入卡片 -->
            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="card-header">
                    <h3 class="card-title">🔗 粘贴视频号分享链接</h3>
                </div>
                
                <div class="form-group" style="margin-top: var(--spacing-md);">
                    <div style="display: flex; gap: var(--spacing-sm);">
                        <input type="text" id="channels-url-input" class="form-input" 
                               placeholder="粘贴视频号分享链接，如 https://weixin.qq.com/sph/xxxxx" 
                               style="flex: 1; padding: 12px 16px; font-size: 1rem; border-radius: 12px;"
                               onkeydown="if(event.key==='Enter') ChannelsPage.fetchProfile()">
                        <button class="btn btn-secondary" onclick="ChannelsPage.pasteFromClipboard()" style="padding: 12px 20px; font-weight: 500;">
                            📋 粘贴
                        </button>
                    </div>
                    <div class="form-hint">支持含有视频号链接的混合文本（如微信直接复制的转发消息）。</div>
                </div>

                <div class="btn-group" style="margin-top: var(--spacing-md); display: flex; gap: var(--spacing-sm); flex-wrap: wrap;">
                    <button class="btn btn-primary" id="btn-channels-parse" onclick="ChannelsPage.fetchProfile()" style="min-width: 120px; display: flex; align-items: center; justify-content: center; gap: 8px;">
                        <span>🔍 开始解析</span>
                    </button>
                    <button class="btn btn-secondary" onclick="ChannelsPage.clearInput()">
                        清空
                    </button>
                    <button class="btn btn-secondary" onclick="ChannelsPage.openDownloadDir()">
                        📂 打开下载文件夹
                    </button>
                </div>
            </div>

            <!-- 状态卡片 -->
            <div id="channels-status-container" class="card" style="display: none; padding: var(--spacing-lg); text-align: center; border-left: 4px solid var(--primary); margin-bottom: var(--spacing-lg);">
                <div style="display: flex; align-items: center; justify-content: center; gap: var(--spacing-md);">
                    <div class="spinner" id="channels-status-spinner" style="width: 20px; height: 20px; border-width: 2px;"></div>
                    <div id="channels-status-text" style="font-weight: 500; font-size: 0.95rem;">正在获取解析结果...</div>
                </div>
            </div>

            <!-- 解析结果区 -->
            <div id="channels-result-container" style="display: none;">
                <!-- 动态填充 -->
            </div>
        `;
    },

    destroy() {
        this.currentVideoData = null;
        this.isParsing = false;
        this.isDownloading = false;
    },

    clearInput() {
        const input = document.getElementById('channels-url-input');
        if (input) {
            input.value = '';
            input.focus();
        }
    },

    async pasteFromClipboard() {
        try {
            const text = await navigator.clipboard.readText();
            const input = document.getElementById('channels-url-input');
            if (input) {
                input.value = text.trim();
                Toast.success('已粘贴剪贴板内容');
                input.focus();
            }
        } catch (err) {
            Toast.warning('无法读取剪贴板，请手动粘贴');
        }
    },

    showStatus(msg, isError = false) {
        const container = document.getElementById('channels-status-container');
        const text = document.getElementById('channels-status-text');
        const spinner = document.getElementById('channels-status-spinner');

        if (!container || !text || !spinner) return;

        if (!msg) {
            container.style.display = 'none';
            return;
        }

        container.style.display = 'block';
        text.textContent = msg;

        if (isError) {
            container.style.borderLeftColor = 'var(--error, #e53e3e)';
            text.style.color = 'var(--error, #e53e3e)';
            spinner.style.display = 'none';
        } else {
            container.style.borderLeftColor = 'var(--primary, #07c160)';
            text.style.color = 'var(--text-primary, #1d1d1f)';
            spinner.style.display = 'block';
        }
    },

    async fetchProfile() {
        if (this.isParsing) return;

        const input = document.getElementById('channels-url-input');
        const shareUrl = input?.value.trim();

        if (!shareUrl) {
            Toast.warning('请输入或粘贴视频号链接');
            return;
        }

        const parseBtn = document.getElementById('btn-channels-parse');
        if (parseBtn) {
            parseBtn.disabled = true;
            parseBtn.innerHTML = '<span class="spinner" style="width: 14px; height: 14px; border-width: 2px; display: inline-block; vertical-align: middle; margin-right: 6px;"></span> 正在解析...';
        }

        this.isParsing = true;
        this.showStatus('正在发起远程云端视频解析，请耐心等待...');
        
        const resultContainer = document.getElementById('channels-result-container');
        if (resultContainer) resultContainer.style.display = 'none';

        try {
            const data = await API.channels.fetchVideoProfile(shareUrl);
            this.showStatus(null);
            this.currentVideoData = data;
            this.renderResult(data);
            Toast.success('视频解析成功！');
        } catch (err) {
            this.showStatus(`解析失败: ${err.message || '未知错误'}，请检查链接或稍后重试。`, true);
        } finally {
            this.isParsing = false;
            if (parseBtn) {
                parseBtn.disabled = false;
                parseBtn.innerHTML = '🔍 开始解析';
            }
        }
    },

    renderResult(feed) {
        const container = document.getElementById('channels-result-container');
        if (!container) return;

        const fi = feed.data && feed.data.feedInfo;
        const ai = feed.data && feed.data.authorInfo;

        if (!fi) {
            container.innerHTML = `
                <div class="card" style="padding: var(--spacing-xl); text-align: center;">
                    <p style="color: var(--text-muted);">解析响应中未发现视频元数据，可能链接已失效或不支持解析该类型。</p>
                </div>
            `;
            container.style.display = 'block';
            return;
        }

        const h264Url = fi.h264VideoInfo?.videoUrl || "";
        const h265Url = fi.h265VideoInfo?.videoUrl || "";
        const defaultUrl = fi.videoUrl || "";
        const rawUrl = this.getRawVideoUrl(defaultUrl || h265Url || h264Url);
        const bestVideoUrl = defaultUrl || rawUrl || h265Url || h264Url;
        const coverUrl = fi.coverUrl || "";
        const description = fi.description || "";
        const createtime = fi.createtime || "";

        let html = `
            <div class="card" style="padding: var(--spacing-lg); overflow: hidden;">
                <div style="display: flex; flex-direction: column; gap: var(--spacing-lg);">
                    
                    <!-- 顶层结果标题 -->
                    <div style="border-bottom: 1px solid rgba(0,0,0,0.06); padding-bottom: var(--spacing-sm); display: flex; align-items: center; justify-content: space-between;">
                        <h3 class="card-title" style="margin: 0; color: var(--primary);">✅ 视频解析结果</h3>
                        <span class="badge badge-success" style="font-size: 0.8rem;">已获取无密直链</span>
                    </div>

                    <!-- 布局容器 -->
                    <div style="display: grid; grid-template-columns: minmax(280px, 360px) 1fr; gap: var(--spacing-lg); align-items: start;">
                        
                        <!-- 左侧：视频播放器与封面 -->
                        <div style="display: flex; flex-direction: column; gap: var(--spacing-sm); width: 100%;">
                            ${bestVideoUrl ? `
                                <video src="${this.esc(bestVideoUrl)}" poster="${this.esc(coverUrl)}" controls preload="metadata" 
                                       style="width: 100%; max-height: 480px; border-radius: 16px; background: #000; box-shadow: var(--shadow-md); object-fit: contain;">
                                </video>
                            ` : `
                                <img src="${this.esc(coverUrl)}" alt="Video Cover" 
                                     style="width: 100%; max-height: 480px; border-radius: 16px; object-fit: cover; box-shadow: var(--shadow-md);">
                            `}
                        </div>

                        <!-- 右侧：作者元数据与操作下载 -->
                        <div style="display: flex; flex-direction: column; gap: var(--spacing-md); justify-content: space-between; height: 100%;">
                            
                            <!-- 作者信息 -->
                            <div style="display: flex; flex-direction: column; gap: var(--spacing-sm);">
                                ${ai ? `
                                    <div style="display: flex; align-items: center; gap: var(--spacing-sm);">
                                        <img src="${this.esc(ai.headImgUrl)}" alt="${this.esc(ai.nickname)}" 
                                             style="width: 42px; height: 42px; border-radius: 50%; border: 1.5px solid rgba(0,0,0,0.08);"
                                             onerror="this.style.display='none'">
                                        <div style="display: flex; flex-direction: column;">
                                            <span style="font-weight: 600; color: var(--text-primary); font-size: 1.05rem; display: flex; align-items: center; gap: 4px;">
                                                ${this.esc(ai.nickname)}
                                                ${ai.authIconUrl ? `<img src="${this.esc(ai.authIconUrl)}" style="width:16px;height:16px" onerror="this.style.display='none'">` : ''}
                                            </span>
                                            <span style="font-size: 0.8rem; color: var(--text-muted);">视频号作者</span>
                                        </div>
                                    </div>
                                ` : ''}
                                
                                <!-- 视频描述 -->
                                ${description ? `
                                    <div style="background: rgba(0,0,0,0.02); padding: 12px 16px; border-radius: 12px; font-size: 0.95rem; line-height: 1.6; color: var(--text-primary); border-left: 3px solid var(--primary); margin-top: var(--spacing-xs);">
                                        ${this.esc(description)}
                                    </div>
                                ` : '<div style="color: var(--text-muted); font-style: italic;">该视频无描述内容</div>'}
                            </div>

                            <!-- 数据统计面板 -->
                            <div style="display: flex; gap: var(--spacing-md); flex-wrap: wrap; background: rgba(0,0,0,0.02); padding: var(--spacing-sm) var(--spacing-md); border-radius: 8px; font-size: 0.85rem; color: var(--text-muted);">
                                ${fi.likeCountFmt ? `<span>👍 点赞 <strong>${this.esc(fi.likeCountFmt)}</strong></span>` : ''}
                                ${fi.favCountFmt ? `<span>❤️ 收藏 <strong>${this.esc(fi.favCountFmt)}</strong></span>` : ''}
                                ${fi.forwardCountFmt ? `<span>🔄 转发 <strong>${this.esc(fi.forwardCountFmt)}</strong></span>` : ''}
                                ${fi.commentCountFmt ? `<span>💬 评论 <strong>${this.esc(fi.commentCountFmt)}</strong></span>` : ''}
                            </div>

                            <!-- 极速下载与动作按钮 -->
                            ${bestVideoUrl ? `
                                <div style="display: flex; flex-direction: column; gap: var(--spacing-sm); margin-top: var(--spacing-xs);">
                                    
                                    <!-- 本地高速下载主按钮 (调用 Flask 后端下载，防止 CORS 跨域问题并快速保存) -->
                                    <button class="btn btn-primary" id="btn-server-download" 
                                            onclick="ChannelsPage.downloadOnServer(this)" 
                                            style="padding: 12px; font-size: 1rem; border-radius: 12px; display: flex; align-items: center; justify-content: center; gap: 8px; box-shadow: var(--shadow-sm);">
                                        📥 本地极速下载 (推荐)
                                    </button>

                                    <div style="display: flex; gap: var(--spacing-xs); width: 100%;">
                                        <!-- 备用浏览器直接下载 -->
                                        <button class="btn btn-secondary" id="btn-browser-download"
                                                data-video-url="${this.esc(bestVideoUrl)}" 
                                                data-desc="${this.esc(description)}" 
                                                data-createtime="${this.esc(createtime)}" 
                                                onclick="ChannelsPage.downloadRawBrowser(this)" 
                                                style="flex: 1; padding: 10px; font-size: 0.85rem; border-radius: 8px;">
                                            🌐 浏览器下载
                                        </button>
                                        
                                        <!-- 复制视频直链 -->
                                        <button class="btn btn-secondary" id="btn-copy-url"
                                                data-video-url="${this.esc(bestVideoUrl)}" 
                                                onclick="ChannelsPage.copyUrl(this)" 
                                                style="flex: 1; padding: 10px; font-size: 0.85rem; border-radius: 8px;">
                                            🔗 复制视频直链
                                        </button>
                                    </div>

                                </div>
                            ` : ''}

                        </div>
                    </div>
                    
                    <!-- 本地保存成功后的状态与文件操作面板 -->
                    <div id="download-success-panel" class="card" style="display: none; padding: 16px; background: rgba(7, 193, 96, 0.05); border: 1.5px dashed var(--primary); border-radius: 12px; margin-top: var(--spacing-sm);">
                        <div style="display: flex; flex-direction: column; gap: var(--spacing-sm);">
                            <div style="display: flex; align-items: center; gap: var(--spacing-xs); color: var(--primary); font-weight: 600;">
                                <span>🎉 视频已成功保存到本地电脑！</span>
                            </div>
                            <div style="font-size: 0.9rem; color: var(--text-primary); word-break: break-all;">
                                📁 文件名: <strong id="saved-filename" style="color: #000;">-</strong><br>
                                📍 存放路径: <span id="saved-path" style="color: var(--text-muted); font-size: 0.85rem;">-</span>
                            </div>
                            <div style="display: flex; gap: var(--spacing-sm); margin-top: var(--spacing-xs);">
                                <button class="btn btn-primary btn-sm" id="btn-open-video" onclick="ChannelsPage.openSavedFile()" style="font-size: 0.8rem; padding: 6px 12px; border-radius: 6px;">
                                    🎬 立即播放视频
                                </button>
                                <button class="btn btn-secondary btn-sm" id="btn-open-video-parent" onclick="ChannelsPage.openSavedParent()" style="font-size: 0.8rem; padding: 6px 12px; border-radius: 6px;">
                                    📂 定位到文件夹
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- 原始响应 JSON (展开区域) -->
                    <details style="border-top: 1px solid rgba(0,0,0,0.06); padding-top: var(--spacing-sm); cursor: pointer; margin-top: var(--spacing-sm);">
                        <summary style="font-size: 0.85rem; color: var(--text-muted); padding: 4px 0; outline: none; font-weight: 500;">
                            📄 原始响应 JSON
                        </summary>
                        <pre style="font-size: 0.8rem; line-height: 1.5; overflow-x: auto; padding: 16px; background: rgba(0,0,0,0.03); border-radius: 10px; margin-top: var(--spacing-sm); cursor: text; border: 1px solid rgba(0,0,0,0.05);">${this.esc(JSON.stringify(feed, null, 2))}</pre>
                    </details>

                </div>
            </div>
        `;

        container.innerHTML = html;
        container.style.display = 'block';
    },

    downloadOnServer(btn) {
        const fi = this.currentVideoData?.data?.feedInfo;
        if (!fi) {
            Toast.error('未获取到视频解析结果');
            return;
        }

        const h264Url = fi.h264VideoInfo?.videoUrl || "";
        const h265Url = fi.h265VideoInfo?.videoUrl || "";
        const defaultUrl = fi.videoUrl || "";
        const rawUrl = this.getRawVideoUrl(defaultUrl || h265Url || h264Url);
        const description = fi.description || "";
        const createtime = fi.createtime || "";

        Modal.open({
            title: '📥 选择下载画质',
            content: `
                <div style="padding: 10px 0; text-align: center;">
                    <p style="font-size: 0.95rem; color: var(--text-secondary); margin-bottom: 20px; line-height: 1.5; text-align: left;">
                        正在准备下载视频：<br><strong style="color: var(--text-primary); font-size: 1rem;">${this.esc(description || '无描述')}</strong>
                    </p>
                    <div style="display: flex; flex-direction: column; gap: 10px; max-width: 320px; margin: 0 auto;">
                        ${rawUrl ? `<button class="btn btn-primary" id="q-raw" style="padding: 10px; font-weight: 500; font-size: 0.9rem;">原始视频 (无压缩原画，最高画质)</button>` : ''}
                        ${h265Url ? `<button class="btn btn-secondary" id="q-h265" style="padding: 10px; font-weight: 500; font-size: 0.9rem;">H265 (HEVC) 极高画质 (压缩率高)</button>` : ''}
                        ${h264Url ? `<button class="btn btn-secondary" id="q-h264" style="padding: 10px; font-weight: 500; font-size: 0.9rem;">H264 (AVC) 标准高清画质 (兼容性高)</button>` : ''}
                        ${defaultUrl ? `<button class="btn btn-secondary" id="q-default" style="padding: 10px; font-weight: 500; font-size: 0.9rem;">默认画质</button>` : ''}
                    </div>
                </div>
            `,
            footer: `
                <button class="btn btn-secondary" onclick="Modal.close()" style="font-weight: 500;">取消</button>
            `
        });

        document.getElementById('q-raw')?.addEventListener('click', () => {
            Modal.close();
            this.startDownloadFlow(rawUrl, description, createtime);
        });
        document.getElementById('q-h265')?.addEventListener('click', () => {
            Modal.close();
            this.startDownloadFlow(h265Url, description, createtime);
        });
        document.getElementById('q-h264')?.addEventListener('click', () => {
            Modal.close();
            this.startDownloadFlow(h264Url, description, createtime);
        });
        document.getElementById('q-default')?.addEventListener('click', () => {
            Modal.close();
            this.startDownloadFlow(defaultUrl, description, createtime);
        });
    },

    startDownloadFlow(url, description, createtime) {
        const successPanel = document.getElementById('download-success-panel');
        if (successPanel) successPanel.style.display = 'none';

        App.downloadChannelsVideo(url, description, createtime, null, (res) => {
            if (successPanel) {
                document.getElementById('saved-filename').textContent = res.filename;
                document.getElementById('saved-path').textContent = res.path;
                this.savedFilePath = res.path;
                successPanel.style.display = 'block';
                successPanel.scrollIntoView({ behavior: 'smooth' });
            }
        });
    },

    async downloadRawBrowser(btn) {
        const url = btn.getAttribute('data-video-url');
        const desc = btn.getAttribute('data-desc');
        const createtime = btn.getAttribute('data-createtime');

        if (!url) {
            Toast.error('下载链接无效');
            return;
        }

        Toast.info('开始唤起浏览器直接下载视频...');
        
        try {
            const rawUrl = this.getRawVideoUrl(url);
            const filename = this.sanitizeFilename(desc, createtime) || "wechat_video.mp4";
            
            const a = document.createElement('a');
            a.href = rawUrl;
            a.target = '_blank';
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            
            Toast.success('已唤起浏览器下载！如无响应请使用“本地极速下载”');
        } catch (err) {
            Toast.error('浏览器直接下载唤起失败：' + err.message);
        }
    },

    getRawVideoUrl(url) {
        try {
            const u = new URL(decodeURIComponent(url));
            const filekey = u.searchParams.get("encfilekey");
            const token = u.searchParams.get("token");
            if (filekey && token) {
                const newUrl = new URL(u.origin + u.pathname);
                newUrl.searchParams.set("encfilekey", filekey);
                newUrl.searchParams.set("token", token);
                return newUrl.toString();
            }
        } catch (e) {}
        return url;
    },

    sanitizeFilename(desc, createtime) {
        if (desc) {
            return desc.replace(/[\\/:*?"<>|\r\n]/g, "").trim().slice(0, 100) + ".mp4";
        }
        if (createtime) {
            const d = new Date(Number(createtime) * 1000);
            const pad = (n) => String(n).padStart(2, "0");
            return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}.mp4`;
        }
        return "video.mp4";
    },

    async openSavedFile() {
        if (!this.savedFilePath) return;
        try {
            await API.articles.openFile(this.savedFilePath);
            Toast.success('已启动本地播放器播放视频');
        } catch (err) {
            Toast.error('播放失败，可能由于路径问题');
        }
    },

    async openSavedParent() {
        if (!this.savedFilePath) return;
        try {
            await API.articles.openParent(this.savedFilePath);
            Toast.success('已在系统管理器中定位该视频文件');
        } catch (err) {
            Toast.error('定位文件夹失败');
        }
    },

    async openDownloadDir() {
        try {
            await API.channels.openFolder();
            Toast.success('下载目录已打开');
        } catch (err) {
            Toast.error('打开下载目录失败');
        }
    },

    async copyUrl(btn) {
        const url = btn.getAttribute('data-video-url');
        if (!url) return;
        
        try {
            await navigator.clipboard.writeText(url);
            Toast.success('视频无密直链已复制到剪贴板！');
        } catch (err) {
            Toast.warning('复制直链失败，请手动选择复制');
        }
    },

    esc(s) {
        if (!s) return "";
        const div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
    }
};
