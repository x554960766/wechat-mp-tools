/**
 * 小红书链接下载组件
 */
const XhsDownloadPage = {
    _pollTimer: null,

    render() {
        return `
            <div class="page-header">
                <div>
                    <h2 class="page-title">小红书链接下载</h2>
                    <p class="page-description">直接粘贴小红书分享文本或笔记链接进行解析并提取下载无水印视频/图片资源。</p>
                </div>
            </div>

            <div class="card" style="margin-bottom: 24px; padding: 20px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px;">
                <h3 style="margin-bottom: 12px; font-size: 1.1rem; color: var(--text-primary);">粘贴链接进行下载</h3>
                <div style="margin-bottom: 16px;">
                    <textarea id="xhs-urls-input" class="form-control" rows="6" placeholder="支持每行一个链接，例如：
https://www.xiaohongshu.com/explore/65f1a2b3...
也支持直接粘贴带分享文案的文字口令：
“39 复制打开小红书网页版，或者复制这整段话，打开小红书App... http://xhslink.com/xxxxxx”" style="width: 100%; padding: 12px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text-primary); resize: vertical; line-height: 1.5; font-size: 0.9rem;"></textarea>
                </div>
                
                <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                    <button class="btn btn-primary" id="btn-xhs-start-download" onclick="XhsDownloadPage.startDownload()">📥 开始批量下载</button>
                    <button class="btn btn-secondary" id="btn-xhs-parse-preview" onclick="XhsDownloadPage.parsePreview()">🔍 解析首个链接预览</button>
                </div>
            </div>

            <div id="xhs-download-preview-container" style="margin-bottom: 24px; display: none;"></div>
        `;
    },

    async init() {
        this.destroy();
    },

    destroy() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    },

    async parsePreview() {
        const text = document.getElementById('xhs-urls-input').value.trim();
        const previewBtn = document.getElementById('btn-xhs-parse-preview');
        const previewContainer = document.getElementById('xhs-download-preview-container');
        
        if (!text) {
            Toast.error('请输入或粘贴小红书链接');
            return;
        }

        if (previewBtn) {
            previewBtn.disabled = true;
            previewBtn.innerHTML = '<div class="spinner" style="width: 14px; height: 14px; border-width: 2px;"></div> 解析中...';
        }

        try {
            const res = await API.xhs.parse(text);
            
            previewContainer.style.display = 'block';
            
            const isVideo = res.type === '视频';
            const icon = isVideo ? '🎬' : '📸';
            const countLabel = isVideo ? '无水印视频' : `图集 (共 ${res.images.length} 张图片)`;
            const tagsHtml = (res.tags || []).map(t => `<span style="background:var(--bg-tertiary);color:var(--text-secondary);padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:500;">#${this._esc(t)}</span>`).join(' ');

            previewContainer.innerHTML = `
                <div style="background: var(--bg-card); border: 1px solid var(--primary); border-radius: 12px; padding: 20px; display: flex; flex-direction: column; gap: 16px; box-shadow: var(--shadow-sm);">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                        <h4 style="margin: 0; color: var(--primary); font-size: 1rem; font-weight: 700;">🔍 笔记解析预览</h4>
                        <button class="btn btn-secondary btn-sm" onclick="XhsDownloadPage.closePreview()" style="padding: 2px 8px; font-size: 0.75rem;">关闭</button>
                    </div>
                    
                    <div style="display: flex; gap: 16px; align-items: center; flex-wrap: wrap;">
                        <div style="width: 100px; height: 130px; background: var(--bg-tertiary); border-radius: 8px; overflow: hidden; flex-shrink: 0; position: relative;">
                            <img src="${res.cover}" style="width:100%; height:100%; object-fit: cover;" onerror="this.src='/img/default-cover.png'" />
                            <span style="position: absolute; top: 4px; left: 4px; background: rgba(0,0,0,0.6); color: white; padding: 1px 4px; border-radius: 3px; font-size: 0.65rem;">${icon} ${res.type}</span>
                        </div>
                        <div style="flex: 1; min-width: 200px;">
                            <h4 style="margin: 0 0 8px; font-size: 1.05rem; color: var(--text-primary); font-weight: 700; line-height: 1.4;">${this._esc(res.title || '无标题笔记')}</h4>
                            <p style="margin: 0 0 8px; font-size: 0.85rem; color: var(--text-muted);">
                                作者: <strong style="color:var(--text-primary);">${this._esc(res.author.nickname)}</strong> | 发布时间: ${res.publish_time}
                            </p>
                            <p style="margin: 0 0 8px; font-size: 0.85rem; color: var(--text-secondary); line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
                                ${this._esc(res.desc || '暂无描述')}
                            </p>
                            <div style="display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px;">
                                ${tagsHtml}
                            </div>
                            <div style="font-size: 0.8rem; color: var(--primary); font-weight: 600;">
                                📦 可提取内容: ${countLabel} (点赞: ${res.stats.liked} | 收藏: ${res.stats.collected})
                            </div>
                        </div>
                    </div>
                </div>
            `;
        } catch (err) {
            Toast.error('解析预览失败: ' + err.message);
        } finally {
            if (previewBtn) {
                previewBtn.disabled = false;
                previewBtn.innerHTML = '🔍 解析首个链接预览';
            }
        }
    },

    closePreview() {
        const previewContainer = document.getElementById('xhs-download-preview-container');
        if (previewContainer) {
            previewContainer.style.display = 'none';
            previewContainer.innerHTML = '';
        }
    },

    async startDownload() {
        const text = document.getElementById('xhs-urls-input').value.trim();
        if (!text) {
            Toast.error('请输入小红书链接');
            return;
        }

        const btn = document.getElementById('btn-xhs-start-download');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<div class="spinner" style="width: 16px; height: 16px; border-width: 2px;"></div> 初始化下载...';
        }

        try {
            const res = await API.xhs.download(text);
            if (res.task_id) {
                this.closePreview();
                this.showProgressModal(res.task_id, res.count);
            } else {
                throw new Error('启动任务失败');
            }
        } catch (err) {
            Toast.error('启动下载失败: ' + err.message);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = '📥 开始批量下载';
            }
        }
    },

    showProgressModal(taskId, total) {
        let isDownloading = true;

        Modal.open({
            title: '📥 正在下载小红书笔记',
            content: `
                <div style="padding: 10px 0;">
                    <p style="font-size: 0.95rem; color: var(--text-secondary); margin-bottom: 20px;" id="xhs-progress-modal-desc">
                        正在准备下载，共计 <strong style="color: var(--text-primary); font-size: 1.1rem;">${total}</strong> 篇笔记...
                    </p>
                    <div style="background: var(--bg-tertiary); border-radius: 8px; height: 16px; overflow: hidden; margin-bottom: 12px; position: relative;">
                        <div id="xhs-progress-modal-bar" style="background: var(--primary); height: 100%; width: 0%; transition: width 0.3s; border-radius: 8px;"></div>
                    </div>
                    <div style="display: flex; justify-content: space-between; font-size: 0.9rem;">
                        <span id="xhs-progress-modal-text" style="font-weight: 500; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 70%;">准备中...</span>
                        <span id="xhs-progress-modal-percent" style="font-weight: 600; color: var(--primary);">0%</span>
                    </div>
                </div>
            `,
            footer: `
                <button class="btn btn-secondary" id="btn-xhs-progress-cancel" style="background: #ff3b30; color: white; border-color: rgba(255,59,48,0.2);">取消下载</button>
            `,
            onClose: () => {
                isDownloading = false;
                if (this._pollTimer) {
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                }
            }
        });

        const cancelBtn = document.getElementById('btn-xhs-progress-cancel');
        if (cancelBtn) {
            cancelBtn.onclick = async () => {
                cancelBtn.disabled = true;
                cancelBtn.textContent = '正在取消...';
                try {
                    await API.xhs.cancelDownload(taskId);
                } catch (e) {
                    console.error('Cancel task error:', e);
                }
            };
        }

        if (this._pollTimer) clearInterval(this._pollTimer);
        this._pollTimer = setInterval(async () => {
            if (!isDownloading) {
                clearInterval(this._pollTimer);
                return;
            }

            try {
                const status = await API.xhs.downloadStatus(taskId);
                const total_count = status.total || total;
                const completed = status.completed || 0;
                const failed = status.failed || 0;
                const skipped = status.skipped || 0;
                const progress_total = completed + failed + skipped;
                
                const pct = total_count > 0 ? Math.round((progress_total / total_count) * 100) : 0;
                
                const bar = document.getElementById('xhs-progress-modal-bar');
                const percent = document.getElementById('xhs-progress-modal-percent');
                const txt = document.getElementById('xhs-progress-modal-text');
                const desc = document.getElementById('xhs-progress-modal-desc');

                if (bar) bar.style.width = `${pct}%`;
                if (percent) percent.textContent = `${pct}%`;
                if (txt) txt.textContent = status.current || '正在下载...';
                if (desc) {
                    desc.innerHTML = `已下载: <strong style="color:var(--success);">${completed}</strong> 篇 | 失败: <strong style="color:var(--error);">${failed}</strong> 篇 | 跳过: <strong style="color:var(--text-muted);">${skipped}</strong> 篇 (共 ${total_count} 篇)`;
                }

                if (status.status === 'completed' || status.status === 'failed' || status.status === 'cancelled') {
                    isDownloading = false;
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    Modal.close();

                    if (status.status === 'completed') {
                        Toast.success(`📥 下载结束！成功: ${completed}，失败: ${failed}，跳过: ${skipped}`);
                        const urlsInput = document.getElementById('xhs-urls-input');
                        if (urlsInput) urlsInput.value = '';
                    } else if (status.status === 'cancelled') {
                        Toast.info('下载已取消');
                    } else {
                        Toast.error('下载任务失败');
                    }
                }
            } catch (err) {
                console.error("Polling download status error:", err);
            }
        }, 1000);
    },

    _esc(s) {
        if (!s) return '';
        const div = document.createElement('div');
        div.textContent = s;
        return div.innerHTML;
    }
};
