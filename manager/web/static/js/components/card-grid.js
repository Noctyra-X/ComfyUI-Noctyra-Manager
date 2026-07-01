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
 * 模型卡片网格 + 无限滚动
 *
 * 性能优化：视口外卡片由浏览器原生的 CSS `content-visibility: auto` 跳过布局/绘制
 * （见 cards.css 的 .model-card），无需手写 DOM 回收。无限滚动仅负责按页拉取，
 * 避免一次性请求全部模型。
 */
import { state } from '../state.js';
import * as api from '../api.js';
import { createModelCard, loadCardMedia } from './model-card.js';
import { openDetailModal } from './modal.js';
import { showToast, showOpError } from './toast.js';
import { openCompare } from './compare.js';
import { VirtualGrid } from './virtual-grid.js';

let gridEl = null;
let scrollEl = null;
let vgrid = null;
let loadGeneration = 0;

// ---- A-Z 字母条：按名排序时，点字母跳到该首字母的第一个模型 ----
const ALPHA_LETTERS = ['#', ...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'];
let alphaIndex = new Map();   // 首字母 -> state.models 里的首个下标

function _alphaKey(m) {
    // 与服务端排序键对齐：model_name 排序用显示名，否则用文件名
    return state.currentSort === 'model_name'
        ? (m.model_name || m.file_name || '')
        : (m.file_name || m.model_name || '');
}
function _firstLetter(s) {
    const c = (s || '').trim().charAt(0).toUpperCase();
    return (c >= 'A' && c <= 'Z') ? c : '#';   // 数字/符号/中文/空 → 归到 #
}

function updateAlphaBar() {
    const bar = document.getElementById('alpha-bar');
    if (!bar) return;
    // 仅按名称【升序】排序时才有意义：降序时 A 段在数组末尾，跳"首个 A"会落到段尾→隐藏更干净；
    // 按时间/大小排序时跳字母也无意义。
    const nameSort = (state.currentSort === 'file_name' || state.currentSort === 'model_name')
        && state.currentSortDir !== 'desc';
    if (!nameSort || !state.models.length) { bar.hidden = true; return; }

    alphaIndex = new Map();
    for (let i = 0; i < state.models.length; i++) {
        const L = _firstLetter(_alphaKey(state.models[i]));
        if (!alphaIndex.has(L)) alphaIndex.set(L, i);
    }
    bar.innerHTML = ALPHA_LETTERS.map(L => {
        const has = alphaIndex.has(L);
        return `<button class="alpha-letter${has ? '' : ' disabled'}" data-letter="${L}"${has ? '' : ' disabled'} tabindex="-1">${L}</button>`;
    }).join('');
    bar.hidden = false;
}

function initAlphaBar() {
    const bar = document.getElementById('alpha-bar');
    if (!bar) return;
    bar.addEventListener('click', (e) => {
        const btn = e.target.closest('.alpha-letter');
        if (!btn || btn.disabled) return;
        const i = alphaIndex.get(btn.dataset.letter);
        if (i != null) vgrid?.scrollToIndex(i);
    });
}

export function initCardGrid() {
    gridEl = document.getElementById('card-grid');
    if (!gridEl) return;
    scrollEl = gridEl.closest('.content-area') || gridEl.parentElement || document.scrollingElement;

    // 幂等：重复调用 initCardGrid 时先销毁旧实例，避免叠加多个 scroll listener / spacer。
    if (vgrid) { vgrid.destroy(); vgrid = null; }

    // 虚拟滚动：一次性载入全部数据，DOM 里只保留可视窗口的几十张卡。
    // createItemFn 顺带登记懒加载（预览未命中→占位+有界重试的自愈逻辑在懒加载里）。
    vgrid = new VirtualGrid(gridEl, scrollEl, (m) => {
        const el = createModelCard(m);
        loadCardMedia(el);   // 立即载图（卡片已在视口附近），不用观察器，避免泄漏
        return el;
    });

    // 卡片点击委托
    gridEl.addEventListener('click', e => {
        if (e.target.closest('.card-fav-btn')) return;
        const card = e.target.closest('.model-card');
        if (!card) return;
        if (state.selectMode) { toggleCardSelection(card); return; }
        // 用唯一的 file_path 打开详情（重复哈希时按 sha256 会张冠李戴）
        const identifier = card.dataset.filePath || card.dataset.sha256;
        if (identifier) openDetailModal(identifier);
    });

    // refresh-list: 过滤器变了 → 重新拉取（回顶）。
    // refresh-cards: 纯呈现设置变了(模糊/阈值/自动播放/密度/比例/信息模式) → 只重排重建可视卡片，
    //   不重新拉取、保留滚动。layout() 会按当前 dataset/CSS 变量重算并用 createModelCard 重建。
    window.addEventListener('noctyra-refresh-list', () => loadModels());
    window.addEventListener('noctyra-refresh-cards', () => vgrid?.layout());

    initAlphaBar();
}

// 加载中又来新请求时，记下「待办」，当前这次完成后立刻补跑最新状态——
// 否则后台扫描刷新占着 isLoading 时，用户切筛选/搜索会被直接丢弃（"点了没反应"）。
let _reloadQueued = false;
let _queuedKeepScroll = true;

export async function loadModels(keepScroll = false) {
    if (!vgrid) return;   // initCardGrid 尚未建好虚拟网格则跳过（init 会再调）
    if (state.isLoading) {
        _reloadQueued = true;
        if (!keepScroll) _queuedKeepScroll = false;   // 用户主动操作优先（回顶）
        return;
    }
    state.isLoading = true;
    const gen = ++loadGeneration;
    // 慢加载才显示骨架(后台刷新 keepScroll 不显示,避免打断浏览)
    let skTimer = (!keepScroll) ? setTimeout(showSkeleton, 180) : null;

    try {
        const res = await api.fetchModels(buildQueryParams());
        if (skTimer) { clearTimeout(skTimer); skTimer = null; }
        hideSkeleton();   // 必须在 setItems 前恢复网格显示，否则 layout 读不到宽度
        if (gen !== loadGeneration) return;
        if (!res.success) { showToast(res.error || '加载失败', 'error'); return; }

        state.models = res.models || [];
        state.total = res.total || state.models.length;
        state.page = 1;
        state.totalPages = 1;

        if (!state.models.length) {
            vgrid.setItems([]);
            showEmptyState();
        } else {
            removeEmptyState();
            // 筛选/搜索/排序变了默认回顶（否则结果集变短时 scrollTop 钳到底，看到的是末尾）；
            // 后台扫描的实时刷新传 keepScroll=true 以保留浏览位置。
            if (!keepScroll && scrollEl) scrollEl.scrollTop = 0;
            vgrid.setItems(state.models);
        }
        updateResultCount();
        updateAlphaBar();
    } catch (e) {
        showToast('网络错误: ' + e.message, 'error');
    } finally {
        if (skTimer) clearTimeout(skTimer);
        hideSkeleton();   // 异常路径兜底恢复网格
        state.isLoading = false;
        if (_reloadQueued) {
            _reloadQueued = false;
            const ks = _queuedKeepScroll;
            _queuedKeepScroll = true;
            loadModels(ks);
        }
    }
}

/**
 * 滚动定位到某个模型的卡片（供画布选择器的「打开详情」深链用：关闭详情后停在该卡片，不用翻找）。
 * identifier 可为 file_path 或 sha256。命中返回 true。
 */
export function scrollToModel(identifier) {
    if (!vgrid || !identifier) return false;
    const i = state.models.findIndex(m =>
        m.file_path === identifier || (m.sha256 && m.sha256 === identifier));
    if (i < 0) return false;
    vgrid.scrollToIndex(i);
    return true;
}

/**
 * 更新某个模型的数据并同步可视卡片（如详情弹窗里改了收藏）。
 * vgrid.items 与 state.models 是同一数组引用，改其一即同步，且会重画在可视窗口里的那张卡。
 */
export function updateGridItem(filePath, patch) {
    vgrid?.updateItem(filePath, (item) => Object.assign(item, patch));
}

function buildQueryParams() {
    const params = {
        page: 1,
        page_size: 100000,   // 虚拟滚动一次性载入全部，前端只渲染可视窗口
        sort_by: state.currentSort,
        sort_dir: state.currentSortDir,
        search: state.currentSearch,
        folder: state.currentFolder,
        base_model: state.currentBaseModel,
        source: state.currentSource,
        model_type: state.currentModelType,
        lora_subtype: state.currentLoraSubtype,
        tag: state.currentTag,
        license: state.currentLicense,
    };
    if (state.settings && state.settings.show_only_sfw) params.sfw_only = 1;
    if (state.currentPreviewStatus) params.preview_status = state.currentPreviewStatus;
    return params;
}

function _hasActiveFilters() {
    return !!(state.currentSearch || state.currentFolder || state.currentBaseModel ||
        state.currentModelType || state.currentLoraSubtype || state.currentTag ||
        state.currentLicense || state.currentPreviewStatus || state.currentSource);
}

function showEmptyState() {
    if (!gridEl || gridEl.querySelector('.empty-state')) return;
    const filtered = _hasActiveFilters();
    const el = document.createElement('div');
    el.className = 'empty-state';
    if (filtered) {
        // 筛选/搜索无结果：语义是"没匹配项"，不是"空库"——给清空筛选
        el.innerHTML = `
            <svg class="empty-state-icon" width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
            <div class="empty-state-title">没有符合条件的模型</div>
            <div class="empty-state-sub">试试调整或清空当前筛选条件</div>
            <button class="btn btn-primary" id="empty-clear-filters">清空筛选</button>`;
        el.querySelector('#empty-clear-filters').addEventListener('click', () => {
            document.getElementById('btn-clear-filters')?.click();
        });
    } else {
        // 真·空库:给扫描入口
        el.innerHTML = `
            <svg class="empty-state-icon" width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>
            <div class="empty-state-title">还没有模型</div>
            <div class="empty-state-sub">扫描你的模型目录,把本地模型导入管理器</div>
            <button class="btn btn-primary" id="empty-scan">扫描模型目录</button>`;
        el.querySelector('#empty-scan').addEventListener('click', () => {
            document.getElementById('btn-scan')?.click();
        });
    }
    gridEl.appendChild(el);
}
function removeEmptyState() {
    gridEl?.querySelector('.empty-state')?.remove();
}

// 加载骨架:慢加载(>180ms)才显示占位卡,快加载不闪。显示时临时藏起网格。
function showSkeleton() {
    if (!gridEl || !gridEl.parentElement) return;
    let sk = document.getElementById('card-skeleton');
    if (!sk) {
        sk = document.createElement('div');
        sk.id = 'card-skeleton';
        sk.className = 'skeleton-grid';
        sk.setAttribute('aria-hidden', 'true');
        sk.innerHTML = Array.from({ length: 12 },
            () => '<div class="skeleton-card"><div class="skeleton-shimmer"></div></div>').join('');
        gridEl.parentElement.insertBefore(sk, gridEl);
    }
    sk.hidden = false;
    gridEl.style.display = 'none';
}
function hideSkeleton() {
    const sk = document.getElementById('card-skeleton');
    if (sk) sk.hidden = true;
    if (gridEl) gridEl.style.display = '';
}

function updateResultCount() {
    const el = document.getElementById('result-count');
    if (el) el.textContent = `${state.total} 个模型`;
}

// ========== 批量选择 ==========

function toggleCardSelection(card) {
    const fp = card.dataset.filePath;
    if (!fp) return;

    if (state.selectedModels.has(fp)) {
        state.selectedModels.delete(fp);
        card.classList.remove('selected');
        const cb = card.querySelector('.card-checkbox');
        if (cb) cb.classList.remove('checked');
    } else {
        state.selectedModels.add(fp);
        card.classList.add('selected');
        const cb = card.querySelector('.card-checkbox');
        if (cb) cb.classList.add('checked');
    }
    updateBatchBar();
}

export function enterSelectMode() {
    state.selectMode = true;
    state.selectedModels.clear();
    document.getElementById('card-grid')?.classList.add('select-mode');
    showBatchBar();
    vgrid?.refresh();   // 重渲染可视卡片以显示勾选框，保留滚动位置（无需重新拉取）
}

export function exitSelectMode() {
    state.selectMode = false;
    state.selectedModels.clear();
    document.getElementById('card-grid')?.classList.remove('select-mode');
    hideBatchBar();
    vgrid?.refresh();
}

export function selectAll() {
    for (const m of state.models) if (m.file_path) state.selectedModels.add(m.file_path);
    // 重渲染可视窗口的卡片以反映选中态；视口外的卡滚到时会按 selectedModels 自动渲染选中
    vgrid?.refresh();
    updateBatchBar();
}

export function deselectAll() {
    state.selectedModels.clear();
    vgrid?.refresh();
    updateBatchBar();
}

function showBatchBar() {
    let bar = document.getElementById('batch-bar');
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'batch-bar';
        bar.className = 'batch-bar';
        bar.innerHTML = `
            <div class="batch-bar-left">
                <span class="batch-count">已选择 0 个</span>
                <button class="btn btn-sm" id="batch-select-all">全选当前页</button>
                <button class="btn btn-sm" id="batch-deselect">取消全选</button>
            </div>
            <div class="batch-bar-right">
                <button class="btn btn-sm" id="batch-compare-btn" style="display:none">对比</button>
                <button class="btn btn-sm" id="batch-tag-btn" title="给选中模型批量添加标签">打标签</button>
                <button class="btn btn-sm" id="batch-basemodel-btn" title="把选中模型的 base_model 统一改掉">设 base_model</button>
                <button class="btn btn-sm" id="batch-move-btn" title="把选中模型移动到指定文件夹">移动</button>
                <button class="btn btn-sm" id="batch-refresh-btn">重新匹配</button>
                <button class="btn btn-sm btn-danger" id="batch-delete-btn">删除</button>
                <button class="btn btn-sm" id="batch-exit">退出选择</button>
            </div>
        `;
        document.body.appendChild(bar);

        bar.querySelector('#batch-select-all').addEventListener('click', selectAll);
        bar.querySelector('#batch-deselect').addEventListener('click', deselectAll);
        bar.querySelector('#batch-exit').addEventListener('click', exitSelectMode);
        bar.querySelector('#batch-delete-btn').addEventListener('click', batchDeleteConfirm);
        bar.querySelector('#batch-refresh-btn').addEventListener('click', batchRefreshAction);
        bar.querySelector('#batch-tag-btn').addEventListener('click', batchTagAction);
        bar.querySelector('#batch-basemodel-btn').addEventListener('click', batchSetBaseModelAction);
        bar.querySelector('#batch-move-btn').addEventListener('click', batchMoveAction);
        bar.querySelector('#batch-compare-btn').addEventListener('click', () => {
            const paths = [...state.selectedModels];
            if (paths.length === 2) openCompare(paths[0], paths[1]);
        });
    }
    bar.classList.add('show');
    updateBatchBar();
}

function hideBatchBar() {
    const bar = document.getElementById('batch-bar');
    if (bar) bar.classList.remove('show');
}

function updateBatchBar() {
    const count = state.selectedModels.size;
    const el = document.querySelector('.batch-count');
    if (el) el.textContent = `已选择 ${count} 个`;

    const cmpBtn = document.getElementById('batch-compare-btn');
    if (cmpBtn) cmpBtn.style.display = count === 2 ? '' : 'none';
}

async function batchDeleteConfirm() {
    const count = state.selectedModels.size;
    if (count === 0) { showToast('请先选择模型', 'warning'); return; }

    const overlay = document.createElement('div');
    overlay.className = 'delete-confirm-overlay';
    overlay.innerHTML = `
        <div class="delete-confirm">
            <h3>批量删除</h3>
            <p>确认要删除选中的 <strong>${count}</strong> 个模型吗？</p>
            <label class="delete-file-option">
                <input type="checkbox" id="batch-delete-file-check">
                同时删除本地文件（不可恢复）
            </label>
            <div class="delete-confirm-actions">
                <button class="btn" id="batch-del-cancel">取消</button>
                <button class="btn btn-danger" id="batch-del-confirm">删除 ${count} 个</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    overlay.querySelector('#batch-del-cancel').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

    overlay.querySelector('#batch-del-confirm').addEventListener('click', async () => {
        const deleteFiles = overlay.querySelector('#batch-delete-file-check').checked;
        overlay.remove();
        showToast(`正在删除 ${count} 个模型...`, 'info');
        const res = await api.batchDelete([...state.selectedModels], deleteFiles);
        if (res.success) {
            showToast(`已删除 ${res.deleted} 个模型`, 'success');
            exitSelectMode();
            loadModels();   // 退出选择模式已不再重拉，删除后需重取新数据（去掉已删卡片）
            window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
        } else {
            showOpError(res, '批量删除失败');
        }
    });
}

async function batchRefreshAction() {
    const count = state.selectedModels.size;
    if (count === 0) { showToast('请先选择模型', 'warning'); return; }

    showToast(`正在重新匹配 ${count} 个模型...`, 'info');
    const btn = document.getElementById('batch-refresh-btn');
    if (btn) { btn.disabled = true; btn.textContent = '匹配中...'; }

    const res = await api.batchRefresh([...state.selectedModels]);
    if (btn) { btn.disabled = false; btn.textContent = '重新匹配'; }

    if (res.success) {
        showToast(`匹配完成: ${res.refreshed}/${res.total} 成功`, 'success');
        loadModels();
    } else {
        showOpError(res, '批量匹配失败');
    }
}

// 小工具：弹一个带输入框的确认框（同 filters.js 里的实现，这里简化内联，不跨文件依赖）
function _promptInput({ title, message, defaultValue = '', okText = '确定', placeholder = '' }) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'delete-confirm-overlay';
        const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]));
        overlay.innerHTML = `
            <div class="delete-confirm">
                <h3>${esc(title)}</h3>
                ${message ? `<p>${esc(message)}</p>` : ''}
                <input type="text" class="preset-prompt-input settings-input"
                       value="${esc(defaultValue)}" placeholder="${esc(placeholder)}">
                <div class="delete-confirm-actions">
                    <button class="btn" data-action="cancel">取消</button>
                    <button class="btn btn-primary" data-action="confirm">${esc(okText)}</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        const input = overlay.querySelector('.preset-prompt-input');
        input.focus();
        input.select();

        const finish = (val) => { overlay.remove(); resolve(val); };
        overlay.querySelector('[data-action="cancel"]').addEventListener('click', () => finish(null));
        overlay.querySelector('[data-action="confirm"]').addEventListener('click', () => {
            const v = input.value.trim();
            finish(v || null);
        });
        overlay.addEventListener('click', e => { if (e.target === overlay) finish(null); });
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter') finish(input.value.trim() || null);
            else if (e.key === 'Escape') finish(null);
        });
    });
}

async function batchTagAction() {
    const count = state.selectedModels.size;
    if (count === 0) { showToast('请先选择模型', 'warning'); return; }
    const input = await _promptInput({
        title: `批量打标签（${count} 个模型）`,
        message: '多个标签用逗号分隔。会追加到已有标签上（不删）。',
        placeholder: '例如：角色, 动漫, 推荐',
        okText: '打标签',
    });
    if (!input) return;
    const tags = input.split(',').map(t => t.trim()).filter(Boolean);
    if (tags.length === 0) return;
    const res = await api.batchTag([...state.selectedModels], tags);
    if (res.success) {
        showToast(`已为 ${res.updated}/${res.total} 个模型添加标签`, 'success');
        window.dispatchEvent(new Event('noctyra-refresh-sidebar')); // tag 数量变了，侧栏过滤要更新
        loadModels();
    } else {
        showToast(res.error || '打标签失败', 'error');
    }
}

async function batchSetBaseModelAction() {
    const count = state.selectedModels.size;
    if (count === 0) { showToast('请先选择模型', 'warning'); return; }
    const input = await _promptInput({
        title: `批量设 base_model（${count} 个模型）`,
        message: '用于 CivitAI 没识别、但你知道基础模型是什么的场景。',
        placeholder: '例如：SDXL / Flux.1 D / Illustrious',
        okText: '设置',
    });
    if (!input) return;
    const res = await api.batchSetBaseModel([...state.selectedModels], input);
    if (res.success) {
        showToast(`已将 ${res.updated}/${res.total} 个模型设为 ${input}`, 'success');
        loadModels();
    } else {
        showToast(res.error || '设置失败', 'error');
    }
}

async function batchMoveAction() {
    const count = state.selectedModels.size;
    if (count === 0) { showToast('请先选择模型', 'warning'); return; }
    const input = await _promptInput({
        title: `批量移动（${count} 个模型）`,
        message: '相对各自扫描根目录的路径。留空 = 移到根目录。',
        placeholder: '例如：Flux/Character 或 Style/Anime',
        okText: '移动',
    });
    if (input === null) return;  // 取消
    const target = input || '';
    const res = await api.batchMove([...state.selectedModels], target);
    if (res.success) {
        const msg = res.failed > 0
            ? `移动完成：${res.moved} 成功，${res.failed} 失败（详见控制台）`
            : `已移动 ${res.moved}/${res.total} 个模型`;
        showToast(msg, res.failed > 0 ? 'warning' : 'success');
        if (res.failed > 0 && res.failed_details) {
            console.warn('[Noctyra] batchMove 失败明细：', res.failed_details);
        }
        loadModels();
        window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
    } else {
        showOpError(res, '移动失败');
    }
}
