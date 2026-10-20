/**
 * virtual_list.js - 无依赖、无构建的变高虚拟滚动器（组卷题库流 / 已选题目流共用）
 *
 * 为什么必须手写：本项目是「离线单文件分发 + 零编译级联加载」的前端（AGENTS.md 明令
 * 禁止任何打包动作），static/lib 里只有 vendor 好的 KaTeX/marked/dompurify。引第三方
 * 虚拟滚动库要么上 CDN，要么上一个 vue/react 运行时——两条都违反架构约束。
 *
 * 变高列表的三个真问题，这里各自的解法：
 *   1. 高度未知 → 先用估算值撑起总高（滚动条长度不会突变），条目真正挂载后用
 *      ResizeObserver 量回真实高度，写进**按 key 持久化**的高度表；下一页/换筛选
 *      条件时同一道题直接复用旧高度，滚动条不再来回抖。
 *   2. 上方条目被量高后总高变化 → 视口会整体跳动。用「视口首行」做锚点，量高后
 *      按锚点行的位移量反向补偿 scrollTop，人眼看到的画面一动不动。
 *   3. 白屏 → 滚动事件只排一帧 rAF，窗口在同一帧内同步补齐，且窗口上下各多渲染
 *      overscan 行；快速甩滚动条时也不会出现空白带。
 *
 * 拖拽排序（drag-lock）：拖拽过程中如果窗口把正在被拖的那一行回收掉，HTML5 拖拽
 * 的源节点直接消失，落点计算全部作废。`setDragLock(true)` 期间冻结窗口的增删，
 * 只更新位置；解锁后立刻补算一次。
 */

(function () {
    'use strict';

    const DEFAULT_OVERSCAN = 6;
    const DEFAULT_ESTIMATE = 150;

    function clamp(value, low, high) {
        return Math.max(low, Math.min(high, value));
    }

    /** offsets 是升序前缀和；二分找第一个 offsets[i+1] > target 的 i，即 target 落在第 i 行。 */
    function findRowIndex(offsets, target, count) {
        let low = 0;
        let high = count - 1;
        while (low < high) {
            const mid = (low + high) >> 1;
            if (offsets[mid + 1] <= target) {
                low = mid + 1;
            } else {
                high = mid;
            }
        }
        return low;
    }

    function VirtualList(options) {
        this.scroller = options.scroller;
        this.root = options.root;
        this.renderRow = options.renderRow;
        this.estimateHeight = options.estimateHeight || null;
        this.overscan = options.overscan === undefined ? DEFAULT_OVERSCAN : options.overscan;
        // disabled=true 时窗口不裁剪（全部行都渲染），专供压测做「老架构 vs 新架构」A/B。
        this.disabled = Boolean(options.disabled);
        this.dragLocked = false;
        this.count = 0;
        this.keyOf = null;
        this.keys = [];
        this.heights = [];
        this.offsets = [0];
        this.nodes = new Map();          // index -> HTMLElement
        this._heightByKey = Object.create(null);
        this._rafId = 0;
        this._anchorIndex = -1;
        this._onRangeChange = options.onRangeChange || null;
        this._onScrollInfo = options.onScrollInfo || null;
        // 视口尺寸与 root 在滚动容器内的偏移量：**读它们会强制同步布局**。
        // 每帧各读一次 = 每帧一次全量重排（万题 profile 里 scrollInfo + _viewport
        // 合计 190ms/3.3s）。改成在 reset、窗口尺寸变化、粘顶头部栏自身变化时刷新。
        this._baseTop = 0;
        this._viewportHeight = 0;
        // scrollTop 同理：一帧之内只允许读一次，见 update()。
        this._scrollTop = 0;

        this.root.style.position = 'relative';
        this._observer = typeof ResizeObserver === 'function'
            ? new ResizeObserver(entries => this._handleResize(entries))
            : null;
        this._handleScroll = () => this.scheduleUpdate();
        this.scroller.addEventListener('scroll', this._handleScroll, { passive: true });
        this._handleResizeWindow = () => {
            this._measureChrome();
            this.scheduleUpdate();
        };
        window.addEventListener('resize', this._handleResizeWindow);
        // 粘顶头部栏在 root 之前、参与文档流，它一换内容 root 的 offsetTop 就变；
        // 滚动容器被外层布局改动时 clientHeight 也会变。两处都只能事后重测。
        this._chromeObserver = typeof ResizeObserver === 'function'
            ? new ResizeObserver(() => {
                this._measureChrome();
                this.scheduleUpdate();
            })
            : null;
        this._observeChrome();
        this._measureChrome();
    }

    VirtualList.prototype._observeChrome = function () {
        if (!this._chromeObserver) return;
        this._chromeObserver.observe(this.scroller);
        const header = this.root.previousElementSibling;
        if (header) this._chromeObserver.observe(header);
    };

    VirtualList.prototype._measureChrome = function () {
        // 只在布局稳定时读，不在滚动帧里读。
        this._baseTop = this.root.offsetTop || 0;
        this._viewportHeight = this.scroller.clientHeight || 0;
    };

    /**
     * 换数据源（筛选条件变化、拉到下一页）后重建窗口。
     * keyOf(index) 必须稳定且能反映高度形态（例如 `qid|expanded`），高度缓存靠它复用。
     */
    VirtualList.prototype.reset = function (count, keyOf) {
        const previousKeys = this.keys;
        // 换数据源通常紧跟着一次头部栏/统计条重写，root 的偏移量会跟着变；
        // 这里重测一次，避免窗口区间整体错位（本方法不在每帧热路径上）。
        this._measureChrome();
        this.count = Math.max(0, count || 0);
        this.keyOf = keyOf;
        this.keys = [];
        this.heights = [];
        for (let index = 0; index < this.count; index += 1) {
            const key = String(keyOf(index));
            this.keys[index] = key;
            const cached = this._heightByKey[key];
            this.heights[index] = typeof cached === 'number' && cached > 0
                ? cached
                : this._estimateAt(index);
        }
        this._rebuildOffsets();
        // key 变了就必须重建，否则旧节点会把上一道题的内容留在新的行位置上。
        const stale = [];
        this.nodes.forEach((node, index) => {
            if (previousKeys[index] !== this.keys[index]) stale.push(index);
        });
        // 这里必须强制拆：key 变了代表「这一行的内容要换」（展开/收起答案、水合回完整题面），
        // 而用户正是用键盘/鼠标点在同一张卡上才触发变化的——软拆会因为焦点保护直接跳过，
        // 表现就是「点了答案按钮，卡片一动不动」。
        this._focusRestoreKey = null;
        stale.forEach(index => this._removeNode(index, true));
        this._anchorIndex = -1;
        this.update();
        this._restoreFocus();
    };

    VirtualList.prototype._estimateAt = function (index) {
        if (typeof this.estimateHeight === 'function') {
            const value = this.estimateHeight(index);
            if (typeof value === 'number' && value > 0) return value;
        }
        return DEFAULT_ESTIMATE;
    };

    VirtualList.prototype._rebuildOffsets = function () {
        const offsets = new Array(this.count + 1);
        offsets[0] = 0;
        for (let index = 0; index < this.count; index += 1) {
            offsets[index + 1] = offsets[index] + this.heights[index];
        }
        this.offsets = offsets;
        this.root.style.height = offsets[this.count] + 'px';
    };

    VirtualList.prototype._viewport = function () {
        const top = this._scrollTop - this._baseTop;
        const height = this._viewportHeight;
        return { top: top, bottom: top + height, height: height };
    };

    VirtualList.prototype._rangeFor = function (viewport) {
        if (this.count === 0) return { start: 0, end: -1 };
        if (this.disabled) return { start: 0, end: this.count - 1 };
        const first = findRowIndex(this.offsets, Math.max(0, viewport.top), this.count);
        const last = findRowIndex(this.offsets, Math.max(0, viewport.bottom), this.count);
        return {
            start: clamp(first - this.overscan, 0, this.count - 1),
            end: clamp(last + this.overscan, 0, this.count - 1)
        };
    };

    VirtualList.prototype.scheduleUpdate = function () {
        if (this._rafId) return;
        const self = this;
        this._rafId = window.requestAnimationFrame(function () {
            self._rafId = 0;
            self.update();
        });
    };

    VirtualList.prototype.update = function () {
        if (this.dragLocked) {
            // 拖拽期间只保证位置正确，不增删行（见文件头 drag-lock 说明）。
            this._positionAll();
            return;
        }
        // 本帧唯一一次布局读数。后面 _viewport() / scrollInfo() 一律用这个缓存值：
        // 一帧里 invalidateKeys + resize 回调 + 滚动会各自触发一次 update，
        // 各自再读一次 scrollTop 就是把「强制重排」按帧数乘倍。
        this._scrollTop = this.scroller.scrollTop;
        const viewport = this._viewport();
        const range = this._rangeFor(viewport);
        const anchorBefore = range.start;
        const anchorTopBefore = this.offsets[anchorBefore];

        this.nodes.forEach(function (ignored, index) {
            if (index < range.start || index > range.end) this._removeNode(index);
        }.bind(this));

        for (let index = range.start; index <= range.end; index += 1) {
            if (!this.nodes.has(index)) this._mountNode(index);
        }
        this._positionAll();
        this._anchorIndex = anchorBefore;
        // 锚点行之前的行如果刚被量高，offsets[anchor] 会整体位移；把它加回 scrollTop，
        // 视口内容就完全不动——这是变高虚拟列表「往上滚突然跳一屏」的根因。
        const anchorDrift = this.offsets[anchorBefore] - anchorTopBefore;
        if (anchorDrift > 0.5 || anchorDrift < -0.5) {
            this.scroller.scrollTop += anchorDrift;
        }
        if (typeof this._onRangeChange === 'function') {
            this._onRangeChange(range, this.scrollInfo());
        }
    };

    VirtualList.prototype._mountNode = function (index) {
        const payload = this.renderRow(index);
        if (!payload || !payload.element) return;
        const node = payload.element;
        node.style.position = 'absolute';
        node.style.left = '0';
        node.style.right = '0';
        node.style.top = '0';
        node.dataset.virtualIndex = String(index);
        node.dataset.rowKey = this.keys[index] || '';
        this.root.appendChild(node);
        this.nodes.set(index, node);
        if (this._observer) this._observer.observe(node);
    };

    VirtualList.prototype._removeNode = function (index, force) {
        const node = this.nodes.get(index);
        if (!node) return;
        // 焦点正在这一行里（键盘在滚题目流）：拆掉会让焦点跳到 <body>，用户下一秒就要摸黑。
        // 但「定向失效重挂」不能因为抢不到焦点就不换内容——点在卡片里展开答案时，
        // 焦点本来就停在被点的那个按钮上，拒绝拆除等于答案永远出不来。
        if (!force && document.activeElement && node.contains(document.activeElement)) return;
        if (force && document.activeElement && node.contains(document.activeElement)) {
            this._focusRestoreKey = this.keys[index];
        }
        if (this._observer) this._observer.unobserve(node);
        if (node.parentNode) node.parentNode.removeChild(node);
        this.nodes.delete(index);
    };

    /**
     * 让指定 key 的行重新渲染（数据按需水合回来、答案展开收起之后必须走这里）。
     * 只做 remove + update：update 发现窗口内缺节点会重新挂载，位置与高度缓存都复用。
     */
    VirtualList.prototype.invalidateKeys = function (keys) {
        if (!keys || !keys.length) return;
        const wanted = new Set(keys.map(String));
        const doomed = [];
        const self = this;
        this.nodes.forEach(function (node, index) {
            if (wanted.has(self.keys[index])) doomed.push(index);
        });
        if (!doomed.length) return;
        this._focusRestoreKey = null;
        doomed.forEach(function (index) { self._removeNode(index, true); });
        this.update();
        this._restoreFocus();
    };

    VirtualList.prototype._restoreFocus = function () {
        const key = this._focusRestoreKey;
        if (!key) return;
        this._focusRestoreKey = null;
        const node = this.root.querySelector('[data-row-key="' + key + '"]');
        if (!node) return;
        const target = node.querySelector('[data-focus-return="true"]') || node.querySelector('button');
        if (target) target.focus();
    };

    VirtualList.prototype._positionAll = function () {
        const self = this;
        this.nodes.forEach(function (node, index) {
            node.style.transform = 'translateY(' + self.offsets[index] + 'px)';
        });
    };

    VirtualList.prototype._handleResize = function (entries) {
        if (this.dragLocked) return;
        for (let i = 0; i < entries.length; i += 1) {
            const node = entries[i].target;
            const index = parseInt(node && node.dataset ? node.dataset.virtualIndex : '', 10);
            if (!Number.isFinite(index) || index < 0 || index >= this.count) continue;
            if (this.nodes.get(index) !== node) continue;
            // border-box 高度：卡片有 margin 时取 rect 会漏掉外边距，这里用 offsetHeight。
            const measured = node.offsetHeight;
            if (!measured || Math.abs(measured - this.heights[index]) < 1.5) continue;
            this._heightByKey[this.keys[index]] = measured;
            this.heights[index] = measured;
            for (let walk = index + 1; walk <= this.count; walk += 1) {
                this.offsets[walk] = this.offsets[walk - 1] + this.heights[walk - 1];
            }
            this.root.style.height = this.offsets[this.count] + 'px';
        }
        this.scheduleUpdate();
    };

    VirtualList.prototype.setDragLock = function (locked) {
        this.dragLocked = Boolean(locked);
        if (!this.dragLocked) this.update();
    };

    VirtualList.prototype.scrollInfo = function () {
        const total = this.offsets[this.count] || 0;
        const scrollTop = this._scrollTop;
        const viewportBottom = scrollTop + this._viewportHeight;
        return {
            scrollTop: scrollTop,
            // 用前缀和算，不碰 scrollHeight（读它会强制整页重排）。
            scrollHeight: total + this._baseTop,
            distanceToBottom: Math.max(0, total + this._baseTop - viewportBottom),
            renderedNodes: this.nodes.size,
            progress: total > 0 ? clamp((scrollTop - this._baseTop) / total, 0, 1) : 0
        };
    };

    VirtualList.prototype.scrollToIndex = function (index) {
        this._measureChrome();
        const base = this._baseTop;
        this.scroller.scrollTop = base + (this.offsets[clamp(index, 0, Math.max(0, this.count - 1))] || 0);
        this.update();
    };

    VirtualList.prototype.destroy = function () {
        if (this._rafId) window.cancelAnimationFrame(this._rafId);
        this._rafId = 0;
        this.scroller.removeEventListener('scroll', this._handleScroll);
        window.removeEventListener('resize', this._handleResizeWindow);
        if (this._observer) this._observer.disconnect();
        if (this._chromeObserver) this._chromeObserver.disconnect();
        this.nodes.forEach(function (node) {
            if (node.parentNode) node.parentNode.removeChild(node);
        });
        this.nodes.clear();
    };

    window.MathBankVirtualList = VirtualList;
})();
