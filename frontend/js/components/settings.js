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

                        <div class="form-group" style="display: flex; flex-wrap: wrap; gap: 20px; margin-top: var(--spacing-md);">
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="setting-save-images" style="width: 18px; height: 18px; accent-color: var(--primary);" />
                                <span>自动下载文章图片</span>
                            </label>
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="setting-save-videos" style="width: 18px; height: 18px; accent-color: var(--primary);" />
                                <span>自动下载文章视频</span>
                            </label>
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="setting-save-markdown" style="width: 18px; height: 18px; accent-color: var(--primary);" />
                                <span>自动导出 Markdown (.md)</span>
                            </label>
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="setting-save-pdf" style="width: 18px; height: 18px; accent-color: var(--primary);" />
                                <span>自动导出 PDF (.pdf)</span>
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

                <!-- 视频号解析配置（公众号版由 mp-mode.js 隐藏） -->
                <div class="card" data-mp-hide>
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

                <!-- 微信读书中转服务配置（公众号版直连官方，由 mp-mode.js 隐藏） -->
                <div class="card" data-mp-hide>
                    <div class="card-header">
                        <h3 class="card-title">📖 微信读书中转服务配置</h3>
                    </div>
                    <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);">
                        <div class="form-group" style="margin-top: var(--spacing-md);">
                            <label class="form-label" for="setting-weread-url">中转服务地址 (weread_platform_url)</label>
                            <input type="text" class="form-input" id="setting-weread-url" placeholder="https://weread.111965.xyz" />
                            <div class="form-hint">默认为公共中转站。如遇频繁限流或超时，可自建 we-mp-rss 并在填入私有服务地址（如 http://IP:8080）。</div>
                        </div>
                    </div>
                </div>

                <!-- B站下载配置（公众号版由 mp-mode.js 隐藏） -->
                <div class="card" data-mp-hide>
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

                <!-- RSS Feed 输出服务与外部访问配置 -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">🌐 RSS Feed 订阅服务与公网安全</h3>
                    </div>
                    <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);">
                        <div class="form-group" style="margin-top: var(--spacing-md);">
                            <label class="form-label" for="setting-rss-base-url">对外基准地址 (RSS_BASE_URL)</label>
                            <input type="url" class="form-input" id="setting-rss-base-url" placeholder="例如：https://rss.example.com （反代后的外部域名，留空则自动跟随当前访问地址）" />
                            <div class="form-hint">当使用 Lucky / Nginx 反向代理时，请填写外部访问的完整协议与域名。配置后，前端弹出的 RSS 复制地址与 XML 内容中的图片附件链接将始终使用此基准地址。</div>
                        </div>
                        <div class="form-group" style="margin-top: var(--spacing-md);">
                            <label class="form-label" for="setting-rss-token">RSS 订阅安全令牌 (RSS_TOKEN)</label>
                            <input type="text" class="form-input" id="setting-rss-token" placeholder="自定义安全令牌密钥（留空则公开可读）" />
                            <div class="form-hint">设置后，RSS 订阅接口必须附加 ?token=xxx 才能拉取内容。前端复制订阅链接时会自动携带该令牌。</div>
                        </div>
                        <div class="form-group" style="margin-top: var(--spacing-md);">
                            <label class="form-label" for="setting-admin-password">Web 管理后台访问密码 (ADMIN_PASSWORD)</label>
                            <input type="password" class="form-input" id="setting-admin-password" placeholder="留空则内网免密；设置后访问管理后台需输入密码" />
                            <div class="form-hint">若将整个服务通过 Lucky 反代暴露至公网，强烈建议配置此密码（启用 HTTP Basic 密码保护），防止管理后台被未授权访问。</div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- NSFW 内容审核配置 -->
            <div class="card" id="nsfw-settings-card" style="margin-top: var(--spacing-lg);">
                <div class="card-header">
                    <h3 class="card-title">🛡️ 内容审核（NSFW）</h3>
                </div>
                <div class="card-body">
                    <div id="nsfw-stale-banner" style="display: none; margin-bottom: var(--spacing-md); padding: 10px 14px; border-radius: 8px; background: rgba(255,170,60,.12); color: var(--warning, #c98a1d); font-size: 0.88rem; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;">
                        <span id="nsfw-stale-text"></span>
                        <button class="btn btn-secondary" style="padding: 4px 12px;" onclick="SettingsPage.recomputeNsfw()">按新阈值重算</button>
                    </div>

                    <div class="form-group">
                        <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                            <input type="checkbox" id="nsfw-auto-scan" style="width: 18px; height: 18px; accent-color: var(--primary);" />
                            <span>自动初筛（新入库图片自动过模型打分）</span>
                        </label>
                        <div class="form-hint">关闭后扫描不再跑模型；已有结论保留，只是不再新增</div>
                    </div>

                    <div class="form-group">
                        <label class="form-label">敏感阈值</label>
                        <div style="display: flex; gap: 16px; flex-wrap: wrap; margin: 6px 0;">
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 6px; cursor: pointer;">
                                <input type="radio" name="nsfw-threshold-preset" value="loose" onchange="SettingsPage.onNsfwThresholdPreset(this)" /> <span>宽松 (0.65)</span>
                            </label>
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 6px; cursor: pointer;">
                                <input type="radio" name="nsfw-threshold-preset" value="standard" onchange="SettingsPage.onNsfwThresholdPreset(this)" /> <span>标准 (0.5)</span>
                            </label>
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 6px; cursor: pointer;">
                                <input type="radio" name="nsfw-threshold-preset" value="strict" onchange="SettingsPage.onNsfwThresholdPreset(this)" /> <span>严格 (0.35)</span>
                            </label>
                            <label class="form-checkbox-label" style="display: flex; align-items: center; gap: 6px; cursor: pointer;">
                                <input type="radio" name="nsfw-threshold-preset" value="custom" onchange="SettingsPage.onNsfwThresholdPreset(this)" /> <span>自定义</span>
                            </label>
                            <input type="number" class="form-input" id="nsfw-threshold-input" min="0" max="1" step="0.01" style="width: 100px;" onchange="SettingsPage.onNsfwThresholdInput()" />
                        </div>
                        <div class="form-hint">score ≥ 阈值判敏感。宽松漏网少误伤多，严格反之；调整只影响新扫描，历史结论可一键重算</div>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="nsfw-provider-select">鉴黄方案</label>
                        <select class="form-input" id="nsfw-provider-select" onchange="SettingsPage.onNsfwProviderChange()" style="max-width: 320px;"></select>
                        <div class="form-hint" id="nsfw-provider-hint"></div>
                    </div>

                    <div class="form-group" id="nsfw-coverage-line" style="color: var(--text-muted); font-size: 0.88rem;"></div>

                    <div style="display: flex; justify-content: flex-end; margin-top: var(--spacing-md);">
                        <button class="btn btn-primary" onclick="SettingsPage.saveNsfwSettings()">保存并应用</button>
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
        await this.loadNsfwSettings();
    },

    /** SPA 路由复用缓存页时调用，重新从后端拉取最新设置 */
    async onShow() {
        await this.loadSettings();
        await this.loadNsfwSettings();
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
        const saveMarkdownCheck = document.getElementById('setting-save-markdown');
        const savePdfCheck = document.getElementById('setting-save-pdf');
        const delayRange = document.getElementById('setting-delay-range');
        const delayInput = document.getElementById('setting-delay');
        const pageSizeInput = document.getElementById('setting-page-size');
        const maxArticlesInput = document.getElementById('setting-max-articles');
        const maxRetriesInput = document.getElementById('setting-max-retries');
        const chWorkerInput = document.getElementById('setting-channels-worker');
        const wereadUrlInput = document.getElementById('setting-weread-url');
        const deviceIdInput = document.getElementById('setting-device-id');
        const rssStartHour = document.getElementById('setting-rss-start-hour');
        const rssStartMinute = document.getElementById('setting-rss-start-minute');
        const rssEndHour = document.getElementById('setting-rss-end-hour');
        const rssEndMinute = document.getElementById('setting-rss-end-minute');
        const rssUploadEnabled = document.getElementById('setting-rss-upload-enabled');
        const rssUploadUrl = document.getElementById('setting-rss-upload-url');
        const rssBaseUrl = document.getElementById('setting-rss-base-url');
        const rssToken = document.getElementById('setting-rss-token');
        const adminPassword = document.getElementById('setting-admin-password');
        
        const biliQuality = document.getElementById('setting-bili-quality');
        const biliDanmaku = document.getElementById('setting-bili-danmaku');
        const biliSubtitle = document.getElementById('setting-bili-subtitle');

        if (dirInput) dirInput.value = data.download_dir || '';
        if (concurrentInput) concurrentInput.value = data.concurrent_downloads || 1;
        if (saveImagesCheck) saveImagesCheck.checked = data.auto_save_images !== undefined ? !!data.auto_save_images : true;
        if (saveVideosCheck) saveVideosCheck.checked = data.auto_save_videos !== undefined ? !!data.auto_save_videos : true;
        if (saveMarkdownCheck) saveMarkdownCheck.checked = data.save_markdown !== undefined ? !!data.save_markdown : true;
        if (savePdfCheck) savePdfCheck.checked = data.save_pdf !== undefined ? !!data.save_pdf : true;
        if (delayInput) delayInput.value = data.request_delay || 0.8;
        if (delayRange) delayRange.value = data.request_delay || 0.8;
        if (pageSizeInput) pageSizeInput.value = data.page_size || 10;
        if (maxArticlesInput) maxArticlesInput.value = data.max_articles || 50;
        if (maxRetriesInput) maxRetriesInput.value = data.max_retries || 3;
        if (chWorkerInput) chWorkerInput.value = data.custom_channels_worker || '';
        if (wereadUrlInput) wereadUrlInput.value = data.weread_platform_url || 'https://weread.111965.xyz';
        if (deviceIdInput) deviceIdInput.value = data.device_id || '公众号_caiji100';
        if (rssStartHour) rssStartHour.value = data.rss_start_hour !== undefined ? data.rss_start_hour : 0;
        if (rssStartMinute) rssStartMinute.value = data.rss_start_minute !== undefined ? data.rss_start_minute : 0;
        if (rssEndHour) rssEndHour.value = data.rss_end_hour !== undefined ? data.rss_end_hour : 24;
        if (rssEndMinute) rssEndMinute.value = data.rss_end_minute !== undefined ? data.rss_end_minute : 0;
        if (rssUploadEnabled) rssUploadEnabled.checked = data.rss_upload_enabled !== undefined ? !!data.rss_upload_enabled : false;
        if (rssUploadUrl) rssUploadUrl.value = data.rss_upload_url || '';
        if (rssBaseUrl) rssBaseUrl.value = data.rss_base_url || '';
        if (rssToken) rssToken.value = data.rss_token || '';
        if (adminPassword) adminPassword.value = data.admin_password || '';
        
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
        const saveMarkdownCheck = document.getElementById('setting-save-markdown');
        const savePdfCheck = document.getElementById('setting-save-pdf');
        const delayInput = document.getElementById('setting-delay');
        const pageSizeInput = document.getElementById('setting-page-size');
        const maxArticlesInput = document.getElementById('setting-max-articles');
        const maxRetriesInput = document.getElementById('setting-max-retries');
        const chWorkerInput = document.getElementById('setting-channels-worker');
        const wereadUrlInput = document.getElementById('setting-weread-url');
        const deviceIdInput = document.getElementById('setting-device-id');
        const rssStartHour = document.getElementById('setting-rss-start-hour');
        const rssStartMinute = document.getElementById('setting-rss-start-minute');
        const rssEndHour = document.getElementById('setting-rss-end-hour');
        const rssEndMinute = document.getElementById('setting-rss-end-minute');
        const rssUploadEnabled = document.getElementById('setting-rss-upload-enabled');
        const rssUploadUrl = document.getElementById('setting-rss-upload-url');
        const rssBaseUrl = document.getElementById('setting-rss-base-url');
        const rssToken = document.getElementById('setting-rss-token');
        const adminPassword = document.getElementById('setting-admin-password');
        
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
            save_markdown: saveMarkdownCheck ? saveMarkdownCheck.checked : true,
            save_pdf: savePdfCheck ? savePdfCheck.checked : true,
            request_delay: request_delay,
            page_size: parseInt(pageSizeInput.value) || 10,
            max_articles: parseInt(maxArticlesInput.value) || 50,
            max_retries: parseInt(maxRetriesInput.value) || 3,
            custom_channels_worker: chWorkerInput ? chWorkerInput.value.trim() : '',
            weread_platform_url: (wereadUrlInput ? wereadUrlInput.value.trim() : '') || 'https://weread.111965.xyz',
            device_id: deviceIdInput ? (deviceIdInput.value.trim() || '公众号_caiji100') : '公众号_caiji100',
            rss_start_hour: rssStartHour ? parseInt(rssStartHour.value) : 0,
            rss_start_minute: rssStartMinute ? parseInt(rssStartMinute.value) : 0,
            rss_end_hour: rssEndHour ? parseInt(rssEndHour.value) : 24,
            rss_end_minute: rssEndMinute && !rssEndMinute.disabled ? parseInt(rssEndMinute.value) : 0,
            rss_upload_enabled: rssUploadEnabled ? rssUploadEnabled.checked : false,
            rss_upload_url: rssUploadUrl ? rssUploadUrl.value.trim() : '',
            rss_base_url: rssBaseUrl ? rssBaseUrl.value.trim() : '',
            rss_token: rssToken ? rssToken.value.trim() : '',
            admin_password: adminPassword ? adminPassword.value.trim() : '',
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
                rss_base_url: '',
                rss_token: '',
                admin_password: '',
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

    // ── NSFW 配置管理 ─────────────────────────────────

    nsfwSettingsCache: null,
    /** 三档预设值，与后端 NSFW_THRESHOLD_PRESETS 一致，radio value → 阈值 */
    nsfwPresetValues: { loose: 0.65, standard: 0.5, strict: 0.35 },

    async loadNsfwSettings() {
        try {
            const data = await API.media.nsfwSettings({ showError: false });
            this.nsfwSettingsCache = data;
            this.renderNsfwSettings(data);
        } catch (err) {
            const line = document.getElementById('nsfw-coverage-line');
            if (line) line.textContent = '⚠️ 媒体服务不可用，无法加载 NSFW 配置';
        }
    },

    renderNsfwSettings(data) {
        const autoScan = document.getElementById('nsfw-auto-scan');
        const thresholdInput = document.getElementById('nsfw-threshold-input');
        const providerSelect = document.getElementById('nsfw-provider-select');
        const providerHint = document.getElementById('nsfw-provider-hint');
        const banner = document.getElementById('nsfw-stale-banner');
        const staleText = document.getElementById('nsfw-stale-text');
        const coverageLine = document.getElementById('nsfw-coverage-line');
        if (!autoScan || !providerSelect) return;   // 渲染时机：卡片还在页面上才填 DOM

        // 锁定字段（部署环境变量覆盖时）置灰并注明来源
        autoScan.disabled = !!data.locked.auto_scan;
        thresholdInput.disabled = !!data.locked.threshold;
        document.querySelectorAll('input[name="nsfw-threshold-preset"]').forEach(el => {
            el.disabled = !!data.locked.threshold;
        });

        autoScan.checked = !!data.auto_scan;
        thresholdInput.value = data.threshold;

        const matchedPreset = Object.entries(this.nsfwPresetValues)
            .find(([, value]) => Math.abs(value - data.threshold) < 1e-6);
        document.querySelectorAll('input[name="nsfw-threshold-preset"]').forEach(el => {
            el.checked = el.value === (matchedPreset ? matchedPreset[0] : 'custom');
        });

        providerSelect.innerHTML = (data.providers || []).map(p => {
            const mark = p.installed ? '' : '（未安装）';
            return `<option value="${p.id}">${p.label}${mark}</option>`;
        }).join('');
        providerSelect.value = data.provider;
        const current = (data.providers || []).find(p => p.id === data.provider);
        providerHint.textContent = current ? current.score_desc + (current.installed ? '' : ' · 权重未安装，保存后将无法扫描，直到权重就位') : '';

        if (data.stale && (data.stale.threshold || data.stale.provider)) {
            banner.style.display = 'flex';
            const reasons = [];
            if (data.stale.threshold) reasons.push(`阈值已从 ${data.applied_threshold} 调整为 ${data.threshold}`);
            if (data.stale.provider) reasons.push('鉴黄方案已更换');
            let text = `${reasons.join('，')}：已有 ${data.stale.pending} 张图片的结论仍按旧口径`;
            if (data.stale.provider) {
                text += '。换方案后分数语义不同，只能重扫、不能重算（文件页 → 全局检测）';
                banner.querySelector('button').style.display = 'none';
            } else {
                text += '。可按已存分数秒级重算，无需重扫';
                banner.querySelector('button').style.display = '';
            }
            staleText.textContent = text;
        } else {
            banner.style.display = 'none';
        }

        coverageLine.textContent = `已初筛 ${data.coverage.scored}/${data.coverage.total} 张图片` +
            (data.coverage.pending ? `，待筛 ${data.coverage.pending} 张` : '，全部完成');
    },

    onNsfwThresholdPreset(el) {
        if (el.value !== 'custom') {
            document.getElementById('nsfw-threshold-input').value = this.nsfwPresetValues[el.value];
        }
    },

    onNsfwThresholdInput() {
        // 手改数字后如果不在预设档位上，radio 回到自定义
        const raw = parseFloat(document.getElementById('nsfw-threshold-input').value);
        const matched = Object.values(this.nsfwPresetValues).some(v => Math.abs(v - raw) < 1e-6);
        document.querySelectorAll('input[name="nsfw-threshold-preset"]').forEach(el => {
            el.checked = matched ? el.value !== 'custom' && Math.abs(this.nsfwPresetValues[el.value] - raw) < 1e-6 : el.value === 'custom';
        });
    },

    onNsfwProviderChange() {
        const data = this.nsfwSettingsCache;
        if (!data) return;
        const current = (data.providers || []).find(p => p.id === document.getElementById('nsfw-provider-select').value);
        const hint = document.getElementById('nsfw-provider-hint');
        if (current) hint.textContent = current.score_desc + (current.installed ? '' : ' · 权重未安装，保存后将无法扫描，直到权重就位');
    },

    async saveNsfwSettings() {
        const data = this.nsfwSettingsCache;
        const payload = {};
        // 锁定字段由部署环境变量决定，PUT 会被 409 拒绝，直接不发
        if (data && !data.locked.auto_scan) {
            payload.auto_scan = document.getElementById('nsfw-auto-scan').checked;
        }
        if (data && !data.locked.threshold) {
            const raw = parseFloat(document.getElementById('nsfw-threshold-input').value);
            if (!Number.isFinite(raw) || raw < 0 || raw > 1) {
                Toast.error('阈值必须在 0–1 之间');
                return;
            }
            payload.threshold = raw;
        }
        payload.provider = document.getElementById('nsfw-provider-select').value;

        try {
            const updated = await API.media.nsfwSettingsSave(payload);
            this.nsfwSettingsCache = updated;
            this.renderNsfwSettings(updated);
            Toast.success('NSFW 配置已保存，从下一次扫描开始生效');
        } catch (err) {
            // 错误提示由 API wrapper 统一弹出（含 409 锁定、422 校验失败）
        }
    },

    async recomputeNsfw() {
        try {
            const result = await API.media.nsfwRecompute();
            Toast.success(`重算完成：${result.updated} 条结论更新，${result.skipped} 条不变`);
            await this.loadNsfwSettings();
        } catch (err) {
            // 错误提示由 API wrapper 统一弹出
        }
    },
};
