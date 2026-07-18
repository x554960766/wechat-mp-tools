/**
 * 系统设置页面组件
 */
const SettingsPage = {
    settings: {},
    cookiePollTimer: null,

    render() {
        let startOptions = '';
        for (let i = 0; i <= 23; i++) {
            startOptions += `<option value="${i}">${i} 点</option>`;
        }
        let endOptions = '';
        for (let i = 0; i <= 24; i++) {
            endOptions += `<option value="${i}">${i} 点</option>`;
        }
        let minuteOptions = '';
        for (let i = 0; i <= 59; i++) {
            const label = String(i).padStart(2, '0');
            minuteOptions += `<option value="${i}">${label} 分</option>`;
        }

        return `
            <div class="page-header">
                <h2 class="page-title">系统设置</h2>
                <p class="page-description">配置文章抓取、并发下载选项及文件存储目录</p>
            </div>

            <div class="grid grid-2" style="gap: var(--spacing-lg);">
                <!-- 下载配置 -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">📥 下载与存储配置</h3>
                    </div>
                    <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);">
                        <div class="form-group">
                            <label class="form-label" for="setting-download-dir">存储目录</label>
                            <input type="text" class="form-input" id="setting-download-dir" placeholder="请输入绝对路径..." />
                            <div class="form-hint">下载的文章、图片和视频将保存在该目录下</div>
                        </div>

                        <div class="form-group">
                            <label class="form-label" for="setting-concurrent">并发下载数 (图片/媒体)</label>
                            <input type="number" class="form-input" id="setting-concurrent" min="1" max="10" />
                            <div class="form-hint">并发下载静态资源的线程数，建议设为 1-3，过高可能被微信限制</div>
                        </div>

                        <div class="form-group" style="display: flex; gap: 24px; margin-top: var(--spacing-md);">
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="setting-save-images" style="width: 18px; height: 18px; accent-color: var(--primary);" />
                                <span>自动下载文章图片</span>
                            </label>
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="setting-save-videos" style="width: 18px; height: 18px; accent-color: var(--primary);" />
                                <span>自动下载文章视频</span>
                            </label>
                        </div>
                    </div>
                </div>

                <!-- 抓取配置 -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">⚙️ 抓取与防封配置</h3>
                    </div>
                    <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);">
                        <div class="form-group">
                            <label class="form-label" for="setting-delay">请求延迟间隔 (秒)</label>
                            <div style="display: flex; align-items: center; gap: 12px;">
                                <input type="range" id="setting-delay-range" min="0.2" max="5.0" step="0.1" style="flex: 1; accent-color: var(--primary);" oninput="document.getElementById('setting-delay').value = this.value" />
                                <input type="number" class="form-input" id="setting-delay" min="0.2" max="5.0" step="0.1" style="width: 80px;" oninput="document.getElementById('setting-delay-range').value = this.value" />
                            </div>
                            <div class="form-hint" style="color: var(--badge-warning-color);">频率过高极易导致微信号被封禁！建议设置 1.0 秒以上</div>
                        </div>

                        <div class="form-group">
                            <label class="form-label" for="setting-page-size">单页拉取数量</label>
                            <input type="number" class="form-input" id="setting-page-size" min="1" max="20" />
                            <div class="form-hint">每次请求微信服务器拉取的文章条数（建议 5-10 条）</div>
                        </div>

                        <div class="form-group">
                            <label class="form-label" for="setting-max-articles">批量拉取最大上限</label>
                            <input type="number" class="form-input" id="setting-max-articles" min="10" max="500" />
                            <div class="form-hint">一次批量拉取公众号文章的最大数量上限</div>
                        </div>

                        <div class="form-group">
                            <label class="form-label" for="setting-max-retries">最大重试次数</label>
                            <input type="number" class="form-input" id="setting-max-retries" min="0" max="10" />
                            <div class="form-hint">请求失败或超时后的自动重试次数</div>
                        </div>
                    </div>
                </div>

                <!-- 视频号解析配置 -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">🟢 视频号服务配置</h3>
                    </div>
                    <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);">
                        <div class="form-group" style="margin-top: var(--spacing-md);">
                            <label class="form-label" for="setting-channels-worker">自定义 Worker 域名 (私有云端)</label>
                            <input type="text" class="form-input" id="setting-channels-worker" placeholder="https://your-worker.your-name.workers.dev" />
                            <div class="form-hint">配置您专属的 Cloudflare Worker 网页中转解析服务，避免共享公共解析站。</div>
                        </div>
                    </div>
                </div>

                <!-- B站下载配置 -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">📺 Bilibili 下载配置</h3>
                    </div>
                    <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);">
                        <div class="form-group" style="margin-top: var(--spacing-md);">
                            <label class="form-label" for="setting-bili-quality">默认视频分辨率</label>
                            <select class="form-input" id="setting-bili-quality">
                                <option value="vip">1080P 高码率 / 4K (需要登录大会员账号)</option>
                                <option value="1080p">1080P 普通 (需要登录普通账号)</option>
                                <option value="720p">720P 高清 (未登录默认)</option>
                                <option value="360p">360P 标清 (省流/防卡顿)</option>
                            </select>
                            <div class="form-hint">未登录状态下，即便选择 1080P/4K，B站 接口也会降级返回 720P。</div>
                        </div>

                        <div class="form-group" style="display: flex; gap: 24px; margin-top: var(--spacing-md);">
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="setting-bili-danmaku" style="width: 18px; height: 18px; accent-color: var(--primary);" />
                                <span>下载弹幕并转为 ASS 轴</span>
                            </label>
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="setting-bili-subtitle" style="width: 18px; height: 18px; accent-color: var(--primary);" />
                                <span>下载内置字幕并转为 SRT</span>
                            </label>
                        </div>
                    </div>
                </div>

                <!-- RSS 订阅配置 -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">📡 RSS 订阅时间段配置</h3>
                    </div>
                    <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);">
                        <!-- 时间配置 — 控制整体自动抓取，始终可见 -->
                        <div style="display: flex; gap: 16px; margin-top: var(--spacing-md);">
                            <div class="form-group" style="flex: 1;">
                                <label class="form-label" for="setting-rss-start-hour">开始时间</label>
                                <div style="display: flex; gap: 8px;">
                                    <select class="form-input" id="setting-rss-start-hour" style="min-width: 0;">
                                        ${startOptions}
                                    </select>
                                    <select class="form-input" id="setting-rss-start-minute" style="min-width: 0;">
                                        ${minuteOptions}
                                    </select>
                                </div>
                                <div class="form-hint">自动采集开始时间，支持分钟</div>
                            </div>
                            <div class="form-group" style="flex: 1;">
                                <label class="form-label" for="setting-rss-end-hour">结束时间</label>
                                <div style="display: flex; gap: 8px;">
                                    <select class="form-input" id="setting-rss-end-hour" style="min-width: 0;" onchange="SettingsPage.syncRssEndMinute()">
                                        ${endOptions}
                                    </select>
                                    <select class="form-input" id="setting-rss-end-minute" style="min-width: 0;">
                                        ${minuteOptions}
                                    </select>
                                </div>
                                <div class="form-hint">自动采集结束时间，24 点仅支持 00 分</div>
                            </div>
                        </div>
                        <div class="form-hint" style="margin-top: 8px;">默认 00:00 到 24:00 整天采集，只有在设置的时间范围内才会执行 RSS 自动采集</div>

                        <hr style="border: 0; border-top: 1px solid var(--border-color); margin: var(--spacing-md) 0;" />

                        <!-- 上传开关 + 上传配置 — 仅控制上传行为 -->
                        <div class="form-group">
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="setting-rss-upload-enabled" onchange="SettingsPage.toggleRssUpload()" style="width: 18px; height: 18px; accent-color: var(--primary);" />
                                <span>RSS 新文章上传到服务器</span>
                            </label>
                            <div class="form-hint">开启后自动抓取下载成功时上传新文章到指定接口</div>
                        </div>
                        <div id="setting-rss-upload-panel" style="display: none;">
                            <div class="form-group" style="margin-top: var(--spacing-md);">
                                <label class="form-label" for="setting-device-id">设备 ID (deviceId)</label>
                                <input type="text" class="form-input" id="setting-device-id" placeholder="请输入设备 ID..." />
                                <div class="form-hint">RSS 上传接口 payload 中的 deviceId 配置</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label" for="setting-rss-upload-url">上传接口地址</label>
                                <input type="url" class="form-input" id="setting-rss-upload-url" placeholder="https://example.com/api/data/gzhAdd" />
                                <div class="form-hint">接口接收 articles 与 deviceId；正文 content 会以 base64 字符串上传</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="card" style="margin-top: var(--spacing-lg);">
                <div class="card-body" style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="color: var(--text-muted); font-size: 0.88rem;">
                        💡 修改后的配置将在下一次抓取/下载任务中生效。
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-secondary" onclick="SettingsPage.resetDefaults()">恢复默认</button>
                        <button class="btn btn-primary" onclick="SettingsPage.saveSettings()">💾 保存设置</button>
                    </div>
                </div>
            </div>
        `;
    },

    async init() {
        await this.loadSettings();
    },

    /** SPA 路由复用缓存页时调用，重新从后端拉取最新设置 */
    async onShow() {
        await this.loadSettings();
    },

    destroy() {
        if (this.cookiePollTimer) {
            clearInterval(this.cookiePollTimer);
            this.cookiePollTimer = null;
        }
    },

    async loadSettings() {
        try {
            const data = await API.settings.get();
            this.settings = data;
            this.populateUI(data);
        } catch (err) {
            Toast.error('加载系统配置失败');
        }
    },

    populateUI(data) {
        const dirInput = document.getElementById('setting-download-dir');
        const concurrentInput = document.getElementById('setting-concurrent');
        const saveImagesCheck = document.getElementById('setting-save-images');
        const saveVideosCheck = document.getElementById('setting-save-videos');
        const delayRange = document.getElementById('setting-delay-range');
        const delayInput = document.getElementById('setting-delay');
        const pageSizeInput = document.getElementById('setting-page-size');
        const maxArticlesInput = document.getElementById('setting-max-articles');
        const maxRetriesInput = document.getElementById('setting-max-retries');
        const chWorkerInput = document.getElementById('setting-channels-worker');
        const deviceIdInput = document.getElementById('setting-device-id');
        const rssStartHour = document.getElementById('setting-rss-start-hour');
        const rssStartMinute = document.getElementById('setting-rss-start-minute');
        const rssEndHour = document.getElementById('setting-rss-end-hour');
        const rssEndMinute = document.getElementById('setting-rss-end-minute');
        const rssUploadEnabled = document.getElementById('setting-rss-upload-enabled');
        const rssUploadUrl = document.getElementById('setting-rss-upload-url');
        
        const biliQuality = document.getElementById('setting-bili-quality');
        const biliDanmaku = document.getElementById('setting-bili-danmaku');
        const biliSubtitle = document.getElementById('setting-bili-subtitle');

        if (dirInput) dirInput.value = data.download_dir || '';
        if (concurrentInput) concurrentInput.value = data.concurrent_downloads || 1;
        if (saveImagesCheck) saveImagesCheck.checked = !!data.auto_save_images;
        if (saveVideosCheck) saveVideosCheck.checked = !!data.auto_save_videos;
        if (delayInput) delayInput.value = data.request_delay || 0.8;
        if (delayRange) delayRange.value = data.request_delay || 0.8;
        if (pageSizeInput) pageSizeInput.value = data.page_size || 10;
        if (maxArticlesInput) maxArticlesInput.value = data.max_articles || 50;
        if (maxRetriesInput) maxRetriesInput.value = data.max_retries || 3;
        if (chWorkerInput) chWorkerInput.value = data.custom_channels_worker || '';
        if (deviceIdInput) deviceIdInput.value = data.device_id || '公众号_caiji100';
        if (rssStartHour) rssStartHour.value = data.rss_start_hour !== undefined ? data.rss_start_hour : 0;
        if (rssStartMinute) rssStartMinute.value = data.rss_start_minute !== undefined ? data.rss_start_minute : 0;
        if (rssEndHour) rssEndHour.value = data.rss_end_hour !== undefined ? data.rss_end_hour : 24;
        if (rssEndMinute) rssEndMinute.value = data.rss_end_minute !== undefined ? data.rss_end_minute : 0;
        if (rssUploadEnabled) rssUploadEnabled.checked = data.rss_upload_enabled !== undefined ? !!data.rss_upload_enabled : false;
        if (rssUploadUrl) rssUploadUrl.value = data.rss_upload_url || '';
        
        if (biliQuality) biliQuality.value = data.bili_video_quality || '1080p';
        if (biliDanmaku) biliDanmaku.checked = data.bili_download_danmaku !== undefined ? !!data.bili_download_danmaku : true;
        if (biliSubtitle) biliSubtitle.checked = data.bili_download_subtitle !== undefined ? !!data.bili_download_subtitle : true;
        this.toggleRssUpload();
        this.syncRssEndMinute();
    },


    toggleRssUpload() {
        const rssUploadEnabled = document.getElementById('setting-rss-upload-enabled');
        const panel = document.getElementById('setting-rss-upload-panel');
        if (!panel) return;
        panel.style.display = rssUploadEnabled && rssUploadEnabled.checked ? 'block' : 'none';
    },


    syncRssEndMinute() {
        const rssEndHour = document.getElementById('setting-rss-end-hour');
        const rssEndMinute = document.getElementById('setting-rss-end-minute');
        if (!rssEndHour || !rssEndMinute) return;

        if (parseInt(rssEndHour.value) === 24) {
            rssEndMinute.value = '0';
            rssEndMinute.disabled = true;
        } else {
            rssEndMinute.disabled = false;
        }
    },



    async saveSettings() {
        const dirInput = document.getElementById('setting-download-dir');
        const concurrentInput = document.getElementById('setting-concurrent');
        const saveImagesCheck = document.getElementById('setting-save-images');
        const saveVideosCheck = document.getElementById('setting-save-videos');
        const delayInput = document.getElementById('setting-delay');
        const pageSizeInput = document.getElementById('setting-page-size');
        const maxArticlesInput = document.getElementById('setting-max-articles');
        const maxRetriesInput = document.getElementById('setting-max-retries');
        const chWorkerInput = document.getElementById('setting-channels-worker');
        const deviceIdInput = document.getElementById('setting-device-id');
        const rssStartHour = document.getElementById('setting-rss-start-hour');
        const rssStartMinute = document.getElementById('setting-rss-start-minute');
        const rssEndHour = document.getElementById('setting-rss-end-hour');
        const rssEndMinute = document.getElementById('setting-rss-end-minute');
        const rssUploadEnabled = document.getElementById('setting-rss-upload-enabled');
        const rssUploadUrl = document.getElementById('setting-rss-upload-url');
        
        const biliQuality = document.getElementById('setting-bili-quality');
        const biliDanmaku = document.getElementById('setting-bili-danmaku');
        const biliSubtitle = document.getElementById('setting-bili-subtitle');

        const request_delay = parseFloat(delayInput.value);
        if (isNaN(request_delay) || request_delay < 0.1) {
            Toast.warning('延迟时间输入不合法，最小为 0.1 秒');
            return;
        }

        const settings = {
            ...this.settings,
            download_dir: dirInput.value.trim(),
            concurrent_downloads: parseInt(concurrentInput.value) || 1,
            auto_save_images: saveImagesCheck.checked,
            auto_save_videos: saveVideosCheck.checked,
            request_delay: request_delay,
            page_size: parseInt(pageSizeInput.value) || 10,
            max_articles: parseInt(maxArticlesInput.value) || 50,
            max_retries: parseInt(maxRetriesInput.value) || 3,
            custom_channels_worker: chWorkerInput ? chWorkerInput.value.trim() : '',
            device_id: deviceIdInput ? (deviceIdInput.value.trim() || '公众号_caiji100') : '公众号_caiji100',
            rss_start_hour: rssStartHour ? parseInt(rssStartHour.value) : 0,
            rss_start_minute: rssStartMinute ? parseInt(rssStartMinute.value) : 0,
            rss_end_hour: rssEndHour ? parseInt(rssEndHour.value) : 24,
            rss_end_minute: rssEndMinute && !rssEndMinute.disabled ? parseInt(rssEndMinute.value) : 0,
            rss_upload_enabled: rssUploadEnabled ? rssUploadEnabled.checked : false,
            rss_upload_url: rssUploadUrl ? rssUploadUrl.value.trim() : '',
            bili_video_quality: biliQuality ? biliQuality.value : '1080p',
            bili_download_danmaku: biliDanmaku ? biliDanmaku.checked : true,
            bili_download_subtitle: biliSubtitle ? biliSubtitle.checked : true,
        };

        try {
            await API.settings.save(settings);
            Toast.success('设置已保存');
            this.settings = settings;
        } catch (err) {
            // Error handled by API wrapper
        }
    },



    async resetDefaults() {
        Modal.confirm('恢复默认设置', '确定要将所有配置项恢复为系统默认吗？', async () => {
            const defaults = {
                download_dir: '',
                page_size: 10,
                max_articles: 50,
                max_retries: 3,
                request_delay: 0.8,
                concurrent_downloads: 1,
                auto_save_images: true,
                auto_save_videos: true,
                device_id: '公众号_caiji100',
                rss_start_hour: 0,
                rss_start_minute: 0,
                rss_end_hour: 24,
                rss_end_minute: 0,
                rss_upload_enabled: false,
                rss_upload_url: '',
                bili_video_quality: '1080p',
                bili_download_danmaku: true,
                bili_download_subtitle: true,
            };
            try {
                await API.settings.save(defaults);
                Toast.success('设置已重置为默认值');
                await this.loadSettings();
            } catch (err) {
                // error handled
            }
        });
    },
};
