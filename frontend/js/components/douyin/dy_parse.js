const DyParsePage = {
    detectedData: null,
    pollTimer: null,
    selectedType: 'post', // 互斥单选: live / replay / post / like / mix / story
    lastSavedPath: '',

    render() {
        return `
            <style>
                @keyframes live-pulse {
                    0% { transform: scale(1); opacity: 0.8; }
                    50% { transform: scale(2.2); opacity: 0; }
                    100% { transform: scale(1); opacity: 0; }
                }
                .option-card-live {
                    border: 1.5px solid rgba(239, 68, 68, 0.4) !important;
                    background: rgba(239, 68, 68, 0.04) !important;
                }
                .option-card-live.active {
                    border-color: #ef4444 !important;
                    background: rgba(239, 68, 68, 0.1) !important;
                    box-shadow: 0 0 12px rgba(239, 68, 68, 0.15);
                }
                .option-card-replay {
                    border: 1.5px solid rgba(139, 92, 246, 0.4) !important;
                    background: rgba(139, 92, 246, 0.04) !important;
                }
                .option-card-replay.active {
                    border-color: #8b5cf6 !important;
                    background: rgba(139, 92, 246, 0.1) !important;
                    box-shadow: 0 0 12px rgba(139, 92, 246, 0.15);
                }
            </style>
            <div class="page-header">
                <h2 class="page-title">解析与下载</h2>
                <p class="page-description">粘贴抖音视频/图文、直播回放、直播间、合集、音乐或主页链接进行下载</p>
            </div>
            
            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="form-group" style="margin-bottom: var(--spacing-md);">
                    <label class="form-label">抖音链接</label>
                    <div style="display: flex; gap: var(--spacing-md);">
                        <input type="text" id="dy-url-input" class="form-input" placeholder="请粘贴抖音视频/图文/直播回放/直播间链接 (https://v.douyin.com/... 或 vsdetail/...)" style="flex: 1;" oninput="DyParsePage.onUrlInput()">
                        <button class="btn btn-secondary" onclick="DyParsePage.detectUrl()" id="dy-detect-btn">检测链接</button>
                    </div>
                    <div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px;">
                        <span style="display: inline-flex; align-items: center; gap: 4px; background: rgba(59, 130, 246, 0.1); color: #3b82f6; border: 1px solid rgba(59, 130, 246, 0.25); padding: 4px 10px; border-radius: 16px; font-size: 0.78rem; font-weight: 500;">
                            📹 短视频 / 图文
                        </span>
                        <span style="display: inline-flex; align-items: center; gap: 4px; background: rgba(139, 92, 246, 0.1); color: #8b5cf6; border: 1px solid rgba(139, 92, 246, 0.25); padding: 4px 10px; border-radius: 16px; font-size: 0.78rem; font-weight: 500;">
                            🎬 直播回放 (/vsdetail/ 或 episode/)
                        </span>
                        <span style="display: inline-flex; align-items: center; gap: 4px; background: rgba(239, 68, 68, 0.1); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.25); padding: 4px 10px; border-radius: 16px; font-size: 0.78rem; font-weight: 500;">
                            🔴 实时直播间 (live.douyin.com)
                        </span>
                        <span style="display: inline-flex; align-items: center; gap: 4px; background: rgba(16, 185, 129, 0.1); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.25); padding: 4px 10px; border-radius: 16px; font-size: 0.78rem; font-weight: 500;">
                            📁 视频合集
                        </span>
                        <span style="display: inline-flex; align-items: center; gap: 4px; background: rgba(245, 158, 11, 0.1); color: #f59e0b; border: 1px solid rgba(245, 158, 11, 0.25); padding: 4px 10px; border-radius: 16px; font-size: 0.78rem; font-weight: 500;">
                            👤 博主主页
                        </span>
                    </div>
                </div>

                <div id="dy-detection-status" style="display: none; margin-bottom: var(--spacing-md); padding: var(--spacing-sm); background: rgba(102, 126, 234, 0.08); border-radius: var(--radius-sm); border: 1px solid rgba(102, 126, 234, 0.2); font-size: 0.88rem; color: var(--text-primary); align-items: center; gap: 8px;">
                    <span style="width: 8px; height: 8px; background: #10b981; border-radius: 50%;"></span>
                    <span id="dy-detection-text"></span>
                </div>

                <!-- 博主主页信息卡片 (主页与直播链接均会展示) -->
                <div id="dy-user-preview-card" style="display: none; align-items: center; gap: 16px; padding: 14px; background: var(--bg-card); border-radius: var(--radius-md); margin-bottom: var(--spacing-md); border: 1px solid var(--border-color); box-shadow: var(--shadow-sm);">
                    <img id="dy-user-preview-avatar" src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2256%22 height=%2256%22%3E%3Ccircle fill=%22%23ddd%22 cx=%2228%22 cy=%2228%22 r=%2228%22/%3E%3C/svg%3E" style="width: 56px; height: 56px; border-radius: 50%; object-fit: cover; border: 2px solid var(--border-color); flex-shrink: 0; background: var(--bg-input);" onerror="this.onerror=null; this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2256%22 height=%2256%22%3E%3Ccircle fill=%22%23ddd%22 cx=%2228%22 cy=%2228%22 r=%2228%22/%3E%3C/svg%3E'" />
                    <div style="flex: 1; min-width: 0;">
                        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                            <span id="dy-user-preview-name" style="font-weight: 600; font-size: 1.05rem; color: var(--text-primary);"></span>
                            <span id="dy-user-preview-id" style="font-size: 0.75rem; background: rgba(102, 126, 234, 0.12); color: var(--primary); padding: 2px 8px; border-radius: 4px; font-weight: 500;"></span>
                            <span id="dy-user-live-tag" style="display: none; font-size: 0.75rem; background: rgba(239, 68, 68, 0.15); color: #ef4444; padding: 2px 8px; border-radius: 4px; font-weight: 600;">🔴 正在直播</span>
                        </div>
                        <div id="dy-user-preview-sig" style="font-size: 0.82rem; color: var(--text-muted); margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"></div>
                        <div style="display: flex; gap: 16px; margin-top: 6px; font-size: 0.82rem; color: var(--text-muted);">
                            <span>作品 <strong id="dy-user-preview-works" style="color: var(--text-primary);">0</strong></span>
                            <span>获赞 <strong id="dy-user-preview-likes" style="color: var(--text-primary);">0</strong></span>
                            <span>粉丝 <strong id="dy-user-preview-fans" style="color: var(--text-primary);">0</strong></span>
                        </div>
                    </div>
                </div>

                <!-- 单条/回放卡片 (非博主/非直播链接时展示) -->
                <div id="dy-media-preview-card" style="display: none; align-items: center; gap: 14px; padding: 14px; background: var(--bg-card); border-radius: var(--radius-md); margin-bottom: var(--spacing-md); border: 1px solid var(--border-color); box-shadow: var(--shadow-sm);">
                    <div id="dy-media-preview-thumb-wrap" style="width: 52px; height: 52px; border-radius: var(--radius-sm); overflow: hidden; flex-shrink: 0; display: flex; align-items: center; justify-content: center; background: var(--bg-input);">
                        <img id="dy-media-preview-thumb" src="" style="width: 100%; height: 100%; object-fit: cover; display: none;" />
                        <span id="dy-media-preview-icon" style="font-size: 1.5rem;">📹</span>
                    </div>
                    <div style="flex: 1; min-width: 0;">
                        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                            <span id="dy-media-preview-author" style="font-weight: 600; font-size: 0.95rem; color: var(--text-primary);"></span>
                            <span id="dy-media-preview-badge" style="font-size: 0.75rem; padding: 2px 8px; border-radius: 4px; font-weight: 500;"></span>
                        </div>
                        <div id="dy-media-preview-title" style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"></div>
                    </div>
                </div>

                <div id="dy-config-container" style="display: none; border-top: 1px solid var(--border-color); padding-top: var(--spacing-md); margin-top: var(--spacing-md);">
                    <h3 style="font-size: 1.05rem; margin-top: 0; margin-bottom: var(--spacing-xs); font-weight: 600;">请选择操作类型 (单选)</h3>
                    <p style="font-size: 0.82rem; color: var(--text-muted); margin-bottom: var(--spacing-md);" id="dy-config-desc">请选择需要执行的录制或下载分类。</p>
                    
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: var(--spacing-md); margin-bottom: var(--spacing-md);">
                        <!-- 🔴 直播录制专用单选卡片 (仅直播时展示) -->
                        <div class="option-card option-card-live" id="card-live" onclick="DyParsePage.selectType('live')" style="display: none; cursor: pointer;">
                            <div class="option-card-header">
                                <input type="radio" name="dy_type_option" id="radio-live" value="live" onclick="event.stopPropagation(); DyParsePage.selectType('live')">
                                <span class="option-card-title" style="color: #ef4444; font-weight: 600;">🔴 录制直播</span>
                            </div>
                            <div class="option-card-desc" id="card-live-desc" style="color: rgba(239, 68, 68, 0.85);">实时录制当前直播视频流</div>
                        </div>

                        <!-- 🎬 直播回放专用单选卡片 (仅当主播有真实回放时展示) -->
                        <div class="option-card option-card-replay" id="card-replay" onclick="DyParsePage.selectType('replay')" style="display: none; cursor: pointer;">
                            <div class="option-card-header">
                                <input type="radio" name="dy_type_option" id="radio-replay" value="replay" onclick="event.stopPropagation(); DyParsePage.selectType('replay')">
                                <span class="option-card-title" style="color: #8b5cf6; font-weight: 600;">🎬 直播回放</span>
                            </div>
                            <div class="option-card-desc" style="color: rgba(139, 92, 246, 0.85);">下载博主公开的直播回放</div>
                        </div>

                        <div class="option-card active" id="card-post" onclick="DyParsePage.selectType('post')" style="cursor: pointer;">
                            <div class="option-card-header">
                                <input type="radio" name="dy_type_option" id="radio-post" value="post" checked onclick="event.stopPropagation(); DyParsePage.selectType('post')">
                                <span class="option-card-title">作品</span>
                            </div>
                            <div class="option-card-desc">博主发布的视频与图集</div>
                        </div>
                        <div class="option-card" id="card-like" onclick="DyParsePage.selectType('like')" style="cursor: pointer;">
                            <div class="option-card-header">
                                <input type="radio" name="dy_type_option" id="radio-like" value="like" onclick="event.stopPropagation(); DyParsePage.selectType('like')">
                                <span class="option-card-title">喜欢</span>
                            </div>
                            <div class="option-card-desc">博主公开的点赞视频列表</div>
                        </div>
                        <div class="option-card" id="card-mix" onclick="DyParsePage.selectType('mix')" style="cursor: pointer;">
                            <div class="option-card-header">
                                <input type="radio" name="dy_type_option" id="radio-mix" value="mix" onclick="event.stopPropagation(); DyParsePage.selectType('mix')">
                                <span class="option-card-title">合集</span>
                            </div>
                            <div class="option-card-desc">博主创建的视频合集列表</div>
                        </div>
                        <div class="option-card" id="card-story" onclick="DyParsePage.selectType('story')" style="cursor: pointer;">
                            <div class="option-card-header">
                                <input type="radio" name="dy_type_option" id="radio-story" value="story" onclick="event.stopPropagation(); DyParsePage.selectType('story')">
                                <span class="option-card-title">日常</span>
                            </div>
                            <div class="option-card-desc">博主发布的日常视频</div>
                        </div>
                    </div>

                    <!-- 🎥 直播录制码率 / 画质选择 -->
                    <div id="dy-live-quality-wrap" style="display: none; margin-top: var(--spacing-sm); margin-bottom: var(--spacing-md); padding: 12px 14px; background: rgba(239, 68, 68, 0.04); border: 1px dashed rgba(239, 68, 68, 0.3); border-radius: var(--radius-sm);">
                        <label class="form-label" style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; flex-wrap: wrap; gap: 4px;">
                            <span style="font-weight: 600; color: var(--text-primary);">🎥 直播录制画质 / 码率选择</span>
                            <span style="font-size: 0.78rem; color: #ef4444;">建议选择 720P 高清以节省内存与磁盘空间</span>
                        </label>
                        <select id="dy-live-quality-select" class="form-input" style="width: 100%; font-size: 0.88rem; background: var(--bg-card); cursor: pointer;">
                            <option value="SD2">720P 高清 30帧 (推荐 / 节省内存与磁盘空间)</option>
                            <option value="FULL_HD1">1080P 蓝光 / 原画 (最高画质 / 文件较大)</option>
                            <option value="HD1">720P 超清 60帧 (中高画质)</option>
                            <option value="SD1">540P 标清 (极小体积 / 最省内存与磁盘)</option>
                        </select>
                    </div>
                    
                    <div class="form-group" id="dy-max-pages-wrap" style="margin-bottom: 0;">
                        <label class="form-label">抓取最大页数 (每页18条，填 0 不限制页数)</label>
                        <input type="number" id="dy-max-pages" class="form-input" value="5" min="0" style="width: 200px;">
                    </div>
                </div>

                <!-- 操作按钮容器 (默认隐藏，仅在检测完成链接后展示) -->
                <div style="display: none; justify-content: flex-end; margin-top: var(--spacing-md);" id="dy-download-btn-wrapper">
                    <button class="btn btn-primary" onclick="DyParsePage.startDownload()" id="dy-parse-btn">开始下载</button>
                </div>
            </div>

            <!-- 下载进度 / 直播录制监控卡片 -->
            <div class="card" id="dy-download-status" style="display: none;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--spacing-md); flex-wrap: wrap; gap: 8px;">
                    <h3 style="margin: 0; font-size: 1.1rem;" id="dy-status-title">下载进度</h3>
                    <!-- 📂 打开目录按钮：放置在「直播录制状态/下载进度」同一行最右侧 -->
                    <button class="btn btn-secondary btn-sm" id="dy-open-folder-btn" onclick="DyParsePage.openFolder()" style="display: none; align-items: center; gap: 6px; padding: 5px 12px; font-size: 0.84rem; cursor: pointer; border-radius: var(--radius-sm);">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 14px; height: 14px; display: inline-block;">
                            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
                        </svg>
                        <span>📂 打开目录</span>
                    </button>
                </div>

                <!-- 普通批量/单条进度条 (默认隐藏，按需呈现) -->
                <div id="dy-batch-progress-wrap" style="display: none; align-items: center; gap: var(--spacing-md); margin-bottom: var(--spacing-md); flex-wrap: wrap;">
                    <div style="flex: 1; min-width: 200px; height: 8px; background: var(--bg-input); border-radius: 4px; overflow: hidden;">
                        <div id="dy-progress-bar" style="width: 0%; height: 100%; background: var(--gradient-primary); transition: width 0.3s ease;"></div>
                    </div>
                    <span id="dy-progress-text" style="font-variant-numeric: tabular-nums; font-weight: 600; min-width: 45px;">0%</span>
                </div>

                <!-- 🔴 直播录制专用计时 HUD 仪表盘 -->
                <div id="dy-live-recording-hud" style="display: none; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 20px; background: linear-gradient(135deg, rgba(239, 68, 68, 0.08) 0%, rgba(220, 38, 38, 0.03) 100%); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: var(--radius-md); margin-bottom: var(--spacing-md);">
                    <div style="display: flex; align-items: center; gap: 14px;">
                        <div style="position: relative; width: 14px; height: 14px;">
                            <span style="position: absolute; width: 100%; height: 100%; border-radius: 50%; background: #ef4444; opacity: 0.75; animation: live-pulse 1.5s cubic-bezier(0, 0, 0.2, 1) infinite;"></span>
                            <span style="position: relative; display: block; width: 100%; height: 100%; border-radius: 50%; background: #ef4444;"></span>
                        </div>
                        <div>
                            <div style="font-size: 0.75rem; color: #ef4444; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">LIVE RECORDING</div>
                            <div style="font-size: 1.5rem; font-weight: 700; font-family: monospace; color: var(--text-primary); letter-spacing: 1px;" id="dy-live-timer">00:00:00</div>
                        </div>
                    </div>
                    <div style="text-align: right;">
                        <div style="font-size: 0.8rem; color: var(--text-muted);">已录制文件大小</div>
                        <div style="font-size: 1.15rem; font-weight: 600; color: var(--text-primary); font-family: monospace;" id="dy-live-size">0.00 MB</div>
                    </div>
                </div>

                <div style="display: flex; justify-content: flex-end; margin-bottom: var(--spacing-sm);">
                    <button class="btn btn-secondary btn-sm" onclick="DyParsePage.cancelDownload()" id="dy-cancel-btn" style="padding: 5px 14px; font-size: 0.85rem; height: 32px; display: none; align-items: center; gap: 4px;">
                        <svg viewBox="0 0 24 24" fill="none" style="width: 14px; height: 14px; display: inline-block; vertical-align: text-bottom;">
                            <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                            <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                        </svg>
                        <span id="dy-cancel-btn-text">取消下载</span>
                    </button>
                </div>

                <div id="dy-log-container" style="background: var(--bg-body); border-radius: var(--radius-sm); padding: var(--spacing-sm); height: 200px; overflow-y: auto; font-family: monospace; font-size: 0.85rem; color: var(--text-muted);">
                </div>
            </div>
        `;
    },

    formatNumber(num) {
        if (!num || isNaN(num)) return '0';
        num = Number(num);
        if (num >= 10000) {
            return (num / 10000).toFixed(1) + 'w';
        }
        if (num >= 1000) {
            return (num / 1000).toFixed(1) + 'k';
        }
        return num.toLocaleString();
    },

    formatDuration(sec) {
        if (!sec || isNaN(sec)) sec = 0;
        sec = Math.floor(sec);
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        const s = sec % 60;
        const pad = (n) => n.toString().padStart(2, '0');
        return `${pad(h)}:${pad(m)}:${pad(s)}`;
    },

    formatBytes(bytes) {
        if (!bytes || isNaN(bytes)) return '0.00 MB';
        return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
    },

    async init() {
        this.detectedData = null;
        const btnWrapper = document.getElementById('dy-download-btn-wrapper');
        try {
            const res = await fetch('/api/douyin/progress');
            const data = await res.json();
            
            if (data && (data.status === 'running' || (data.logs && data.logs.length > 0))) {
                document.getElementById('dy-download-status').style.display = 'block';
                const logContainer = document.getElementById('dy-log-container');
                logContainer.innerHTML = data.logs.map(l => `<div style="margin-bottom: 4px;">${l}</div>`).join('');
                logContainer.scrollTop = logContainer.scrollHeight;

                if (data.last_saved_path) {
                    this.lastSavedPath = data.last_saved_path;
                }

                this.updateStatusView(data);

                if (data.status === 'running') {
                    if (btnWrapper) btnWrapper.style.display = 'flex';
                    this.startProgressPolling();
                } else {
                    if (btnWrapper) btnWrapper.style.display = 'none';
                }
            } else {
                document.getElementById('dy-download-status').style.display = 'none';
                if (btnWrapper) btnWrapper.style.display = 'none';
            }
        } catch (e) {
            console.error('检查下载进度失败:', e);
            if (btnWrapper) btnWrapper.style.display = 'none';
        }
    },

    onShow() {
        this.init();
    },

    onUrlInput() {
        this.detectedData = null;
        const btnWrapper = document.getElementById('dy-download-btn-wrapper');
        if (btnWrapper) btnWrapper.style.display = 'none';
        const statusDiv = document.getElementById('dy-detection-status');
        if (statusDiv) statusDiv.style.display = 'none';
        const userCard = document.getElementById('dy-user-preview-card');
        const mediaCard = document.getElementById('dy-media-preview-card');
        if (userCard) userCard.style.display = 'none';
        if (mediaCard) mediaCard.style.display = 'none';
        const configContainer = document.getElementById('dy-config-container');
        if (configContainer) configContainer.style.display = 'none';
        const qualityWrap = document.getElementById('dy-live-quality-wrap');
        if (qualityWrap) qualityWrap.style.display = 'none';
        this.selectedType = 'post';
        this.updateDownloadBtnLabel();
    },

    async detectUrl() {
        const url = document.getElementById('dy-url-input').value.trim();
        if (!url) {
            Toast.show('请填写链接', 'warning');
            return;
        }

        const detectBtn = document.getElementById('dy-detect-btn');
        detectBtn.disabled = true;
        detectBtn.textContent = '检测中...';

        try {
            const res = await fetch('/api/douyin/detect-url', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            this.detectedData = data;

            // 1. 显示识别信息栏
            const statusDiv = document.getElementById('dy-detection-status');
            const statusText = document.getElementById('dy-detection-text');
            statusText.textContent = '已识别：' + data.message;
            statusDiv.style.display = 'flex';

            const userCard = document.getElementById('dy-user-preview-card');
            const mediaCard = document.getElementById('dy-media-preview-card');
            const configContainer = document.getElementById('dy-config-container');
            const cardLive = document.getElementById('card-live');
            const cardReplay = document.getElementById('card-replay');
            const userLiveTag = document.getElementById('dy-user-live-tag');
            const qualityWrap = document.getElementById('dy-live-quality-wrap');
            const qualitySelect = document.getElementById('dy-live-quality-select');
            const btnWrapper = document.getElementById('dy-download-btn-wrapper');

            // 2. 如果是博主主页 或 直播间链接：展示博主详细资料卡片与单选分类面板
            if (data.type === 'user' || data.type === 'live') {
                if (mediaCard) mediaCard.style.display = 'none';

                // 填充博主详细信息
                document.getElementById('dy-user-preview-avatar').src = data.avatar || 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2256%22 height=%2256%22%3E%3Ccircle fill=%22%23ddd%22 cx=%2228%22 cy=%2228%22 r=%2228%22/%3E%3C/svg%3E';
                document.getElementById('dy-user-preview-name').textContent = data.nickname || '未知主播';
                document.getElementById('dy-user-preview-id').textContent = data.unique_id ? `抖音号: ${data.unique_id}` : '';
                document.getElementById('dy-user-preview-id').style.display = data.unique_id ? 'inline-block' : 'none';
                document.getElementById('dy-user-preview-sig').textContent = data.signature || '暂无个性签名';
                document.getElementById('dy-user-preview-works').textContent = this.formatNumber(data.aweme_count);
                document.getElementById('dy-user-preview-likes').textContent = this.formatNumber(data.total_favorited);
                document.getElementById('dy-user-preview-fans').textContent = this.formatNumber(data.follower_count);

                userLiveTag.style.display = data.is_live ? 'inline-block' : 'none';
                userCard.style.display = 'flex';
                configContainer.style.display = 'block';

                // 直播录制卡片：仅在链接为直播间或正在直播时展示
                if (cardLive) {
                    cardLive.style.display = (data.type === 'live' || data.is_live) ? 'block' : 'none';
                }

                // 直播回放卡片：仅在博主真实拥有直播回放作品时展示
                if (cardReplay) {
                    cardReplay.style.display = data.has_replays ? 'block' : 'none';
                }

                if (data.type === 'live') {
                    // 直播链接：显示直播录制卡片，并默认单选「🔴 录制直播」
                    if (qualitySelect && data.live_qualities && data.live_qualities.length > 0) {
                        qualitySelect.innerHTML = data.live_qualities.map(q => {
                            return `<option value="${q.key}" ${q.is_default ? 'selected' : ''}>${this.escapeHtml(q.name)}</option>`;
                        }).join('');
                    }
                    if (qualityWrap) qualityWrap.style.display = 'block';

                    this.selectType('live');
                } else {
                    // 普通博主主页：默认单选「作品」
                    if (qualityWrap) qualityWrap.style.display = 'none';
                    this.selectType('post');
                }
            } else {
                // 单条视频/回放/合集/音乐
                if (userCard) userCard.style.display = 'none';
                configContainer.style.display = 'none';
                if (qualityWrap) qualityWrap.style.display = 'none';

                const authorEl = document.getElementById('dy-media-preview-author');
                const badgeEl = document.getElementById('dy-media-preview-badge');
                const titleEl = document.getElementById('dy-media-preview-title');
                const thumbImg = document.getElementById('dy-media-preview-thumb');
                const iconEl = document.getElementById('dy-media-preview-icon');

                authorEl.textContent = data.nickname || '抖音资源';
                titleEl.textContent = data.title || data.message || '';

                if (data.cover || data.avatar) {
                    thumbImg.src = data.cover || data.avatar;
                    thumbImg.style.display = 'block';
                    iconEl.style.display = 'none';
                } else {
                    thumbImg.style.display = 'none';
                    iconEl.style.display = 'block';
                }

                if (data.type === 'replay') {
                    badgeEl.textContent = '🎬 直播回放';
                    badgeEl.style.background = 'rgba(139, 92, 246, 0.15)';
                    badgeEl.style.color = '#8b5cf6';
                    iconEl.textContent = '🎬';
                } else if (data.type === 'mix') {
                    badgeEl.textContent = '📁 视频合集';
                    badgeEl.style.background = 'rgba(16, 185, 129, 0.15)';
                    badgeEl.style.color = '#10b981';
                    iconEl.textContent = '📁';
                } else {
                    badgeEl.textContent = data.item_type === 'image' ? '🖼️ 图文作品' : '📹 视频作品';
                    badgeEl.style.background = 'rgba(59, 130, 246, 0.15)';
                    badgeEl.style.color = '#3b82f6';
                    iconEl.textContent = data.item_type === 'image' ? '🖼️' : '📹';
                }
                mediaCard.style.display = 'flex';
                this.updateDownloadBtnLabel();
            }

            // 检测完成后显示下载/录制按钮容器
            if (btnWrapper) btnWrapper.style.display = 'flex';

        } catch (err) {
            Toast.show(err.message, 'error');
            this.detectedData = null;
            const btnWrapper = document.getElementById('dy-download-btn-wrapper');
            if (btnWrapper) btnWrapper.style.display = 'none';
        } finally {
            detectBtn.disabled = false;
            detectBtn.textContent = '检测链接';
        }
    },

    // ── 互斥单选切换 ──────────────────────────────────────────
    selectType(type) {
        this.selectedType = type;
        const allTypes = ['live', 'replay', 'post', 'like', 'mix', 'story'];
        allTypes.forEach(t => {
            const card = document.getElementById(`card-${t}`);
            const radio = document.getElementById(`radio-${t}`);
            const isCur = (t === type);
            if (card) {
                if (isCur) card.classList.add('active');
                else card.classList.remove('active');
            }
            if (radio) radio.checked = isCur;
        });

        const maxPagesWrap = document.getElementById('dy-max-pages-wrap');
        if (maxPagesWrap) {
            maxPagesWrap.style.display = (type === 'live') ? 'none' : 'block';
        }

        const qualityWrap = document.getElementById('dy-live-quality-wrap');
        if (qualityWrap) {
            qualityWrap.style.display = (type === 'live') ? 'block' : 'none';
        }

        // 确保切换类型后下载按钮始终可见且文案匹配
        const btnWrapper = document.getElementById('dy-download-btn-wrapper');
        if (btnWrapper) btnWrapper.style.display = 'flex';

        this.updateDownloadBtnLabel();
    },

    updateDownloadBtnLabel(isRunning = false, isLive = false) {
        const parseBtn = document.getElementById('dy-parse-btn');
        if (!parseBtn) return;

        const isLiveMode = isLive || this.selectedType === 'live' || (this.detectedData && this.detectedData.type === 'live' && this.selectedType === 'live');

        if (isRunning && isLiveMode) {
            parseBtn.textContent = '⏹️ 停止录制并保存';
            parseBtn.className = 'btn btn-error';
            parseBtn.disabled = false;
            parseBtn.onclick = () => DyParsePage.cancelDownload();
        } else if (isRunning && !isLiveMode) {
            parseBtn.textContent = '⏳ 下载进行中...';
            parseBtn.className = 'btn btn-primary';
            parseBtn.disabled = true;
            parseBtn.onclick = () => DyParsePage.startDownload();
        } else {
            parseBtn.className = 'btn btn-primary';
            parseBtn.disabled = false;
            parseBtn.onclick = () => DyParsePage.startDownload();

            if (this.selectedType === 'live') {
                parseBtn.textContent = '🔴 开始录制直播';
            } else if (this.selectedType === 'replay') {
                parseBtn.textContent = '🎬 开始下载直播回放 (批量)';
            } else if (this.selectedType === 'post') {
                parseBtn.textContent = '开始下载作品 (批量)';
            } else if (this.selectedType === 'like') {
                parseBtn.textContent = '开始下载喜欢 (批量)';
            } else if (this.selectedType === 'mix') {
                parseBtn.textContent = '开始下载合集 (批量)';
            } else if (this.selectedType === 'story') {
                parseBtn.textContent = '开始下载日常 (批量)';
            } else {
                parseBtn.textContent = '开始下载';
            }
        }
    },

    updateStatusView(data) {
        // 关键修复：严格以后端明确的 task_type === 'live' 作为唯一判据，绝不根据作品标题是否包含「直播」来误判！
        const isLiveTask = (data && data.task_type === 'live');
        const isRunning = (data && data.status === 'running');
        const isEnded = (data && (data.status === 'completed' || data.status === 'cancelled' || data.status === 'idle'));
        const hud = document.getElementById('dy-live-recording-hud');
        const batchWrap = document.getElementById('dy-batch-progress-wrap');
        const statusTitle = document.getElementById('dy-status-title');
        const cancelBtnText = document.getElementById('dy-cancel-btn-text');
        const cancelBtn = document.getElementById('dy-cancel-btn');
        const openFolderBtn = document.getElementById('dy-open-folder-btn');

        if (data && data.last_saved_path) {
            this.lastSavedPath = data.last_saved_path;
        }

        // 打开目录按钮：在有保存路径或任务结束/录制过内容时，展示在标题栏最右侧
        if (openFolderBtn) {
            if (this.lastSavedPath || (data && (data.recorded_size > 0 || data.downloaded_count > 0 || isEnded))) {
                openFolderBtn.style.display = 'inline-flex';
            } else {
                openFolderBtn.style.display = 'none';
            }
        }

        if (isLiveTask) {
            if (hud) hud.style.display = isRunning ? 'flex' : 'none';
            if (batchWrap) batchWrap.style.display = 'none';
            if (statusTitle) statusTitle.textContent = isRunning ? '🔴 直播实时录制中' : '直播录制状态';
            if (cancelBtnText) cancelBtnText.textContent = '停止录制并保存';
            if (cancelBtn) {
                cancelBtn.style.display = isRunning ? 'flex' : 'none';
                cancelBtn.style.color = '#ef4444';
                cancelBtn.style.borderColor = 'rgba(239, 68, 68, 0.5)';
            }

            const timerEl = document.getElementById('dy-live-timer');
            const sizeEl = document.getElementById('dy-live-size');
            if (timerEl) timerEl.textContent = this.formatDuration(data ? (data.duration_seconds || 0) : 0);
            if (sizeEl) sizeEl.textContent = this.formatBytes(data ? (data.recorded_size || 0) : 0);

            this.updateDownloadBtnLabel(isRunning, true);
        } else {
            if (hud) hud.style.display = 'none';
            if (batchWrap) batchWrap.style.display = (data && data.status !== 'idle') ? 'flex' : 'none';
            if (statusTitle) statusTitle.textContent = '下载进度';
            if (cancelBtnText) cancelBtnText.textContent = '取消下载';
            if (cancelBtn) {
                cancelBtn.style.display = isRunning ? 'flex' : 'none';
                cancelBtn.style.color = '';
                cancelBtn.style.borderColor = '';
            }

            let pct = 0;
            let processed = (data ? ((data.downloaded_count || 0) + (data.failed_count || 0)) : 0);
            if (data && data.total > 0) {
                pct = Math.floor((processed / data.total) * 100);
            } else if (data && data.status === 'completed') {
                pct = 100;
            }
            const bar = document.getElementById('dy-progress-bar');
            const text = document.getElementById('dy-progress-text');
            if (bar) bar.style.width = pct + '%';
            if (text) {
                if (data && data.total > 1) {
                    text.textContent = `${data.downloaded_count || 0}/${data.total}`;
                } else {
                    text.textContent = pct + '%';
                }
            }

            this.updateDownloadBtnLabel(isRunning, false);
        }
    },

    async openFolder() {
        try {
            const res = await fetch('/api/douyin/open-folder', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ path: this.lastSavedPath || '' })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            Toast.show(data.message || '已打开所在目录', 'success');
        } catch (err) {
            Toast.show('打开目录失败: ' + err.message, 'error');
        }
    },

    async startDownload() {
        const url = document.getElementById('dy-url-input').value.trim();
        if (!url) {
            Toast.show('请填写链接', 'warning');
            return;
        }

        const btn = document.getElementById('dy-parse-btn');
        btn.disabled = true;
        btn.textContent = '任务启动中...';

        // 1. 如果没有进行链接检测，则先自动调用检测
        if (!this.detectedData) {
            await this.detectUrl();
            if (!this.detectedData) {
                btn.disabled = false;
                this.updateDownloadBtnLabel();
                return;
            }
            // 如果自动检测出是普通博主主页（非直播），则展开配置并停下，让用户确认/选择下载项
            if (this.detectedData.type === 'user' && !this.detectedData.is_live) {
                btn.disabled = false;
                this.updateDownloadBtnLabel();
                Toast.show('已识别博主主页，请在下方选择操作类型后再次点击下载', 'info');
                return;
            }
        }

        const data = this.detectedData;
        const isLiveSelected = (this.selectedType === 'live' || (data && data.type === 'live' && this.selectedType === 'live'));

        // 2. 准备立即显示本地进度卡片与正确视图
        const statusCard = document.getElementById('dy-download-status');
        const logContainer = document.getElementById('dy-log-container');
        statusCard.style.display = 'block';

        if (isLiveSelected) {
            this.updateStatusView({ task_type: 'live', status: 'running', duration_seconds: 0, recorded_size: 0 });
        } else {
            this.updateStatusView({ task_type: 'batch', status: 'running', total: 1, downloaded_count: 0 });
        }
        
        const timestamp = new Date().toLocaleTimeString();
        logContainer.innerHTML = `<div style="margin-bottom: 4px; color: var(--color-primary);">[${timestamp}] 🚀 正在启动任务...</div>`;

        try {
            // A. 如果单选了「🔴 录制直播」：调用直播下载并立即切换为录制 HUD
            if (data.type === 'live' && isLiveSelected) {
                const qualitySelect = document.getElementById('dy-live-quality-select');
                const quality = qualitySelect ? qualitySelect.value : '';

                const res = await fetch('/api/douyin/download-single', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ url, quality })
                });
                const resData = await res.json();
                if (resData.error) throw new Error(resData.error);

                if (resData.save_path) {
                    this.lastSavedPath = resData.save_path;
                }
                Toast.show(resData.message || '直播录制已启动', 'success');
                this.startProgressPolling();
            }
            // B. 如果是博主单选分类（作品/直播回放/喜欢/合集/日常）
            else if (data.type === 'user' || (data.type === 'live' && !isLiveSelected)) {
                const selectedType = this.selectedType;
                const maxPages = parseInt(document.getElementById('dy-max-pages').value) || 0;
                const secUid = data.sec_uid || data.id;

                const res = await fetch('/api/douyin/download-user', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        sec_uid: secUid,
                        types: [selectedType],
                        max_pages: maxPages
                    })
                });
                const resData = await res.json();
                if (resData.error) throw new Error(resData.error);

                Toast.show('批量下载任务已成功启动', 'success');
                this.startProgressPolling();
            } 
            // C. 普通单条视频/回放/合集/音乐下载
            else {
                const res = await fetch('/api/douyin/download-single', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ url })
                });
                const resData = await res.json();
                if (resData.error) throw new Error(resData.error);

                if (resData.save_path) {
                    this.lastSavedPath = resData.save_path;
                }

                if (resData.task_started) {
                    Toast.show(resData.message || '任务已启动', 'success');
                    this.startProgressPolling();
                } else {
                    const finishTime = new Date().toLocaleTimeString();
                    logContainer.innerHTML += `<div style="margin-bottom: 4px; color: #10b981;">[${finishTime}] ✅ 下载完成: ${resData.title}</div>`;
                    document.getElementById('dy-progress-bar').style.width = '100%';
                    document.getElementById('dy-progress-text').textContent = '100%';
                    Toast.show(`下载完成: ${resData.title}`, 'success');
                    this.updateStatusView({ status: 'completed', downloaded_count: 1, total: 1, last_saved_path: resData.save_path });
                    this.updateDownloadBtnLabel(false, false);
                }
            }
        } catch (err) {
            Toast.show(err.message, 'error');
            const errorTime = new Date().toLocaleTimeString();
            logContainer.innerHTML += `<div style="margin-bottom: 4px; color: #ef4444;">[${errorTime}] ❌ 任务启动失败: ${err.message}</div>`;
            this.updateStatusView({ status: 'idle' });
            this.updateDownloadBtnLabel(false, false);
            const hud = document.getElementById('dy-live-recording-hud');
            const batchWrap = document.getElementById('dy-batch-progress-wrap');
            if (hud) hud.style.display = 'none';
            if (batchWrap) batchWrap.style.display = 'none';
        }
    },

    startProgressPolling() {
        document.getElementById('dy-download-status').style.display = 'block';
        const logContainer = document.getElementById('dy-log-container');
        const cancelBtn = document.getElementById('dy-cancel-btn');

        if (this.pollTimer) clearInterval(this.pollTimer);

        this.pollTimer = setInterval(async () => {
            try {
                const res = await fetch('/api/douyin/progress');
                const data = await res.json();

                if (data.last_saved_path) {
                    this.lastSavedPath = data.last_saved_path;
                }

                this.updateStatusView(data);

                // update logs
                if (data.logs && data.logs.length > 0) {
                    logContainer.innerHTML = data.logs.map(l => `<div style="margin-bottom: 4px;">${l}</div>`).join('');
                    logContainer.scrollTop = logContainer.scrollHeight;
                }

                // 检查状态
                if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled' || data.status === 'idle') {
                    clearInterval(this.pollTimer);
                    this.pollTimer = null;
                    cancelBtn.style.display = 'none';
                    this.updateDownloadBtnLabel(false, false);

                    if (data.status === 'completed') {
                        Toast.show(data.task_type === 'live' ? '直播录制已完成并保存！' : '下载任务完成！', 'success');
                    } else if (data.status === 'cancelled') {
                        Toast.show(data.task_type === 'live' ? '已停止直播录制并保存' : '下载已取消', 'info');
                    } else if (data.status === 'failed') {
                        Toast.show('任务执行失败', 'error');
                    }
                } else {
                    cancelBtn.style.display = 'flex';
                }
            } catch(e) {}
        }, 1000);
    },

    async cancelDownload() {
        const cancelBtn = document.getElementById('dy-cancel-btn');
        const parseBtn = document.getElementById('dy-parse-btn');
        if (cancelBtn) cancelBtn.disabled = true;
        if (parseBtn) {
            parseBtn.disabled = true;
            parseBtn.textContent = '正在停止任务...';
        }
 
        try {
            const res = await API.douyin.cancelDownload();
            Toast.show(res.message, 'info');
            this.updateStatusView({ status: 'cancelled' });
            this.updateDownloadBtnLabel(false, false);
        } catch (err) {
            Toast.show(err.message, 'error');
            if (cancelBtn) cancelBtn.disabled = false;
            if (err.message && err.message.includes('没有正在运行')) {
                this.updateStatusView({ status: 'idle' });
                this.updateDownloadBtnLabel(false, false);
            } else {
                this.updateDownloadBtnLabel(true, this.selectedType === 'live');
            }
        }
    },

    escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    },

    destroy() {
        if (this.pollTimer) clearInterval(this.pollTimer);
    }
};