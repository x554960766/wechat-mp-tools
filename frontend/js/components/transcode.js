/**
     * 视频转码页面组件
     * Premium Glassmorphism 界面 + 一键转码 + 拖入识别 + 双流信息对比
     */
const TranscodePage = {
    selectedFile: null,       // 当前选中的待转码文件: { name, path, size, meta }
    scannedVideos: [],        // 扫描到的已下载视频列表
    pollTimer: null,          // 转码状态轮询定时器
    isUploading: false,       // 外部浏览器拖入时的上传状态
    activeTab: 'wechat',      // 下载视频扫描的分组标签: wechat / douyin

    render() {
        return `
            <div class="page-header animate-fade-in">
                <h2 class="page-title">🎬 视频一键转码</h2>
                <p class="page-description">支持一键转码视频号及抖音已下载视频，也支持从本地其他文件夹拖入视频文件，进行高画质转换与高码率压缩。</p>
            </div>

            <div class="grid grid-2 animate-fade-in" style="grid-template-columns: 1.1fr 0.9fr; gap: var(--spacing-lg); align-items: start;">
                <!-- 左半边: 视频来源与转码参数配置 -->
                <div style="display: flex; flex-direction: column; gap: var(--spacing-lg);">
                    <!-- 视频选择与拖拽区 -->
                    <div class="card">
                        <div class="card-header">
                            <h3 class="card-title">📥 视频来源选择</h3>
                        </div>
                        <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);">
                            <!-- 拖拽/点击选择区域 -->
                            <div id="transcode-dropzone" class="dropzone" style="border: 2px dashed var(--border-color); border-radius: var(--radius-lg); padding: var(--spacing-xl); text-align: center; background: var(--bg-input); cursor: pointer; transition: all var(--transition-normal); position: relative; overflow: hidden;">
                                <div class="dropzone-icon" style="font-size: 2.8rem; margin-bottom: var(--spacing-sm); filter: drop-shadow(0 0 10px var(--primary-glow));">
                                    📁
                                </div>
                                <div class="dropzone-text" style="font-weight: 600; color: var(--text-primary); font-size: 1.05rem;">
                                    拖拽外部视频文件到此处，或点击浏览本地文件
                                </div>
                                <div class="dropzone-subtext" style="font-size: 0.8rem; color: var(--text-muted); margin-top: 6px;">
                                    支持 MP4, MOV, MKV, AVI 等格式
                                </div>
                                <input type="file" id="transcode-file-input" accept="video/*" style="display: none;" />
                                
                                <!-- 上传遮罩层 (外部浏览器 fallback) -->
                                <div id="upload-overlay" style="display: none; position: absolute; inset: 0; background: rgba(10, 10, 26, 0.9); flex-direction: column; align-items: center; justify-content: center; gap: var(--spacing-md); z-index: 10;">
                                    <div class="spinner" style="width: 36px; height: 36px;"></div>
                                    <div style="color: var(--text-primary); font-weight: 500;" id="upload-status-text">正在上传视频到本地缓存...</div>
                                    <div class="progress-bar" style="width: 60%; height: 6px;">
                                        <div class="progress-fill" id="upload-progress-fill" style="width: 0%;"></div>
                                    </div>
                                </div>
                            </div>

                            <!-- 已选中文件信息摘要 -->
                            <div id="selected-file-banner" style="display: none; margin-top: var(--spacing-md); padding: 12px; background: rgba(102, 126, 234, 0.08); border: 1px solid rgba(102, 126, 234, 0.2); border-radius: var(--radius-md); align-items: center; gap: var(--spacing-md);">
                                <div style="font-size: 1.8rem;">🎥</div>
                                <div style="flex: 1; min-width: 0;">
                                    <div id="selected-file-name" style="font-weight: 600; font-size: 0.95rem; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"></div>
                                    <div style="display: flex; gap: 12px; font-size: 0.78rem; color: var(--text-secondary); margin-top: 4px;">
                                        <span id="selected-file-size"></span>
                                        <span id="selected-file-type" style="color: var(--primary-light); font-weight: bold;"></span>
                                    </div>
                                </div>
                                <button class="btn btn-secondary btn-sm" onclick="TranscodePage.clearSelectedFile()" style="padding: 4px 8px; font-size: 0.75rem;">取消</button>
                            </div>
                        </div>
                    </div>

                    <!-- 转码配置参数 -->
                    <div class="card" id="transcode-settings-card" style="opacity: 0.6; pointer-events: none; transition: opacity 0.3s;">
                        <div class="card-header">
                            <h3 class="card-title">⚙️ 转码与压缩参数配置</h3>
                        </div>
                        <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);">
                            <div class="form-row">
                                <div class="form-group">
                                    <label class="form-label" for="param-format">输出容器格式</label>
                                    <select class="form-select" id="param-format" onchange="TranscodePage.onFormatChange()">
                                        <option value="mp4">MP4 (通用高清封装)</option>
                                        <option value="mkv">MKV (高清无损容器)</option>
                                        <option value="mov">MOV (Apple 兼容格式)</option>
                                        <option value="webm">WebM (适合网页播放)</option>
                                        <option value="mp3">MP3 (仅提取音频)</option>
                                    </select>
                                </div>

                                <div class="form-group" id="group-video-codec">
                                    <label class="form-label" for="param-codec">视频编码格式</label>
                                    <select class="form-select" id="param-codec">
                                        <option value="h264">H.264 / AVC (高兼容性)</option>
                                        <option value="hevc">H.265 / HEVC (高压缩率)</option>
                                        <option value="copy">Direct Copy (直接封装，不重编码)</option>
                                    </select>
                                </div>
                            </div>

                            <div class="form-row" id="group-quality-res">
                                <div class="form-group">
                                    <label class="form-label" for="param-quality">转换质量 (压缩率)</label>
                                    <select class="form-select" id="param-quality">
                                        <option value="medium">中等画质 (兼顾体积与画质 - 推荐)</option>
                                        <option value="high">极佳画质 (体积偏大，适合二次编辑)</option>
                                        <option value="low">极高压缩率 (体积缩减 60-80%，适合传输)</option>
                                    </select>
                                </div>

                                <div class="form-group">
                                    <label class="form-label" for="param-resolution">目标分辨率</label>
                                    <select class="form-select" id="param-resolution">
                                        <option value="keep">保持原视频大小</option>
                                        <option value="1080p">1080P FHD (1920x1080)</option>
                                        <option value="720p">720P HD (1280x720)</option>
                                        <option value="480p">480P 标清 (854x480)</option>
                                    </select>
                                </div>
                            </div>

                            <div class="form-row" style="margin-top: 8px;">
                                <div class="form-group" id="group-audio-mode" style="margin-bottom: 0;">
                                    <label class="form-label" for="param-audio">音频轨处理模式</label>
                                    <select class="form-select" id="param-audio">
                                        <option value="keep">保留音频轨道</option>
                                        <option value="mute">视频静音 (去除音轨)</option>
                                        <option value="mp3">压缩音轨为 MP3 (省体积)</option>
                                    </select>
                                </div>

                                <div class="form-group" id="group-hw-accel" style="margin-bottom: 0;">
                                    <label class="form-label" for="param-hw">硬件加速引擎 (macOS)</label>
                                    <select class="form-select" id="param-hw">
                                        <option value="true">💡 开启 GPU 编码加速 (推荐，快 5-10 倍)</option>
                                        <option value="false">标准 CPU 软件转码 (兼容性佳，发热高)</option>
                                    </select>
                                </div>
                            </div>

                            <div style="margin-top: var(--spacing-lg);">
                                <button class="btn btn-primary" id="btn-start-transcode" onclick="TranscodePage.startTranscoding()" style="width: 100%; height: 46px; font-size: 1rem; border-radius: var(--radius-md);">
                                    ⚡ 开始一键转码
                                </button>
                            </div>
                        </div>
                    </div>

                </div>

                <!-- 右半边: 转码队列、元数据双流比对与扫描导入 -->
                <div style="display: flex; flex-direction: column; gap: var(--spacing-lg);">
                    <!-- 转码进度及任务队列 -->
                    <div class="card">
                        <div class="card-header">
                            <h3 class="card-title">⏳ 任务队列与进度</h3>
                            <button class="btn btn-secondary btn-sm" onclick="TranscodePage.clearCompletedJobs()" style="padding: 4px 8px; font-size: 0.75rem;">清空历史</button>
                        </div>
                        <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);">
                            <div id="transcode-queue-container" style="display: flex; flex-direction: column; gap: var(--spacing-md); max-height: 240px; overflow-y: auto;">
                                <div style="text-align: center; color: var(--text-muted); padding: 30px 0; font-size: 0.9rem;">
                                    暂无转码任务，请在左侧选择视频后开始
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- 视频元数据比对区 -->
                    <div class="card" id="metadata-comparison-card" style="display: none; animation: fadeIn 0.3s ease;">
                        <div class="card-header">
                            <h3 class="card-title">🔍 视频元数据精确比对</h3>
                        </div>
                        <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md); display: flex; flex-direction: column; gap: var(--spacing-md);">
                            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
                                <!-- 准备转码的视频信息 -->
                                <div style="background: rgba(255, 255, 255, 0.05); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: 12px;">
                                    <div style="font-weight: bold; font-size: 0.8rem; color: var(--text-primary); border-bottom: 1px solid var(--border-color); padding-bottom: 6px; margin-bottom: 8px; display: flex; align-items: center; gap: 6px;">
                                        <span>📝</span> 准备转码源文件
                                    </div>
                                    <div class="metadata-detail-list" style="display: flex; flex-direction: column; gap: 6px; font-size: 0.82rem; color: var(--text-primary);">
                                        <div>大小：<span id="meta-in-size" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                        <div>时长：<span id="meta-in-duration" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                        <div>格式：<span id="meta-in-format" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                        <div>视频流：<span id="meta-in-vcodec" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                        <div>音频流：<span id="meta-in-acodec" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                        <div>分辨率：<span id="meta-in-resolution" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                        <div>帧率：<span id="meta-in-fps" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                    </div>
                                </div>

                                <!-- 转码完成后的视频信息 -->
                                <div id="meta-out-box" style="background: rgba(67, 233, 123, 0.05); border: 1px dashed rgba(67, 233, 123, 0.2); border-radius: var(--radius-md); padding: 12px; transition: all 0.3s;">
                                    <div style="font-weight: bold; font-size: 0.8rem; color: var(--text-primary); border-bottom: 1px dashed rgba(67, 233, 123, 0.2); padding-bottom: 6px; margin-bottom: 8px; display: flex; align-items: center; gap: 6px;">
                                        <span>🏁</span> 转码输出结果
                                    </div>
                                    <div id="meta-out-empty" style="color: var(--text-primary); font-size: 0.8rem; text-align: center; padding-top: 30px;">
                                        待当前任务转换结束...
                                    </div>
                                    <div id="meta-out-details" class="metadata-detail-list" style="display: none; flex-direction: column; gap: 6px; font-size: 0.82rem; color: var(--text-primary);">
                                        <div>大小：<span id="meta-out-size" style="font-weight: bold; color: #22c55e !important;">--</span></div>
                                        <div>时长：<span id="meta-out-duration" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                        <div>格式：<span id="meta-out-format" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                        <div>视频流：<span id="meta-out-vcodec" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                        <div>音频流：<span id="meta-out-acodec" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                        <div>分辨率：<span id="meta-out-resolution" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                        <div>帧率：<span id="meta-out-fps" style="font-weight: bold; color: var(--text-primary);">--</span></div>
                                    </div>
                                </div>
                            </div>
                            <div style="display: flex; justify-content: center; margin-top: 4px;">
                                <span id="meta-out-ratio" class="badge badge-success" style="display: none; padding: 4px 12px; font-size: 0.85rem;">--</span>
                            </div>
                        </div>
                    </div>

                    <!-- App已下载视频快捷导入列表 -->
                    <div class="card">
                        <div class="card-header">
                            <h3 class="card-title">📂 已下载视频快速导入</h3>
                        </div>
                        <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md); display: flex; flex-direction: column; gap: var(--spacing-md);">
                            <!-- 分类标签 -->
                            <div class="system-switcher" style="margin-top: 0; padding: 2px;">
                                <button class="sys-btn active" id="btn-tab-wechat" onclick="TranscodePage.switchTab('wechat')" style="font-size: 0.75rem; padding: 4px 0;">微信视频号下载</button>
                                <button class="sys-btn" id="btn-tab-douyin" onclick="TranscodePage.switchTab('douyin')" style="font-size: 0.75rem; padding: 4px 0;">抖音下载</button>
                            </div>

                            <!-- 扫描结果列表 -->
                            <div id="scanned-list-container" style="display: flex; flex-direction: column; gap: var(--spacing-sm); max-height: 280px; overflow-y: auto; padding-right: 2px;">
                                <div class="loading-screen" style="min-height: 80px;">
                                    <div class="spinner" style="width: 24px; height: 24px;"></div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    async init() {
        console.log('TranscodePage initialized.');
        ensure_drag_drop_styles();
        
        // 绑定拖拽事件及普通文件选取
        this.setupDragAndDrop();
        
        // 扫描下载目录
        await this.loadScannedVideos();
        
        // 检查路由 Query 参数实现自动导入（如从“下载历史”点击跳转过来）
        const hash = window.location.hash;
        if (hash.includes('?')) {
            try {
                const queryStr = hash.split('?')[1];
                const params = new URLSearchParams(queryStr);
                const path = params.get('path');
                const name = params.get('name') || '历史视频导入';
                if (path) {
                    const cleanPath = decodeURIComponent(path);
                    const cleanName = decodeURIComponent(name);
                    
                    // 自动请求后端获取文件真实大小，并载入元数据面板
                    const meta = await API.transcode.videoInfo(cleanPath);
                    const sizeBytes = (meta && !meta.error) ? meta.size_bytes : 0;
                    
                    this.loadVideoInfo(cleanPath, cleanName, sizeBytes);
                    Toast.success('已自动导入历史视频！');
                }
            } catch (err) {
                console.error('Failed to auto-import query path:', err);
            }
        }
        
        // 轮询队列任务进度
        this.startStatusPolling();
    },

    destroy() {
        if (this.pollTimer) {
            clearInterval(this.pollTimer);
            this.pollTimer = null;
        }
        console.log('TranscodePage destroyed.');
    },

    // ── 双系统 Tab 切换 ──────────────────────────────────────
    switchTab(tab) {
        this.activeTab = tab;
        document.getElementById('btn-tab-wechat').classList.toggle('active', tab === 'wechat');
        document.getElementById('btn-tab-douyin').classList.toggle('active', tab === 'douyin');
        this.renderScannedVideos();
    },

    // ── 拖拽及手动文件选择逻辑 ────────────────────────────────────
    setupDragAndDrop() {
        const dropzone = document.getElementById('transcode-dropzone');
        const fileInput = document.getElementById('transcode-file-input');

        if (!dropzone || !fileInput) return;

        // 点击唤起文件选择器
        dropzone.addEventListener('click', () => fileInput.click());

        fileInput.addEventListener('change', (e) => {
            if (e.target.files && e.target.files[0]) {
                this.handleFileSelected(e.target.files[0]);
            }
        });

        // 拖拽高亮与移出
        dropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropzone.style.borderColor = 'var(--primary)';
            dropzone.style.background = 'rgba(102, 126, 234, 0.05)';
        });

        dropzone.addEventListener('dragleave', () => {
            dropzone.style.borderColor = 'var(--border-color)';
            dropzone.style.background = 'var(--bg-input)';
        });

        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.style.borderColor = 'var(--border-color)';
            dropzone.style.background = 'var(--bg-input)';

            if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                this.handleFileSelected(e.dataTransfer.files[0]);
            }
        });
    },

    async handleFileSelected(file) {
        // 核心亮点: 支持 Desktop webview 绝对路径直读, 或者是 Web 浏览器 HTTP upload fallback
        if (file.path) {
            // Desktop 模式：直接读取绝对文件路径，极速元数据分析，不占用网络资源
            this.loadVideoInfo(file.path, file.name, file.size);
        } else {
            // Web 外部浏览器模式：通过 HTTP 循环上传到本地 Python temp_uploads 文件夹进行转码
            this.uploadFile(file);
        }
    },

    async uploadFile(file) {
        if (this.isUploading) return;
        this.isUploading = true;

        const overlay = document.getElementById('upload-overlay');
        const progressFill = document.getElementById('upload-progress-fill');
        const statusText = document.getElementById('upload-status-text');

        if (overlay) overlay.style.display = 'flex';

        try {
            const formData = new FormData();
            formData.append('file', file);
            
            // HTTP 上传 (localhost 瞬间传输)
            const result = await API.transcode.upload(formData);
            
            if (result.success) {
                Toast.success('文件缓存成功！');
                this.loadVideoInfo(result.path, result.name, result.size_bytes);
            }
        } catch (err) {
            console.error('File upload failed:', err);
        } finally {
            this.isUploading = false;
            if (overlay) overlay.style.display = 'none';
            if (progressFill) progressFill.style.width = '0%';
        }
    },

    // ── 读取与解析视频信息 ─────────────────────────────────────
    async loadVideoInfo(absolutePath, filename, sizeBytes) {
        const settingsCard = document.getElementById('transcode-settings-card');
        const banner = document.getElementById('selected-file-banner');
        const bannerName = document.getElementById('selected-file-name');
        const bannerSize = document.getElementById('selected-file-size');
        const bannerType = document.getElementById('selected-file-type');
        
        // 1. 显示已选择的文件横幅
        if (banner) banner.style.display = 'flex';
        if (bannerName) bannerName.textContent = filename;
        if (bannerSize) bannerSize.textContent = formatBytes(sizeBytes);
        if (bannerType) bannerType.textContent = filename.split('.').pop().toUpperCase();
        
        // 激活设置卡片
        if (settingsCard) {
            settingsCard.style.opacity = '1';
            settingsCard.style.pointerEvents = 'auto';
        }

        // 2. 显示元数据读取动画
        this.selectedFile = {
            name: filename,
            path: absolutePath,
            size: sizeBytes,
            meta: null
        };

        // 展开元数据比对区，填充 -- 占位
        const compCard = document.getElementById('metadata-comparison-card');
        if (compCard) compCard.style.display = 'block';

        this.setMetaValues('in', { size_bytes: sizeBytes }, true);
        this.clearMetaOutValues();

        try {
            // 调用 ffprobe 提取信息
            const meta = await API.transcode.videoInfo(absolutePath);
            if (meta && !meta.error) {
                this.selectedFile.meta = meta;
                this.setMetaValues('in', meta, false);
            }
        } catch (err) {
            console.error('Failed to read video metadata:', err);
        }
    },

    clearSelectedFile() {
        this.selectedFile = null;
        const banner = document.getElementById('selected-file-banner');
        const settingsCard = document.getElementById('transcode-settings-card');
        const compCard = document.getElementById('metadata-comparison-card');

        if (banner) banner.style.display = 'none';
        if (settingsCard) {
            settingsCard.style.opacity = '0.6';
            settingsCard.style.pointerEvents = 'none';
        }
        if (compCard) compCard.style.display = 'none';
    },

    // ── 格式选项互斥处理 ─────────────────────────────────────
    onFormatChange() {
        const format = document.getElementById('param-format').value;
        const groupVideoCodec = document.getElementById('group-video-codec');
        const groupQualityRes = document.getElementById('group-quality-res');
        const groupAudioMode = document.getElementById('group-audio-mode');
        const groupHwAccel = document.getElementById('group-hw-accel');

        // 音频抽取模式
        const isAudioOnly = format === 'mp3';
        
        if (isAudioOnly) {
            if (groupVideoCodec) groupVideoCodec.style.display = 'none';
            if (groupQualityRes) groupQualityRes.style.display = 'none';
            if (groupAudioMode) groupAudioMode.style.display = 'none';
            if (groupHwAccel) groupHwAccel.style.display = 'none';
        } else {
            if (groupVideoCodec) groupVideoCodec.style.display = 'block';
            if (groupQualityRes) groupQualityRes.style.display = 'grid';
            if (groupAudioMode) groupAudioMode.style.display = 'block';
            if (groupHwAccel) groupHwAccel.style.display = 'block';
        }
    },

    // ── 开始转码并进入队列 ────────────────────────────────────
    async startTranscoding() {
        if (!this.selectedFile) return;

        const format = document.getElementById('param-format').value;
        const codec = document.getElementById('param-codec').value;
        const quality = document.getElementById('param-quality').value;
        const resolution = document.getElementById('param-resolution').value;
        const audio = document.getElementById('param-audio').value;
        const hw = document.getElementById('param-hw').value === 'true';

        const btn = document.getElementById('btn-start-transcode');
        if (btn) btn.disabled = true;

        const params = {
            output_format: format,
            video_codec: codec,
            quality: quality,
            resolution: resolution,
            audio_mode: audio,
            hw_accel: hw
        };

        try {
            const result = await API.transcode.start(this.selectedFile.path, params);
            if (result.success) {
                Toast.success('转码任务已成功排队！');
                // 立即更新队列状态
                await this.pollStatus();
            }
        } catch (err) {
            // shown by wrapper
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    // ── 扫描导入本地下载视频 ──────────────────────────────────
    async loadScannedVideos() {
        try {
            const data = await API.transcode.scanDownloads();
            if (data.success) {
                this.scannedVideos = data.videos || [];
                this.renderScannedVideos();
            }
        } catch (err) {
            console.error('Scan downloads failed:', err);
        }
    },

    renderScannedVideos() {
        const container = document.getElementById('scanned-list-container');
        if (!container) return;

        const filtered = this.scannedVideos.filter(v => v.source === this.activeTab);

        if (filtered.length === 0) {
            container.innerHTML = `
                <div style="text-align: center; color: var(--text-muted); padding: 30px 0; font-size: 0.85rem;">
                    当前分类下暂无已下载的视频文件
                </div>
            `;
            return;
        }

        container.innerHTML = filtered.map(v => {
            const timeStr = v.created_at ? new Date(v.created_at * 1000).toLocaleDateString() : '--';
            const sizeStr = formatBytes(v.size_bytes);
            return `
                <div class="scanned-item" style="display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px; background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: var(--radius-md); transition: all 0.2s;">
                    <div style="min-width: 0; flex: 1;">
                        <div style="font-weight: 500; font-size: 0.85rem; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${escapeHtml(v.name)}">
                            ${escapeHtml(v.name)}
                        </div>
                        <div style="font-size: 0.72rem; color: var(--text-muted); margin-top: 4px; display: flex; gap: var(--spacing-md);">
                            <span>📁 ${escapeHtml(v.parent_name || '已下载')}</span>
                            <span>⚖️ ${sizeStr}</span>
                            <span>🕒 ${timeStr}</span>
                        </div>
                    </div>
                    <button class="btn btn-secondary btn-sm" onclick="TranscodePage.importScannedVideo('${escapeSingleQuotes(v.path)}', '${escapeSingleQuotes(v.name)}', ${v.size_bytes})" style="padding: 4px 10px; font-size: 0.75rem; flex-shrink: 0;">
                        导入
                    </button>
                </div>
            `;
        }).join('');
    },

    importScannedVideo(path, name, sizeBytes) {
        this.loadVideoInfo(path, name, sizeBytes);
        Toast.success('成功快捷导入下载视频！');
    },

    // ── 轮询队列与进度 ──────────────────────────────────────────
    startStatusPolling() {
        if (this.pollTimer) clearInterval(this.pollTimer);
        this.pollStatus();
        this.pollTimer = setInterval(() => this.pollStatus(), 1500);
    },

    async pollStatus() {
        const queueContainer = document.getElementById('transcode-queue-container');
        if (!queueContainer) return;

        try {
            const data = await API.transcode.status();
            if (!data.success || !data.jobs || data.jobs.length === 0) {
                queueContainer.innerHTML = `
                    <div style="text-align: center; color: var(--text-muted); padding: 30px 0; font-size: 0.9rem;">
                        暂无转码任务，请在左侧选择视频后开始
                    </div>
                `;
                return;
            }

            // 按创建时间倒序排列显示任务
            const sortedJobs = [...data.jobs].sort((a, b) => b.created_at - a.created_at);

            queueContainer.innerHTML = sortedJobs.map(job => {
                let statusBadge = '';
                let progressArea = '';
                
                const timeStr = new Date(job.created_at * 1000).toLocaleTimeString();
                
                if (job.status === 'pending') {
                    statusBadge = '<span class="badge badge-warning" style="background: rgba(254, 225, 64, 0.08); color: var(--warning);">等待中</span>';
                } else if (job.status === 'running') {
                    statusBadge = '<span class="badge badge-info" style="background: rgba(102, 126, 234, 0.08); color: var(--primary-light); animation: pulse 1s infinite;">转码中</span>';
                    progressArea = `
                        <div style="margin-top: 8px; display: flex; flex-direction: column; gap: 4px;">
                            <div class="progress-bar" style="height: 6px;">
                                <div class="progress-fill" style="width: ${job.progress}%;"></div>
                            </div>
                            <div style="display: flex; justify-content: space-between; font-size: 0.72rem; color: var(--text-secondary);">
                                <span>速度: ${job.speed || 'N/A'} (${job.fps ? Math.round(job.fps) + ' fps' : '--'})</span>
                                <span>进度: ${job.progress}%</span>
                            </div>
                        </div>
                    `;
                } else if (job.status === 'completed') {
                    statusBadge = '<span class="badge badge-success" style="background: rgba(67, 233, 123, 0.08); color: var(--success);">已完成</span>';
                } else if (job.status === 'failed') {
                    statusBadge = '<span class="badge badge-error" style="background: rgba(245, 87, 108, 0.08); color: var(--error);" title="' + escapeHtml(job.error || '') + '">失败</span>';
                }

                return `
                    <div class="queue-item" style="padding: 10px; background: rgba(255, 255, 255, 0.01); border: 1px solid var(--border-color); border-radius: var(--radius-md); transition: all 0.2s;">
                        <div style="display: flex; align-items: center; justify-content: space-between; gap: 8px;">
                            <div style="min-width: 0; flex: 1;">
                                <div style="font-weight: 600; font-size: 0.85rem; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${escapeHtml(job.input_name)}">
                                    ${escapeHtml(job.input_name)}
                                </div>
                                <div style="font-size: 0.72rem; color: var(--text-muted); margin-top: 4px; display: flex; gap: 8px; align-items: center;">
                                    <span>🕒 ${timeStr}</span>
                                    <span>⚙️ ${job.params.output_format.toUpperCase()} (${job.params.video_codec})</span>
                                </div>
                            </div>
                            <div style="display: flex; align-items: center; gap: 8px; flex-shrink: 0;">
                                ${statusBadge}
                                ${job.status === 'completed' && job.output_path ? `
                                    <button class="btn btn-secondary btn-sm" onclick="TranscodePage.openOutputParent('${escapeSingleQuotes(job.output_path)}')" style="padding: 2px 6px; font-size: 0.7rem;">
                                        📁 目录
                                    </button>
                                ` : ''}
                            </div>
                        </div>
                        ${progressArea}
                        ${job.status === 'failed' ? `
                            <div style="margin-top: 6px; color: var(--error); font-size: 0.72rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${escapeHtml(job.error || '')}">
                                ❌ 错误: ${escapeHtml(job.error || '')}
                            </div>
                        ` : ''}
                    </div>
                `;
            }).join('');

            // 如果当前查看的视频就是队列中转码完成的视频，自动填充输出比对元数据（始终获取最新完成的任务）
            if (this.selectedFile) {
                const matchedJobs = data.jobs.filter(j => j.input_path === this.selectedFile.path && j.status === 'completed');
                if (matchedJobs.length > 0) {
                    matchedJobs.sort((a, b) => b.created_at - a.created_at);
                    const latestJob = matchedJobs[0];
                    if (latestJob && latestJob.output_metadata) {
                        this.showMetaOut(latestJob);
                    }
                }
            }

        } catch (err) {
            console.error('Queue poll error:', err);
        }
    },

    async openOutputParent(path) {
        try {
            await API.transcode.openParent(path);
            Toast.success('输出文件夹已打开');
        } catch (err) {
            // shown by wrapper
        }
    },

    async clearCompletedJobs() {
        try {
            await API.transcode.clearCompleted();
            Toast.success('已清空历史完成任务');
            this.clearMetaOutValues();
            this.pollStatus();
        } catch (err) {
            // shown by wrapper
        }
    },

    // ── 双流元数据界面更新器 ────────────────────────────────────
    setMetaValues(type, meta, loading = false) {
        const sizeEl = document.getElementById(`meta-${type}-size`);
        const durEl = document.getElementById(`meta-${type}-duration`);
        const fmtEl = document.getElementById(`meta-${type}-format`);
        const vcodecEl = document.getElementById(`meta-${type}-vcodec`);
        const acodecEl = document.getElementById(`meta-${type}-acodec`);
        const resEl = document.getElementById(`meta-${type}-resolution`);
        const fpsEl = document.getElementById(`meta-${type}-fps`);

        if (loading) {
            if (sizeEl) sizeEl.textContent = meta.size_bytes ? formatBytes(meta.size_bytes) : '--';
            if (durEl) durEl.textContent = '读取中...';
            if (fmtEl) fmtEl.textContent = '读取中...';
            if (vcodecEl) vcodecEl.textContent = '读取中...';
            if (acodecEl) acodecEl.textContent = '读取中...';
            if (resEl) resEl.textContent = '读取中...';
            if (fpsEl) fpsEl.textContent = '读取中...';
            return;
        }

        if (sizeEl) sizeEl.textContent = formatBytes(meta.size_bytes);
        if (durEl) durEl.textContent = formatDuration(meta.duration);
        if (fmtEl) fmtEl.textContent = meta.format_name ? meta.format_name.split(',')[0] : 'Unknown';
        if (vcodecEl) vcodecEl.textContent = meta.video_codec.toUpperCase();
        if (acodecEl) acodecEl.textContent = meta.audio_codec.toUpperCase();
        if (resEl) resEl.textContent = meta.width && meta.height ? `${meta.width} x ${meta.height}` : 'Audio Only';
        if (fpsEl) fpsEl.textContent = meta.fps ? `${meta.fps} FPS` : '--';
    },

    showMetaOut(job) {
        const metaOutEmpty = document.getElementById('meta-out-empty');
        const metaOutDetails = document.getElementById('meta-out-details');
        const metaOutBox = document.getElementById('meta-out-box');
        const ratioEl = document.getElementById('meta-out-ratio');

        if (metaOutEmpty) metaOutEmpty.style.display = 'none';
        if (metaOutDetails) metaOutDetails.style.display = 'flex';
        if (metaOutBox) {
            metaOutBox.style.background = 'rgba(67, 233, 123, 0.05)';
            metaOutBox.style.borderColor = 'rgba(67, 233, 123, 0.4)';
        }

        const outMeta = job.output_metadata;
        this.setMetaValues('out', outMeta, false);

        // 计算并显示高对比度的空间节省率 Badge
        if (ratioEl) {
            const inSize = job.input_size;
            const outSize = job.output_size || outMeta.size_bytes;
            if (inSize && outSize) {
                const ratio = Math.round((1 - outSize / inSize) * 100);
                ratioEl.style.display = 'inline-flex';
                if (ratio > 0) {
                    ratioEl.textContent = `🎉 已节省空间 ${ratio}%`;
                    ratioEl.style.background = 'rgba(67, 233, 123, 0.15)';
                    ratioEl.style.color = '#22c55e';
                } else if (ratio < 0) {
                    ratioEl.textContent = `⚠️ 体积增大了 ${Math.abs(ratio)}%`;
                    ratioEl.style.background = 'rgba(239, 68, 68, 0.15)';
                    ratioEl.style.color = '#ef4444';
                } else {
                    ratioEl.textContent = '⚖️ 转码前后体积无变化';
                    ratioEl.style.background = 'rgba(102, 126, 234, 0.15)';
                    ratioEl.style.color = 'var(--primary-light)';
                }
            }
        }
    },

    clearMetaOutValues() {
        const metaOutEmpty = document.getElementById('meta-out-empty');
        const metaOutDetails = document.getElementById('meta-out-details');
        const metaOutBox = document.getElementById('meta-out-box');
        const ratioEl = document.getElementById('meta-out-ratio');

        if (metaOutEmpty) metaOutEmpty.style.display = 'block';
        if (metaOutDetails) metaOutDetails.style.display = 'none';
        if (ratioEl) {
            ratioEl.style.display = 'none';
            ratioEl.textContent = '--';
        }
        if (metaOutBox) {
            metaOutBox.style.background = 'rgba(255, 255, 255, 0.01)';
            metaOutBox.style.borderColor = 'var(--border-color)';
        }
    }
};

// ── 格式化大小与时间辅助函数 ────────────────────────────────────
function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

function formatDuration(seconds) {
    if (!seconds || isNaN(seconds)) return '00:00';
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    
    let ret = "";
    if (hrs > 0) {
        ret += (hrs < 10 ? "0" + hrs : hrs) + ":";
    }
    ret += (mins < 10 ? "0" + mins : mins) + ":";
    ret += (secs < 10 ? "0" + secs : secs);
    return ret;
}

function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[char]));
}

function escapeSingleQuotes(str) {
    if (!str) return '';
    return String(str).replace(/\\/g, '/').replace(/'/g, "\\'");
}

function ensure_drag_drop_styles() {
    // 注入局部的微动画样式确保拖拽反馈及 pulse 更加细腻
    const id = 'transcode-local-styles';
    if (document.getElementById(id)) return;

    const style = document.createElement('style');
    style.id = id;
    style.innerHTML = `
        @keyframes pulse {
            0% { opacity: 0.6; }
            50% { opacity: 1; }
            100% { opacity: 0.6; }
        }
        .scanned-item:hover {
            background: rgba(255,255,255,0.06) !important;
            border-color: var(--primary-light) !important;
        }
        .queue-item:hover {
            border-color: var(--primary-light) !important;
            box-shadow: var(--shadow-sm);
        }
    `;
    document.head.appendChild(style);
}
