/* 共享图片查看器（普通大图 + VR 全景）。
 *
 * 从 media_search 抽取，供多个页面复用：media_search 用前缀 ms，全局文件检索用 fv。
 * 前缀只作用于 DOM id（真正的同名冲突点），样式类沿用 .ms-viewer 系列，避免复制一套 CSS。
 *
 * 查看器本身不感知业务：标签读取/写入、NSFW 复核等页面特有行为由页面通过 handlers 注入。
 * 全景口径与媒体服务自带页一致：等距柱状图贴到内向球体，上下立体只取上半幅。
 */
class MediaViewer {
    constructor(prefix = 'ms') {
        this.prefix = prefix;
        this._viewer = null;
        this._keys = null;
        this._three = null;
        this._threePromise = null;
        this._tagSeq = 0;
    }

    /** 按前缀取 DOM id，避免同页多个查看器（理论上不会同时存在）互相踩踏。 */
    _id(name) {
        return `${this.prefix}-${name}`;
    }

    /** 大图地址。列表项已带 file 时直接用，否则按文件行拼 /media/api/media/file。 */
    _fileUrl(image) {
        if (image && image.file) return `/media${image.file}`;
        const version = image && image.content_version != null ? image.content_version : '';
        return `/media/api/media/file?root=${encodeURIComponent(image.root || '')}`
            + `&relpath=${encodeURIComponent(image.relpath || '')}`
            + `&version=${encodeURIComponent(version)}&display=1`;
    }

    open(images, index, handlers = {}) {
        this.close();
        if (!images || !images.length) return;
        const box = document.createElement('div');
        box.className = 'ms-viewer';
        const withReview = typeof handlers.getNsfw === 'function';
        const withTags = typeof handlers.loadTags === 'function';
        box.innerHTML = `
            <div class="ms-viewer-panel" role="dialog" aria-modal="true" aria-label="图片查看">
                <div class="ms-viewer-bar">
                    <span class="form-hint" id="${this._id('viewer-count')}"></span>
                    <span class="ms-viewer-spacer"></span>
                    <button class="btn btn-secondary btn-sm" data-act="prev">上一张</button>
                    <button class="btn btn-secondary btn-sm" data-act="next">下一张</button>
                    <span class="ms-viewer-pano-controls" id="${this._id('pano-controls')}" hidden>
                        <label class="form-hint">全景模式
                            <select class="form-input" id="${this._id('pano-mode')}">
                                <option value="mono">单目</option>
                                <option value="stereo">上下立体（取上半幅）</option>
                            </select>
                        </label>
                        <button class="btn btn-secondary btn-sm" data-act="pano-exit">退出全景</button>
                    </span>
                    ${withReview ? `<span class="ms-viewer-review" id="${this._id('viewer-review')}" hidden>
                        <button class="btn btn-secondary btn-sm" data-act="review-nsfw">确认敏感</button>
                        <button class="btn btn-secondary btn-sm" data-act="review-safe">标为误报</button>
                    </span>` : ''}
                    <button class="btn btn-primary btn-sm" data-act="pano">全景查看</button>
                    <button class="btn btn-secondary btn-sm" data-act="close">关闭</button>
                </div>
                <div class="ms-viewer-stage" id="${this._id('stage')}">
                    <img id="${this._id('viewer-img')}" alt="" draggable="false">
                    <div class="ms-viewer-pano" id="${this._id('viewer-pano')}" hidden></div>
                    <div class="ms-viewer-lens" id="${this._id('lens')}" hidden><img alt="" draggable="false"></div>
                </div>
                <div class="ms-viewer-zoom" id="${this._id('zoom-bar')}" role="toolbar" aria-label="缩放与旋转">
                    <button type="button" class="ms-zoom-btn" data-act="zoom-out" title="缩小（-）" aria-label="缩小">−</button>
                    <button type="button" class="ms-zoom-btn ms-zoom-value" data-act="zoom-reset" id="${this._id('zoom-value')}" title="还原（0）" aria-label="还原到 100%">100%</button>
                    <button type="button" class="ms-zoom-btn" data-act="zoom-in" title="放大（+）" aria-label="放大">＋</button>
                    <span class="ms-zoom-sep" aria-hidden="true"></span>
                    <button type="button" class="ms-zoom-btn" data-act="rotate-left" title="逆时针旋转（Shift+R）" aria-label="逆时针旋转">⟲</button>
                    <button type="button" class="ms-zoom-btn" data-act="rotate-right" title="顺时针旋转（R）" aria-label="顺时针旋转">⟳</button>
                    <button type="button" class="ms-zoom-btn" data-act="lens" id="${this._id('zoom-lens')}" title="局部放大镜（M）" aria-label="局部放大镜" aria-pressed="false">放大镜</button>
                </div>
                ${withTags ? `<div class="ms-viewer-tags" id="${this._id('viewer-tags')}">
                    <span class="ms-tag-chips" id="${this._id('tag-chips')}"></span>
                    <input class="form-input ms-tag-input" id="${this._id('tag-input')}" type="text"
                           maxlength="32" placeholder="加标签后回车" autocomplete="off">
                </div>` : ''}
                <p class="form-hint" id="${this._id('viewer-status')}" role="status" hidden></p>
            </div>`;
        document.body.appendChild(box);
        this._handlers = handlers;
        // baseW/baseH 是按当前旋转角算出的「适配尺寸」，scale/tx/ty 都叠加在它之上：
        // 缩放不改变布局尺寸，只改 transform，舞台大小才不会跟着图片抖。
        this._viewer = {
            box, images, index: 0, seq: 0, pano: null,
            view: { scale: 1, rotate: 0, tx: 0, ty: 0, lens: false, baseW: 0, baseH: 0 },
        };

        this._bindStage();
        this._onResize = () => this.layout();
        window.addEventListener('resize', this._onResize);
        const viewerImg = this._el('viewer-img');
        if (viewerImg) viewerImg.addEventListener('load', () => this.layout());

        box.addEventListener('click', (event) => {
            if (event.target === box) {
                this.close();
                return;
            }
            const act = event.target.closest('[data-act]');
            if (!act) return;
            const name = act.dataset.act;
            if (name === 'close') this.close();
            else if (name === 'prev') this.step(-1);
            else if (name === 'next') this.step(1);
            else if (name === 'pano') this.panorama();
            else if (name === 'pano-exit') this.exitPano();
            else if (name === 'review-nsfw') this.review('nsfw');
            else if (name === 'review-safe') this.review('safe');
            else if (name === 'tag-remove') this.editTag(act.dataset.tag, 'remove');
            else if (name === 'zoom-in') this.zoomBy(1.25);
            else if (name === 'zoom-out') this.zoomBy(1 / 1.25);
            else if (name === 'zoom-reset') this.resetView();
            else if (name === 'rotate-left') this.rotateBy(-1);
            else if (name === 'rotate-right') this.rotateBy(1);
            else if (name === 'lens') this.toggleLens();
        });

        const mode = document.getElementById(this._id('pano-mode'));
        if (mode) mode.addEventListener('change', () => this.applyPanoMode());
        const tagInput = document.getElementById(this._id('tag-input'));
        if (tagInput) tagInput.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter') return;
            event.preventDefault(); // 回车只提交标签，别冒泡出去触发别的提交
            this.editTag(tagInput.value, 'add');
        });

        this._keys = (event) => {
            if (!this._viewer) return;
            if (event.key === 'Escape') {
                this.close();
                return;
            }
            // 标签输入框里敲 r/0/- 不该触发旋转或缩放
            const tag = event.target && event.target.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (event.target && event.target.isContentEditable)) return;
            if (this._viewer.pano) return; // 全景下方向键留给场景拖动
            if (event.key === 'ArrowLeft') this.step(-1);
            if (event.key === 'ArrowRight') this.step(1);
            if (event.key === '+' || event.key === '=') this.zoomBy(1.25);
            if (event.key === '-' || event.key === '_') this.zoomBy(1 / 1.25);
            if (event.key === '0') this.resetView();
            if (event.key === 'r' || event.key === 'R') this.rotateBy(event.shiftKey ? -1 : 1);
            if (event.key === 'm' || event.key === 'M') this.toggleLens();
        };
        document.addEventListener('keydown', this._keys);
        this.show(index);
    }

    show(index) {
        const viewer = this._viewer;
        if (!viewer) return;
        const total = viewer.images.length;
        viewer.index = ((Math.trunc(index) % total) + total) % total;
        const image = viewer.images[viewer.index];
        this.exitPano();
        const img = document.getElementById(this._id('viewer-img'));
        const count = document.getElementById(this._id('viewer-count'));
        if (count) count.textContent = `${viewer.index + 1} / ${total} · ${image.relpath || ''}`;
        if (img) {
            const url = this._fileUrl(image);
            // 同一张图重复 show（比如复核后刷新状态）不该把用户的缩放/旋转重置掉
            if (img.getAttribute('src') !== url) {
                img.src = url;
                this.resetView();
            }
            img.alt = image.relpath || '';
            img.hidden = false;
        }
        this._syncViewChrome();
        const handlers = this._handlers || {};
        const nsfw = typeof handlers.getNsfw === 'function' ? handlers.getNsfw(image) : null;
        const risky = !!(nsfw && nsfw.label === 'nsfw');
        const reviewed = !!(nsfw && nsfw.stage === 'second');
        const score = nsfw && nsfw.score != null ? Number(nsfw.score).toFixed(3) : '—';
        this.status(risky
            ? (reviewed ? `人工复核：确认敏感（分数 ${score}）` : `初筛 疑似 NSFW（${score}）`)
            : (reviewed ? '人工复核：已标为误报' : ''));
        // 有机器初筛结论才提供复核入口：没有证据的图不该被随手标记
        const reviewBox = document.getElementById(this._id('viewer-review'));
        if (reviewBox) reviewBox.hidden = !nsfw;
        this.loadTags(image);
    }

    /** 人工复核当前大图：写 second 级结论，机器重扫只动 first 行，不会覆盖它。 */
    async review(label) {
        const viewer = this._viewer;
        const handlers = this._handlers || {};
        if (!viewer || typeof handlers.review !== 'function') return;
        const image = viewer.images[viewer.index];
        if (!image || !image.root || !image.relpath) return;
        const box = document.getElementById(this._id('viewer-review'));
        const buttons = box ? box.querySelectorAll('button') : [];
        buttons.forEach((button) => { button.disabled = true; });
        try {
            await handlers.review(image, label);
            this.show(viewer.index); // 状态行与按钮跟着新结论刷新
        } catch (err) {
            const Toast = window.Toast;
            if (Toast && Toast.error) Toast.error('复核失败：' + (err && err.message ? err.message : '请求失败'));
        } finally {
            buttons.forEach((button) => { button.disabled = false; });
        }
    }

    /** 当前大图的标签。数据由页面注入的 loadTags 提供，查看器只管渲染与代次防串。 */
    async loadTags(image) {
        const chips = document.getElementById(this._id('tag-chips'));
        const handlers = this._handlers || {};
        if (!chips || typeof handlers.loadTags !== 'function') return;
        const seq = ++this._tagSeq;
        this.renderTags([]);
        if (!image || !image.root || !image.relpath) return;
        try {
            const tags = await handlers.loadTags(image);
            if (seq !== this._tagSeq) return; // 切图之后回来的旧响应不许覆盖新图
            this.renderTags(tags);
        } catch (err) {
            if (seq === this._tagSeq) this.renderTags([]);
        }
    }

    /** 用 DOM 构建而不是拼 HTML：标签是用户输入，拼字符串就得额外做转义。 */
    renderTags(tags) {
        const chips = document.getElementById(this._id('tag-chips'));
        if (!chips) return;
        chips.textContent = '';
        if (!tags || !tags.length) {
            const hint = document.createElement('span');
            hint.className = 'form-hint';
            hint.textContent = '未打标签';
            chips.appendChild(hint);
            return;
        }
        tags.forEach((tag) => {
            const chip = document.createElement('span');
            chip.className = 'ms-tag-chip';
            chip.textContent = tag;
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.dataset.act = 'tag-remove';
            remove.dataset.tag = tag;
            remove.title = '移除标签';
            remove.textContent = '×';
            chip.appendChild(remove);
            chips.appendChild(chip);
        });
    }

    /** 打标签 / 去标签；成功后用后端返回的完整列表刷新，不在前端猜增删结果。 */
    async editTag(tag, mode) {
        const viewer = this._viewer;
        const handlers = this._handlers || {};
        const text = (tag || '').trim();
        if (!viewer || !text || typeof handlers.saveTag !== 'function') return;
        const image = viewer.images[viewer.index];
        if (!image || !image.root || !image.relpath) return;
        const input = document.getElementById(this._id('tag-input'));
        if (input) input.disabled = true;
        try {
            const tags = await handlers.saveTag(image, text, mode);
            if (input && mode === 'add') input.value = '';
            this.renderTags(tags);
            if (typeof handlers.onTagsChanged === 'function') handlers.onTagsChanged();
        } catch (err) {
            const Toast = window.Toast;
            if (Toast && Toast.error) Toast.error('标签保存失败：' + (err && err.message ? err.message : '请求失败'));
        } finally {
            if (input) { input.disabled = false; input.focus(); }
        }
    }

    step(delta) {
        const viewer = this._viewer;
        if (!viewer) return;
        this.show(viewer.index + delta);
    }

    // ── 缩放 / 旋转 / 局部放大镜 ─────────────────────────
    /** 按后缀取查看器内元素。 */
    _el(name) {
        return document.getElementById(this._id(name));
    }

    _viewState() {
        return this._viewer ? this._viewer.view : null;
    }

    /** 舞台上的手势：滚轮缩放（以光标为锚点）、放大后拖动、双击切换、放大镜跟随。 */
    _bindStage() {
        const stage = this._el('stage');
        if (!stage) return;
        let drag = null;
        stage.addEventListener('pointerdown', (event) => {
            const view = this._viewState();
            if (!view || !this._viewer || this._viewer.pano || event.button !== 0) return;
            if (event.target.closest('[data-act]')) return; // 控件上的按下不当作拖动
            if (view.scale <= 1.001) return; // 未放大时画面没有可拖动的余量
            drag = { id: event.pointerId, x: event.clientX, y: event.clientY, tx: view.tx, ty: view.ty };
            if (stage.setPointerCapture) stage.setPointerCapture(event.pointerId);
            stage.classList.add('is-panning');
            this.hideLens();
        });
        stage.addEventListener('pointermove', (event) => {
            const view = this._viewState();
            if (!view) return;
            this._pointer = { x: event.clientX, y: event.clientY };
            if (drag && drag.id === event.pointerId) {
                view.tx = drag.tx + (event.clientX - drag.x);
                view.ty = drag.ty + (event.clientY - drag.y);
                this.applyView();
                return;
            }
            this.updateLensAt(event.clientX, event.clientY);
        });
        const endDrag = (event) => {
            if (!drag || (event && drag.id !== event.pointerId)) return;
            const pointerId = drag.id;
            drag = null;
            stage.classList.remove('is-panning');
            if (stage.releasePointerCapture && stage.hasPointerCapture && stage.hasPointerCapture(pointerId)) {
                stage.releasePointerCapture(pointerId);
            }
        };
        stage.addEventListener('pointerup', endDrag);
        stage.addEventListener('pointercancel', endDrag);
        stage.addEventListener('pointerleave', () => this.hideLens());
        stage.addEventListener('wheel', (event) => {
            const view = this._viewState();
            if (!view || !this._viewer || this._viewer.pano) return;
            event.preventDefault();
            // 指数缩放：触控板的细碎 deltaY 也能平滑跟手
            this.zoomTo(view.scale * Math.exp(-event.deltaY * 0.002), event.clientX, event.clientY);
        }, { passive: false });
        stage.addEventListener('dblclick', (event) => {
            const view = this._viewState();
            if (!view || !this._viewer || this._viewer.pano) return;
            if (event.target.closest('[data-act]')) return; // 连点按钮不该当成双击缩放
            if (view.scale > 1.05) this.resetView();
            else this.zoomTo(2.5, event.clientX, event.clientY);
        });
    }

    /**
     * 按舞台尺寸与当前旋转重算「适配尺寸」：rotate=0 时就是 contain，
     * 旋转后按外接框重新装进舞台，避免 90° 后两侧被裁掉。
     */
    layout() {
        const viewer = this._viewer;
        const view = this._viewState();
        const img = this._el('viewer-img');
        const stage = this._el('stage');
        if (!viewer || !view || !img || !stage || img.hidden) return;
        if (!img.naturalWidth || !img.naturalHeight) return;
        const boxW = Math.max(80, stage.clientWidth - 24);
        const boxH = Math.max(80, stage.clientHeight - 24);
        const rad = (view.rotate * Math.PI) / 180;
        const cos = Math.abs(Math.cos(rad));
        const sin = Math.abs(Math.sin(rad));
        const ratio = img.naturalWidth / img.naturalHeight;
        // 外接框宽 = bw*cos + bh*sin，高 = bw*sin + bh*cos（bw = ratio*bh）
        let bh = Math.min(boxW / (ratio * cos + sin), boxH / (ratio * sin + cos));
        bh = Math.min(bh, img.naturalHeight); // 小图不为了铺满而放大
        view.baseW = bh * ratio;
        view.baseH = bh;
        img.style.maxWidth = 'none';
        img.style.maxHeight = 'none';
        img.style.width = `${view.baseW}px`;
        img.style.height = `${view.baseH}px`;
        this.hideLens(); // 尺寸变了，镜片里那份旧采样先撤掉
        this.applyView();
    }

    applyView() {
        const view = this._viewState();
        const stage = this._el('stage');
        const img = this._el('viewer-img');
        if (!view) return;
        this._syncViewChrome();
        if (!stage || !img || !view.baseW) return;
        const rad = (view.rotate * Math.PI) / 180;
        const cos = Math.abs(Math.cos(rad));
        const sin = Math.abs(Math.sin(rad));
        // 平移上限按旋转后的外接框算：放大后最多把图拖到边缘贴齐，不能拖丢
        const dispW = (view.baseW * cos + view.baseH * sin) * view.scale;
        const dispH = (view.baseW * sin + view.baseH * cos) * view.scale;
        const maxX = Math.max(0, (dispW - stage.clientWidth) / 2);
        const maxY = Math.max(0, (dispH - stage.clientHeight) / 2);
        view.tx = Math.max(-maxX, Math.min(maxX, view.tx));
        view.ty = Math.max(-maxY, Math.min(maxY, view.ty));
        img.style.transform = `translate(${view.tx.toFixed(2)}px, ${view.ty.toFixed(2)}px)`
            + ` rotate(${view.rotate}deg) scale(${view.scale})`;
    }

    /** 百分比、光标形态与放大镜按钮状态；与 transform 解耦，图片还没加载时也能先同步。 */
    _syncViewChrome() {
        const view = this._viewState();
        if (!view) return;
        const value = this._el('zoom-value');
        if (value) value.textContent = `${Math.round(view.scale * 100)}%`;
        const stage = this._el('stage');
        if (stage) {
            stage.classList.toggle('is-zoomed', view.scale > 1.001);
            stage.classList.toggle('is-lens', !!view.lens);
        }
        const lensBtn = this._el('zoom-lens');
        if (lensBtn) lensBtn.setAttribute('aria-pressed', view.lens ? 'true' : 'false');
        if (!view.lens) this.hideLens();
    }

    /**
     * 以光标为锚点缩放到 nextScale：光标下的那块内容保持不动，
     * 这也是淘宝/京东商品图滚轮缩放的手感来源。
     */
    zoomTo(nextScale, clientX, clientY) {
        const view = this._viewState();
        const stage = this._el('stage');
        if (!view || !stage) return;
        const next = Math.max(1, Math.min(8, nextScale));
        if (Math.abs(next - view.scale) < 0.001) return;
        const rect = stage.getBoundingClientRect();
        const qx = (clientX == null ? rect.width / 2 : clientX - rect.left) - rect.width / 2;
        const qy = (clientY == null ? rect.height / 2 : clientY - rect.top) - rect.height / 2;
        const rad = (view.rotate * Math.PI) / 180;
        const cos = Math.cos(rad);
        const sin = Math.sin(rad);
        // 光标下的图内点（相对图片中心、未缩放的基准坐标）
        const dx = qx - view.tx;
        const dy = qy - view.ty;
        const px = (dx * cos + dy * sin) / view.scale;
        const py = (-dx * sin + dy * cos) / view.scale;
        view.scale = next;
        if (next <= 1.0001) {
            view.scale = 1;
            view.tx = 0;
            view.ty = 0;
        } else {
            view.tx = qx - next * (px * cos - py * sin);
            view.ty = qy - next * (px * sin + py * cos);
        }
        this.applyView();
        // 键盘/按钮缩放没有光标坐标，镜片就按最后一次鼠标位置重新取样
        if (clientX == null && this._pointer) this.updateLensAt(this._pointer.x, this._pointer.y);
        else this.updateLensAt(clientX, clientY);
    }

    zoomBy(factor) {
        const view = this._viewState();
        if (!view) return;
        this.zoomTo(view.scale * factor, null, null);
    }

    resetView() {
        const view = this._viewState();
        const img = this._el('viewer-img');
        if (!view || !img) return;
        view.scale = 1;
        view.rotate = 0;
        view.tx = 0;
        view.ty = 0;
        view.baseW = 0;
        view.baseH = 0;
        // 先撤掉内联尺寸，加载中的新图回到 CSS 的 contain 兜底
        img.style.width = '';
        img.style.height = '';
        img.style.maxWidth = '';
        img.style.maxHeight = '';
        img.style.transform = '';
        this.hideLens();
        this.layout(); // 图片已加载时会立刻按新方向重新适配
        this._syncViewChrome();
    }

    rotateBy(delta) {
        const view = this._viewState();
        if (!view) return;
        view.rotate = ((view.rotate + delta * 90) % 360 + 360) % 360;
        view.tx = 0; // 适配尺寸随旋转变了，平移归零，画面不会跑出可视区
        view.ty = 0;
        this.layout();
        this.hideLens();
    }

    toggleLens() {
        const view = this._viewState();
        const stage = this._el('stage');
        if (!view) return;
        view.lens = !view.lens;
        this.hideLens();
        this.applyView();
        // 打开镜片就把它摆在舞台中心，不用等鼠标移动才出现
        if (view.lens && stage) {
            const rect = stage.getBoundingClientRect();
            this.updateLensAt(rect.left + rect.width / 2, rect.top + rect.height / 2);
        }
    }

    hideLens() {
        const lens = this._el('lens');
        if (lens) lens.hidden = true;
    }

    /**
     * 局部放大镜：镜片是 180px 的圆，内容按「当前缩放 × 2、2–8 倍封顶」重画。
     * 镜内图用与舞台相同的基准尺寸再放大，浏览器会从原图采样，不会把已缩小的位图二次放大。
     */
    updateLensAt(clientX, clientY) {
        const viewer = this._viewer;
        const view = this._viewState();
        const lens = this._el('lens');
        const stage = this._el('stage');
        if (!viewer || !view || !lens || !stage) return;
        if (!view.lens || viewer.pano || !view.baseW || clientX == null) {
            lens.hidden = true;
            return;
        }
        const rect = stage.getBoundingClientRect();
        const x = clientX - rect.left;
        const y = clientY - rect.top;
        if (x < 0 || y < 0 || x > rect.width || y > rect.height) {
            lens.hidden = true;
            return;
        }
        const rad = (view.rotate * Math.PI) / 180;
        const cos = Math.cos(rad);
        const sin = Math.sin(rad);
        const qx = x - rect.width / 2;
        const qy = y - rect.height / 2;
        const dx = qx - view.tx;
        const dy = qy - view.ty;
        const px = (dx * cos + dy * sin) / view.scale;
        const py = (-dx * sin + dy * cos) / view.scale;
        const zoom = Math.min(8, Math.max(2, view.scale * 2));
        const inner = lens.querySelector('img');
        const src = this._el('viewer-img');
        if (inner) {
            const url = src ? src.getAttribute('src') : '';
            if (url && inner.getAttribute('src') !== url) inner.src = url;
            inner.style.width = `${view.baseW}px`;
            inner.style.height = `${view.baseH}px`;
            inner.style.marginLeft = `${-view.baseW / 2}px`;
            inner.style.marginTop = `${-view.baseH / 2}px`;
            // 让光标下的图内点 (px,py) 落在镜片中心
            const tx = -zoom * (px * cos - py * sin);
            const ty = -zoom * (px * sin + py * cos);
            inner.style.transform = `translate(${tx.toFixed(2)}px, ${ty.toFixed(2)}px)`
                + ` rotate(${view.rotate}deg) scale(${zoom})`;
        }
        lens.style.left = `${x}px`;
        lens.style.top = `${y}px`;
        lens.hidden = false;
    }

    async panorama() {
        const viewer = this._viewer;
        if (!viewer) return;
        const image = viewer.images[viewer.index];
        this.exitPano();
        // 代次必须在第一个 await 之前建立：等待 Three.js 期间退出或切图要能作废本次加载，
        // 否则回来时会往已经关掉的查看器里塞场景。
        const token = ++viewer.seq;
        this.status('正在加载全景…');
        let THREE = null;
        try {
            THREE = await this.loadThree();
        } catch (err) {
            if (this._viewer === viewer && token === viewer.seq) {
                this.status('Three.js 加载失败：' + (err && err.message ? err.message : '网络不可用'));
            }
            return;
        }
        if (this._viewer !== viewer || token !== viewer.seq) return;
        this.buildPano(THREE, image, viewer);
    }

    async loadThree() {
        if (this._three) return this._three;
        if (!this._threePromise) {
            // 固定版本，与媒体服务自带页保持一致；只在首次点「全景查看」时才下载
            this._threePromise = import('https://unpkg.com/three@0.160.0/build/three.module.min.js')
                .then((mod) => {
                    this._three = mod;
                    return mod;
                })
                .catch((err) => {
                    this._threePromise = null;
                    throw err;
                });
        }
        return this._threePromise;
    }

    buildPano(THREE, image, viewer) {
        const stage = document.getElementById(this._id('viewer-pano'));
        const img = document.getElementById(this._id('viewer-img'));
        if (!stage || !viewer) return;
        const width = stage.clientWidth || 800;
        const height = stage.clientHeight || 450;
        const renderer = new THREE.WebGLRenderer({ antialias: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.setSize(width, height, false);
        stage.appendChild(renderer.domElement);
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(90, width / height, 0.1, 1100);
        const geometry = new THREE.SphereGeometry(500, 64, 40);
        geometry.scale(-1, 1, 1); // 法线朝内：等于把相机放在球心往外看
        const texture = new THREE.TextureLoader().load(this._fileUrl(image), () => {
            if (viewer.pano && viewer.pano.texture === texture) this.status('拖动旋转，滚轮缩放');
        }, undefined, () => {
            if (viewer.pano && viewer.pano.texture === texture) this.status('全景原图加载失败');
        });
        if (THREE.SRGBColorSpace) texture.colorSpace = THREE.SRGBColorSpace;
        const material = new THREE.MeshBasicMaterial({ map: texture });
        const mesh = new THREE.Mesh(geometry, material);
        scene.add(mesh);
        const pano = {
            renderer, scene, camera, geometry, material, texture, mesh,
            yaw: 0, pitch: 0, fov: 90, mode: 'mono', raf: 0,
        };
        viewer.pano = pano;
        stage.hidden = false;
        if (img) img.hidden = true;
        // 全景是独立的 3D 视口，平面缩放/旋转控件在这里没有意义
        const zoomBar = this._el('zoom-bar');
        if (zoomBar) zoomBar.hidden = true;
        this.hideLens();
        const controls = document.getElementById(this._id('pano-controls'));
        if (controls) controls.hidden = false;
        this.applyPanoMode();
        const canvas = renderer.domElement;
        let dragging = false;
        let lastX = 0;
        let lastY = 0;
        const onDown = (event) => {
            dragging = true;
            lastX = event.clientX;
            lastY = event.clientY;
            if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);
        };
        const onMove = (event) => {
            if (!dragging) return;
            pano.yaw += (event.clientX - lastX) * 0.0045;
            const limit = Math.PI / 2 - 0.05;
            pano.pitch = Math.max(-limit, Math.min(limit, pano.pitch + (event.clientY - lastY) * 0.0045));
            lastX = event.clientX;
            lastY = event.clientY;
        };
        const onUp = () => { dragging = false; };
        const onWheel = (event) => {
            event.preventDefault();
            pano.fov = Math.max(35, Math.min(100, pano.fov + Math.sign(event.deltaY) * 4));
            camera.fov = pano.fov;
            camera.updateProjectionMatrix();
        };
        canvas.addEventListener('pointerdown', onDown);
        canvas.addEventListener('pointermove', onMove);
        canvas.addEventListener('pointerup', onUp);
        canvas.addEventListener('pointerleave', onUp);
        canvas.addEventListener('wheel', onWheel, { passive: false });
        pano.detach = () => {
            canvas.removeEventListener('pointerdown', onDown);
            canvas.removeEventListener('pointermove', onMove);
            canvas.removeEventListener('pointerup', onUp);
            canvas.removeEventListener('pointerleave', onUp);
            canvas.removeEventListener('wheel', onWheel);
        };
        const resize = () => {
            if (!viewer.pano) return;
            const w = stage.clientWidth || width;
            const h = stage.clientHeight || height;
            renderer.setSize(w, h, false);
            camera.aspect = w / h;
            camera.updateProjectionMatrix();
        };
        pano.resize = resize;
        window.addEventListener('resize', resize);
        const loop = () => {
            if (!viewer.pano || viewer.pano !== pano) return;
            camera.rotation.order = 'YXZ';
            camera.rotation.y = pano.yaw;
            camera.rotation.x = pano.pitch;
            renderer.render(scene, camera);
            pano.raf = requestAnimationFrame(loop);
        };
        loop();
    }

    applyPanoMode() {
        const viewer = this._viewer;
        const pano = viewer && viewer.pano;
        if (!pano) return;
        const select = document.getElementById(this._id('pano-mode'));
        pano.mode = select ? select.value : 'mono';
        if (pano.mode === 'stereo') {
            // 上下立体：v 取 [0.5,1]，即整幅图的上半副眼图
            pano.texture.repeat.y = 0.5;
            pano.texture.offset.y = 0.5;
        } else {
            pano.texture.repeat.y = 1;
            pano.texture.offset.y = 0;
        }
        pano.texture.needsUpdate = true;
    }

    exitPano() {
        const viewer = this._viewer;
        if (!viewer) return;
        const pano = viewer.pano;
        viewer.pano = null;
        viewer.seq += 1; // 作废仍在等待的加载
        this.disposePano(pano);
        const stage = document.getElementById(this._id('viewer-pano'));
        if (stage) {
            stage.hidden = true;
            stage.innerHTML = '';
        }
        const img = document.getElementById(this._id('viewer-img'));
        if (img) img.hidden = false;
        const zoomBar = this._el('zoom-bar');
        if (zoomBar) zoomBar.hidden = false;
        const controls = document.getElementById(this._id('pano-controls'));
        if (controls) controls.hidden = true;
        if (pano) this.status('');
    }

    disposePano(pano) {
        if (!pano) return;
        if (pano.raf) cancelAnimationFrame(pano.raf);
        if (pano.resize) window.removeEventListener('resize', pano.resize);
        if (pano.detach) pano.detach();
        try { pano.scene.remove(pano.mesh); } catch (err) { /* 忽略：资源已释放 */ }
        try { pano.geometry.dispose(); } catch (err) { /* 同上 */ }
        try { pano.texture.dispose(); } catch (err) { /* 同上 */ }
        try { pano.material.dispose(); } catch (err) { /* 同上 */ }
        const canvas = pano.renderer && pano.renderer.domElement;
        try { pano.renderer.dispose(); } catch (err) { /* 同上 */ }
        if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
    }

    status(msg) {
        const el = document.getElementById(this._id('viewer-status'));
        if (!el) return;
        el.textContent = msg || '';
        el.hidden = !msg;
    }

    close() {
        const viewer = this._viewer;
        if (!viewer) return;
        this._viewer = null;
        viewer.seq += 1;
        if (this._keys) {
            document.removeEventListener('keydown', this._keys);
            this._keys = null;
        }
        if (this._onResize) {
            window.removeEventListener('resize', this._onResize);
            this._onResize = null;
        }
        const pano = viewer.pano;
        viewer.pano = null;
        this.disposePano(pano);
        if (viewer.box && viewer.box.parentNode) viewer.box.parentNode.removeChild(viewer.box);
    }
}

window.MediaViewer = MediaViewer;
