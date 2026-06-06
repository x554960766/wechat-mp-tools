/**
 * 系统设置页面组件
 */
const SettingsPage = {
    settings: {},
    cookiePollTimer: null,

    render() {
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
