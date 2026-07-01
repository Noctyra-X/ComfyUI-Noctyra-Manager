// ComfyUI-Noctyra-Manager
// Copyright (C) 2026 Noctyra
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

/**
 * 虚拟滚动网格（借鉴 ComfyUI-Lora-Manager 的 VirtualScroller）。
 *
 * 核心：
 *   - 一个撑满高度的 spacer = 总行数 × 行高 → 滚动条从头就是「全部模型」的完整高度，
 *     所以滚动条满、中键自动滚动能瞬间到底、无边界等待。
 *   - 卡片绝对定位（top = 行×行高），DOM 里只保留「可视窗口 + overscan」那几行，
 *     滚出视口就 remove → 不论库多大、滚多快，DOM 节点恒定 ~几十张，丝滑。
 *
 * 前提：卡片固定高度（itemH = 卡宽×4/3 + 信息条高），由 cards.css 的 .vg-item 配合。
 * 数据一次性全量载入（items 全在内存），适合几百~几千；超大库可后续再加分页窗口。
 */

const INFO_H = 124;         // 信息条固定高度（与 cards.css 的 .vg-item .card-info 一致！）
                            // 装下标准完整卡：名字+版本+1行徽章+统计+作者≈120；96 会切掉作者行
const DEFAULT_RATIO = 4 / 3; // 预览图默认 3:4 → 高 = 宽 × 4/3（= 高/宽）
const GAP = 20;             // 卡片间距（与原 #card-grid gap 一致）
const COMPACT_GAP = 12;     // 紧凑密度间距（与原 compact gap 一致）

// 从 --card-aspect（形如 "3 / 4" = 宽/高）算「高/宽」比，给 itemH 用。无效则默认 4/3。
function readAspectRatio(el) {
    try {
        const v = getComputedStyle(el).getPropertyValue('--card-aspect').trim();
        const m = v.match(/([\d.]+)\s*\/\s*([\d.]+)/);
        if (m) {
            const w = parseFloat(m[1]), h = parseFloat(m[2]);
            if (w > 0 && h > 0) return h / w;
        }
    } catch (_) { /* 用默认 */ }
    return DEFAULT_RATIO;
}

function rafThrottle(fn) {
    let scheduled = false;
    return function () {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(() => { scheduled = false; fn(); });
    };
}
function debounce(fn, wait) {
    let t;
    return function (...a) { clearTimeout(t); t = setTimeout(() => fn.apply(this, a), wait); };
}

export class VirtualGrid {
    /**
     * @param {HTMLElement} gridEl   容器（原 #card-grid），虚拟模式下作定位上下文
     * @param {HTMLElement} scrollEl 滚动容器（.content-area）
     * @param {(model:object)=>HTMLElement} createItemFn 造一张卡（已带事件/选中态）
     * @param {number} overscanRows 视口上下额外渲染的行数
     */
    constructor(gridEl, scrollEl, createItemFn, overscanRows = 4) {
        this.gridEl = gridEl;
        this.scrollEl = scrollEl;
        this.createItemFn = createItemFn;
        this.overscanRows = overscanRows;

        this.items = [];
        this._indexByPath = new Map();  // file_path -> 下标（updateItem O(1) 定位）
        this.rendered = new Map();      // index -> element
        this.cols = 1;
        this.itemW = 0;
        this.itemH = 0;
        this.gap = GAP;
        this.infoH = INFO_H;
        this.gridOffset = 0;            // grid 顶部在 scroll 内容坐标系里的偏移
        this.lastRange = { start: -1, end: -1 };

        this.gridEl.classList.add('vg-active');
        this.spacer = document.createElement('div');
        this.spacer.className = 'vg-spacer';
        this.gridEl.appendChild(this.spacer);

        this._onScroll = rafThrottle(() => this._render(false));
        this.scrollEl.addEventListener('scroll', this._onScroll, { passive: true });

        this._relayout = debounce(() => this.layout(), 120);
        window.addEventListener('resize', this._relayout);
        if (typeof ResizeObserver !== 'undefined') {
            this._ro = new ResizeObserver(this._relayout);
            this._ro.observe(this.gridEl);
        }
    }

    /** 替换全部数据。集合未变（实时刷新常没发现新模型）则只换数据引用、不动 DOM，
     *  避免滚动浏览时每次刷新都 clear+relayout 整网格造成的闪烁；集合变了才全量重排。 */
    setItems(items) {
        const next = items || [];
        const same = this._sameItems(next);
        this.items = next;
        this._rebuildIndex();
        if (same) return;          // 同一批模型：保留现有 DOM，仅更新数据引用 → 不闪
        this._clearRendered();
        this.layout();
    }

    // 按长度 + file_path 序列判断是否同一批模型（廉价：仅字符串比较）
    _sameItems(next) {
        if (!this.items || next.length !== this.items.length) return false;
        for (let i = 0; i < next.length; i++) {
            const a = next[i] && next[i].file_path;
            const b = this.items[i] && this.items[i].file_path;
            if (a !== b) return false;
        }
        return true;
    }

    // file_path -> 下标，给 updateItem 做 O(1) 定位
    _rebuildIndex() {
        this._indexByPath = new Map();
        for (let i = 0; i < this.items.length; i++) {
            const fp = this.items[i] && this.items[i].file_path;
            if (fp && !this._indexByPath.has(fp)) this._indexByPath.set(fp, i);
        }
    }

    /** 重算列数/卡片尺寸 + spacer，再渲染可视窗口 */
    layout() {
        const gridWidth = this.gridEl.clientWidth;
        if (!gridWidth) return;

        const compact = this.gridEl.dataset.density === 'compact';
        const minCol = compact ? 170 : 240;
        this.gap = compact ? COMPACT_GAP : GAP;
        // 悬停信息模式：信息浮层覆盖在图上，卡片只有预览图那么高
        this.infoH = this.gridEl.dataset.cardInfo === 'hover' ? 0 : INFO_H;

        this.cols = Math.max(1, Math.floor((gridWidth + this.gap) / (minCol + this.gap)));
        this.itemW = (gridWidth - (this.cols - 1) * this.gap) / this.cols;
        this.itemH = this.itemW * readAspectRatio(this.gridEl) + this.infoH;

        // grid 顶部相对滚动内容的偏移（含上方 padding），布局时算一次
        this.gridOffset = this.gridEl.getBoundingClientRect().top
            - this.scrollEl.getBoundingClientRect().top + this.scrollEl.scrollTop;

        const rows = Math.ceil(this.items.length / this.cols);
        const h = rows > 0 ? rows * this.itemH + (rows - 1) * this.gap : 0;
        this.spacer.style.height = `${h}px`;

        // 列数/尺寸变了，已渲染的旧卡 top/left/尺寸都过时 → 全清重建，否则缩放后错位重叠
        this._clearRendered();
        this.lastRange = { start: -1, end: -1 };
        this._render(true);
    }

    _visibleRange() {
        const rowH = this.itemH + this.gap;
        if (rowH <= 0) return { start: 0, end: 0 };
        const top = this.scrollEl.scrollTop - this.gridOffset;
        const vh = this.scrollEl.clientHeight;
        const firstRow = Math.max(0, Math.floor(top / rowH) - this.overscanRows);
        const lastRow = Math.ceil((top + vh) / rowH) + this.overscanRows;
        const start = Math.max(0, firstRow * this.cols);
        const end = Math.min(this.items.length, lastRow * this.cols);
        return { start, end };
    }

    _render(force) {
        if (!this.items.length || !this.cols) return;
        const { start, end } = this._visibleRange();
        if (!force && start === this.lastRange.start && end === this.lastRange.end) return;
        this.lastRange = { start, end };

        // 移除离开窗口的
        for (const [i, el] of this.rendered) {
            if (i < start || i >= end) { el.remove(); this.rendered.delete(i); }
        }
        // 补进入窗口的（批量入 fragment 一次 append）。单帧最多建 MAX_PER_FRAME 张：快速下滑/
        // 跳转时一帧要建满整窗（overscan×列数 ≈ 几十张，createModelCard 较重）会阻塞主线程 →
        // 顿 + 滚轮失灵。超额的留到下一帧续建（用户还在滑则按新窗补），主线程始终不卡死。
        const MAX_PER_FRAME = 30;
        let created = 0;
        const frag = document.createDocumentFragment();
        for (let i = start; i < end; i++) {
            if (this.rendered.has(i)) continue;
            const el = this.createItemFn(this.items[i]);
            this._position(el, i);
            frag.appendChild(el);
            this.rendered.set(i, el);
            if (++created >= MAX_PER_FRAME) break;
        }
        if (frag.childNodes.length) this.gridEl.appendChild(frag);
        // 本帧没建满整窗 → 下一帧继续（重置 lastRange 强制再算）。终会建完（已建的会被 has(i) 跳过）。
        if (created >= MAX_PER_FRAME) {
            this.lastRange = { start: -1, end: -1 };
            requestAnimationFrame(() => this._render(false));
        }
    }

    _position(el, i) {
        const row = Math.floor(i / this.cols);
        const col = i % this.cols;
        el.classList.add('vg-item');
        el.style.position = 'absolute';
        el.style.left = `${col * (this.itemW + this.gap)}px`;
        el.style.top = `${row * (this.itemH + this.gap)}px`;
        el.style.width = `${this.itemW}px`;
        el.style.height = `${this.itemH}px`;
    }

    /** 重渲染当前可视窗口（如选择态变化后需要重画卡片）。 */
    refresh() {
        this._clearRendered();
        this.lastRange = { start: -1, end: -1 };
        this._render(true);
    }

    /** 更新单条数据；若它正在可视窗口里就重画那张卡。 */
    updateItem(filePath, mutate) {
        const i = this._indexByPath ? this._indexByPath.get(filePath) : this.items.findIndex(m => m.file_path === filePath);
        if (i == null || i < 0) return;
        mutate(this.items[i]);
        if (this.rendered.has(i)) {
            const el = this.createItemFn(this.items[i]);
            this._position(el, i);
            this.rendered.get(i).replaceWith(el);
            this.rendered.set(i, el);
        }
    }

    get length() { return this.items.length; }

    /** 滚动到第 i 张卡（字母条 A-Z 跳转用）。 */
    scrollToIndex(i) {
        if (i < 0 || i >= this.items.length || !this.cols) return;
        // 实时重算 grid 顶部偏移：上方筛选条/标签可能变高，缓存的 gridOffset 会过期 → 跳偏
        const offset = this.gridEl.getBoundingClientRect().top
            - this.scrollEl.getBoundingClientRect().top + this.scrollEl.scrollTop;
        const row = Math.floor(i / this.cols);
        const top = Math.max(0, offset + row * (this.itemH + this.gap) - 8);
        // 远距离跳转用瞬时：smooth 会逐帧渲染沿途经过的每一窗卡片（卡），且动画期间滚轮失灵；
        // 仅近距离（≤2 屏）才平滑，体验好又不卡。
        const behavior = Math.abs(top - this.scrollEl.scrollTop) > this.scrollEl.clientHeight * 2
            ? 'auto' : 'smooth';
        this.scrollEl.scrollTo({ top, behavior });
    }

    _clearRendered() {
        for (const el of this.rendered.values()) el.remove();
        this.rendered.clear();
    }

    destroy() {
        this.scrollEl.removeEventListener('scroll', this._onScroll);
        window.removeEventListener('resize', this._relayout);
        if (this._ro) this._ro.disconnect();
        this._clearRendered();
        if (this.spacer) this.spacer.remove();
        this.gridEl.classList.remove('vg-active');
    }
}
