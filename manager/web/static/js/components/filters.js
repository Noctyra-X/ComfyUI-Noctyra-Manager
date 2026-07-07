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
 * 过滤工具栏
 */
import { state } from '../state.js';
import * as api from '../api.js';
import { loadModels } from './card-grid.js';
import { showToast } from './toast.js';
import { showConfirm, showPrompt } from './dialog.js';
// 通过事件通知 app.js 更新标签栏，避免循环依赖
const refreshTypeTabs = () => window.dispatchEvent(new Event('noctyra-refresh-tabs'));

// 本地缓存：预设列表，避免每次切换都重新请求
let _filterPresets = [];
// 扫描/匹配进行中节流自动刷新的时间戳
let _liveRefreshAt = 0;

// ========== 筛选胶囊系统 ==========
// 每个分类映射到一个 state 字段；source/license/preview_status 选项固定，
// base_model/tag 选项来自后端（loadBaseModels / loadTags）。单选：再点一次取消。
const FILTER_CATS = {
    base_model:     { key: 'currentBaseModel',     title: '基础模型' },
    lora_subtype:   { key: 'currentLoraSubtype',   title: 'LoRA 细分' },
    tag:            { key: 'currentTag',           title: '标签' },
    source:         { key: 'currentSource',        title: '来源' },
    license:        { key: 'currentLicense',       title: '许可' },
    preview_status: { key: 'currentPreviewStatus', title: '预览图' },
};
const STATIC_FILTER_OPTIONS = {
    source: [['favorite', '收藏'], ['civitai', 'CivitAI'], ['huggingface', 'HuggingFace'],
             ['unmatched', '未匹配'], ['updatable', '可更新'], ['corrupt', '损坏'], ['deleted', '已删除(留记录)']],
    license: [['commercial', '允许商用'], ['personal', '仅个人使用']],
    preview_status: [['missing', '有缺失'], ['failed', '缓存失败'], ['complete', '齐全']],
    // LoRA 家族细分（LoCon = LyCORIS）；归类仍统一为 LoRA，仅用于筛选
    lora_subtype: [['lora', 'LoRA'], ['lycoris', 'LyCORIS'], ['dora', 'DoRA']],
};

export function initFilters() {
    // 排序
    const sortSelect = document.getElementById('sort-select');
    if (sortSelect) {
        sortSelect.addEventListener('change', () => {
            state.currentSort = sortSelect.value;
            state.currentSortDir = '';
            updateSortDirButton();
            loadModels();
        });
    }

    // 排序方向
    const sortDirBtn = document.getElementById('btn-sort-dir');
    if (sortDirBtn) {
        sortDirBtn.addEventListener('click', () => {
            if (state.currentSortDir === 'asc') {
                state.currentSortDir = 'desc';
            } else if (state.currentSortDir === 'desc') {
                state.currentSortDir = '';
            } else {
                state.currentSortDir = 'asc';
            }
            updateSortDirButton();
            loadModels();
        });
    }

    // 筛选胶囊（基础模型 / 标签 / 来源 / 许可 / 预览图）—— 取代原先一排 select
    initFilterPanel();

    // 筛选预设
    initFilterPresets();

    // 扫描按钮 — 异步启动，通过 WebSocket 接收进度
    const scanBtn = document.getElementById('btn-scan');
    if (scanBtn) {
        scanBtn.title = '扫描模型目录（增量）。Shift+点击 = 全量重扫，重新判定类型（修正误分类，保留收藏/标签）';
        scanBtn.addEventListener('click', async (e) => {
            const force = e.shiftKey;
            scanBtn.disabled = true;
            scanBtn.textContent = force ? '重扫中...' : '扫描中...';
            showToast(force ? '正在全量重扫（重新判定类型）...' : '正在扫描模型目录...', 'info');
            try {
                const res = await api.triggerScan(force);
                if (!res.success) {
                    showToast(res.error === 'busy' ? '有操作正在进行中，请稍后再试' : '扫描失败: ' + (res.error || ''), res.error === 'busy' ? 'warning' : 'error');
                    scanBtn.disabled = false;
                    scanBtn.textContent = '扫描';
                }
                // 成功则等待 WebSocket 的 scan_progress complete 事件
            } catch (e) {
                showToast('扫描出错: ' + e.message, 'error');
                scanBtn.disabled = false;
                scanBtn.textContent = '扫描';
            }
        });
    }

    // 匹配按钮
    const matchBtn = document.getElementById('btn-match');
    if (matchBtn) {
        matchBtn.addEventListener('click', async (e) => {
            const rematch = e.shiftKey;
            matchBtn.disabled = true;
            matchBtn.textContent = rematch ? '全部匹配中...' : '匹配中...';
            showToast(rematch ? '正在重新匹配所有模型...' : '正在匹配未匹配的模型...', 'info');
            try {
                const res = await api.triggerMatch(rematch);
                if (!res.success) {
                    showToast(res.error === 'busy' ? '有操作正在进行中，请稍后再试' : '匹配失败: ' + (res.error || ''), res.error === 'busy' ? 'warning' : 'error');
                    matchBtn.disabled = false;
                    matchBtn.textContent = '匹配';
                }
                // 成功则等待 WebSocket 的 match_progress complete 事件
            } catch (e) {
                showToast('匹配出错: ' + e.message, 'error');
                matchBtn.disabled = false;
                matchBtn.textContent = '匹配';
            }
        });
    }

    // WebSocket 进度监听
    api.onWsEvent('scan_progress', (msg) => {
        const scanBtn = document.getElementById('btn-scan');
        if (msg.stage === 'complete') {
            if (scanBtn) { scanBtn.disabled = false; scanBtn.textContent = '扫描'; }
            showToast(`扫描完成，共 ${msg.total} 个模型`, 'success');
            loadModels();
            loadBaseModels();
            refreshTypeTabs();
            window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
        } else if (msg.stage === 'error') {
            if (scanBtn) { scanBtn.disabled = false; scanBtn.textContent = '扫描'; }
            showToast('扫描出错: ' + (msg.error || ''), 'error');
        } else if (scanBtn) {
            // 实时进度
            if (msg.total > 0) {
                scanBtn.textContent = `扫描 ${msg.progress}%`;
            } else {
                scanBtn.textContent = `扫描中...`;
            }
            // 扫描中：节流自动刷新，让新入库的模型卡片陆续出现（仅第 1 页时，避免打断滚动）
            const now = Date.now();
            if (state.page <= 1 && !state.isLoading && now - _liveRefreshAt > 5000) {
                _liveRefreshAt = now;
                loadModels(true);   // 后台扫描/匹配实时刷新：保留滚动位置
            }
        }
    });

    api.onWsEvent('match_progress', (msg) => {
        const matchBtn = document.getElementById('btn-match');
        if (msg.stage === 'complete') {
            if (matchBtn) { matchBtn.disabled = false; matchBtn.textContent = '匹配'; }
            const s = msg.stats || {};
            showToast(`匹配完成: CivitAI ${s.civitai_matched || 0}, HF ${s.hf_matched || 0}, 未匹配 ${s.unmatched || 0}`, 'success', 5000);
            loadModels();
            loadTags();
            refreshTypeTabs();
        } else if (msg.stage === 'error') {
            if (matchBtn) { matchBtn.disabled = false; matchBtn.textContent = '匹配'; }
            showToast('匹配出错: ' + (msg.error || ''), 'error');
        } else if (matchBtn) {
            if (msg.total > 0) {
                matchBtn.textContent = `匹配 ${msg.current}/${msg.total}`;
            }
            // 匹配进行中：节流自动刷新，让已匹配的卡片陆续更新，不必等整批跑完。
            // 仅在第 1 页（未向下滚动加载更多）时刷新，避免把正在看的多页内容收回顶部。
            const now = Date.now();
            if (state.page <= 1 && !state.isLoading && now - _liveRefreshAt > 5000) {
                _liveRefreshAt = now;
                loadModels(true);   // 后台扫描/匹配实时刷新：保留滚动位置
            }
        }
    });

    // 刷新后恢复进度：若后端正扫描/匹配，把按钮置回"进行中"并轮询到完成
    restoreOpState();

    // WS 断线重连后同样补齐扫描/匹配进度（断线期间的进度事件已丢）
    window.addEventListener('noctyra-ws-reconnected', restoreOpState);
}

// ===== 刷新后恢复扫描/匹配进度（进度在后端，前端内存刷新即丢，这里从 /status 拉回） =====
function _opBtnInProgress(progress, btnId, label) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.disabled = true;
    const pct = (progress && progress.total > 0)
        ? Math.round(progress.current / progress.total * 100) : null;
    btn.textContent = pct != null ? `${label} ${pct}%` : `${label}中...`;
}

let _opPollTimer = null;

async function restoreOpState() {
    let st;
    try { st = await api.fetchStatus(); } catch (_) { return; }
    if (st.is_scanning) _opBtnInProgress(st.progress, 'btn-scan', '扫描');
    if (st.is_matching) _opBtnInProgress(st.progress, 'btn-match', '匹配');
    if (st.is_scanning || st.is_matching) _startOpPoller();
}

function _startOpPoller() {
    if (_opPollTimer) return;
    _opPollTimer = setInterval(async () => {
        let st;
        try { st = await api.fetchStatus(); } catch (_) { return; }
        if (st.is_scanning) _opBtnInProgress(st.progress, 'btn-scan', '扫描');
        if (st.is_matching) _opBtnInProgress(st.progress, 'btn-match', '匹配');
        if (!st.is_scanning && !st.is_matching) {
            // 操作已结束（可能在刷新间隙完成、WS complete 漏接）→ 复位按钮 + 刷新一次
            clearInterval(_opPollTimer); _opPollTimer = null;
            const scanBtn = document.getElementById('btn-scan');
            const matchBtn = document.getElementById('btn-match');
            if (scanBtn) { scanBtn.disabled = false; scanBtn.textContent = '扫描'; }
            if (matchBtn) { matchBtn.disabled = false; matchBtn.textContent = '匹配'; }
            loadModels();
            loadBaseModels();
            refreshTypeTabs();
            window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
        }
    }, 2500);
}

function updateSortDirButton() {
    const btn = document.getElementById('btn-sort-dir');
    if (!btn) return;
    if (state.currentSortDir === 'asc') {
        btn.textContent = '↑';
        btn.title = '升序（点击切换降序）';
        btn.classList.add('active');
    } else if (state.currentSortDir === 'desc') {
        btn.textContent = '↓';
        btn.title = '降序（点击切换默认）';
        btn.classList.add('active');
    } else {
        btn.textContent = '↕';
        btn.title = '默认排序（点击切换升序）';
        btn.classList.remove('active');
    }
}

// ========== 筛选胶囊：渲染 + 交互 ==========
function _filterOptions(cat) {
    if (cat === 'base_model') return (state.baseModels || []).map(bm => [bm, bm]);
    if (cat === 'tag') return (state.tags || []).map(t => [t.tag, `${t.tag} (${t.count})`]);
    return STATIC_FILTER_OPTIONS[cat] || [];
}

function renderFilterChips(cat) {
    const wrap = document.getElementById('chips-' + cat);
    if (!wrap) return;
    const cur = state[FILTER_CATS[cat].key] || '';
    const opts = _filterOptions(cat);
    if (!opts.length) { wrap.innerHTML = '<span class="filter-chips-empty">暂无</span>'; return; }
    wrap.innerHTML = opts.map(([v, l]) =>
        `<button class="filter-chip${v === cur ? ' active' : ''}" data-cat="${cat}" data-val="${escapeHtml(v)}">${escapeHtml(l)}</button>`
    ).join('');
}

function renderAllFilterChips() {
    for (const cat of Object.keys(FILTER_CATS)) renderFilterChips(cat);
}

function _filterLabel(cat, v) {
    if (cat === 'base_model' || cat === 'tag') return v;
    const f = (STATIC_FILTER_OPTIONS[cat] || []).find(o => o[0] === v);
    return f ? f[1] : v;
}

// 顶部"已选条" + 筛选按钮的数字角标 + has-active 高亮
function syncActiveFilters() {
    const active = [];
    for (const [cat, def] of Object.entries(FILTER_CATS)) {
        if (state[def.key]) active.push([cat, state[def.key]]);
    }
    const countEl = document.getElementById('filter-count');
    if (countEl) { countEl.textContent = String(active.length); countEl.hidden = active.length === 0; }
    document.getElementById('btn-filter-toggle')?.classList.toggle('has-active', active.length > 0);
    const strip = document.getElementById('active-filters');
    if (strip) {
        strip.innerHTML = active.map(([cat, v]) =>
            `<span class="active-filter-chip">${escapeHtml(_filterLabel(cat, v))}<button class="active-filter-x" data-cat="${cat}" aria-label="移除" title="移除">×</button></span>`
        ).join('');
    }
}

// 设置某分类筛选值（''=清除）→ 刷新胶囊 + 已选条 +（可选）重载列表
function setFilterValue(cat, val, reload = true) {
    const def = FILTER_CATS[cat];
    if (!def) return;
    state[def.key] = val || '';
    renderFilterChips(cat);
    syncActiveFilters();
    if (reload) loadModels();
    if (cat === 'source') window.dispatchEvent(new Event('noctyra-refresh-tabs'));
}

function clearAllFilters() {
    let changed = false;
    for (const def of Object.values(FILTER_CATS)) {
        if (state[def.key]) { state[def.key] = ''; changed = true; }
    }
    if (!changed) return;
    renderAllFilterChips();
    syncActiveFilters();
    loadModels();
    window.dispatchEvent(new Event('noctyra-refresh-tabs'));
}

function initFilterPanel() {
    renderAllFilterChips();
    syncActiveFilters();

    const toggle = document.getElementById('btn-filter-toggle');
    const panel = document.getElementById('filter-panel');
    if (toggle && panel) {
        toggle.addEventListener('click', (e) => {
            e.stopPropagation();
            const open = panel.hidden;
            panel.hidden = !open;
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
        panel.addEventListener('click', (e) => {
            e.stopPropagation();   // 面板内点击不冒泡到 document（否则会触发"点外部关闭"）
            const chip = e.target.closest('.filter-chip');
            if (!chip) return;
            const cat = chip.dataset.cat;
            const val = chip.dataset.val;
            // 单选：点已选的再点一次 → 取消
            setFilterValue(cat, state[FILTER_CATS[cat].key] === val ? '' : val);
        });
        document.addEventListener('click', (e) => {
            if (!panel.hidden && !panel.contains(e.target) && !toggle.contains(e.target)) {
                panel.hidden = true;
                toggle.setAttribute('aria-expanded', 'false');
            }
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !panel.hidden) {
                panel.hidden = true;
                toggle.setAttribute('aria-expanded', 'false');
            }
        });
    }

    const strip = document.getElementById('active-filters');
    if (strip) {
        strip.addEventListener('click', (e) => {
            const x = e.target.closest('.active-filter-x');
            if (x) setFilterValue(x.dataset.cat, '');
        });
    }

    const clearBtn = document.getElementById('btn-clear-filters');
    if (clearBtn) clearBtn.addEventListener('click', clearAllFilters);

    // 外部入口（侧栏点"来源"、详情弹窗点标签）统一走胶囊系统
    window.addEventListener('noctyra-set-filter', (e) => {
        const { cat, value, reload } = e.detail || {};
        if (!FILTER_CATS[cat]) return;
        state[FILTER_CATS[cat].key] = value || '';
        renderFilterChips(cat);
        syncActiveFilters();
        if (reload) loadModels();
    });
}

export async function loadTags() {
    const res = await api.fetchTags(100);
    if (!res.success) return;
    state.tags = res.tags;
    renderFilterChips('tag');
}

export async function loadBaseModels() {
    const res = await api.fetchBaseModels();
    if (!res.success) return;
    state.baseModels = res.base_models;
    renderFilterChips('base_model');
}

// ========== 筛选预设 ==========

// 当前 state 中可保存到预设的字段（与后端 _PRESET_FILTER_KEYS 对齐）
export function collectCurrentFilters() {
    const out = {};
    if (state.currentSearch) out.search = state.currentSearch;
    if (state.currentFolder) out.folder = state.currentFolder;
    if (state.currentBaseModel) out.base_model = state.currentBaseModel;
    if (state.currentSource) out.source = state.currentSource;
    if (state.currentModelType) out.model_type = state.currentModelType;
    if (state.currentLoraSubtype) out.lora_subtype = state.currentLoraSubtype;
    if (state.currentTag) out.tag = state.currentTag;
    if (state.currentLicense) out.license = state.currentLicense;
    if (state.currentPreviewStatus) out.preview_status = state.currentPreviewStatus;
    if (state.currentSort && state.currentSort !== 'file_name') out.sort_by = state.currentSort;
    if (state.currentSortDir) out.sort_dir = state.currentSortDir;
    return out;
}

// 把预设里的 filters 套用到 state 并刷新各 select 的显示
export function applyFiltersToState(filters) {
    const f = filters || {};
    state.currentSearch = f.search || '';
    state.currentFolder = f.folder || '';
    state.currentBaseModel = f.base_model || '';
    state.currentSource = f.source || '';
    state.currentModelType = f.model_type || '';
    state.currentLoraSubtype = f.lora_subtype || '';
    state.currentTag = f.tag || '';
    state.currentLicense = f.license || '';
    state.currentPreviewStatus = f.preview_status || '';
    state.currentSort = f.sort_by || 'file_name';
    state.currentSortDir = f.sort_dir || '';

    // 同步 UI：排序仍是 select；其余筛选改用胶囊 + 已选条
    const sortSel = document.getElementById('sort-select');
    if (sortSel) sortSel.value = state.currentSort || 'file_name';
    updateSortDirButton();
    renderAllFilterChips();
    syncActiveFilters();

    // 搜索框（在 header.js 里，id 需要保持稳定）
    const searchInput = document.getElementById('search-input');
    if (searchInput) searchInput.value = state.currentSearch;

    // 类型 tab + 侧栏
    window.dispatchEvent(new Event('noctyra-refresh-tabs'));
    window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
}

function renderPresetOptions(selectedName = '') {
    const sel = document.getElementById('filter-preset-select');
    if (!sel) return;
    const options = ['<option value="">筛选预设…</option>'];
    for (const p of _filterPresets) {
        const isSel = p.name === selectedName ? ' selected' : '';
        const text = p.name.length > 30 ? p.name.slice(0, 29) + '…' : p.name;
        const safe = escapeHtml(p.name);
        const txt = escapeHtml(text);
        options.push(`<option value="${safe}"${isSel}>${txt}</option>`);
    }
    sel.innerHTML = options.join('');
    updateDeleteButtonState();
}

function updateDeleteButtonState() {
    const sel = document.getElementById('filter-preset-select');
    const btn = document.getElementById('btn-delete-preset');
    if (!sel || !btn) return;
    btn.disabled = !sel.value;
}

function escapeHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// 确认 / 文本输入弹窗已提取到 ./dialog.js（showConfirm / showPrompt），全局复用

async function initFilterPresets() {
    const sel = document.getElementById('filter-preset-select');
    const saveBtn = document.getElementById('btn-save-preset');
    const delBtn = document.getElementById('btn-delete-preset');
    if (!sel || !saveBtn || !delBtn) return;

    // 首次加载预设列表
    try {
        const res = await api.listFilterPresets();
        if (res && res.success) {
            _filterPresets = res.presets || [];
            renderPresetOptions();
        }
    } catch (e) {
        // 安静失败；预设是锦上添花
    }

    sel.addEventListener('change', () => {
        const name = sel.value;
        updateDeleteButtonState();
        if (!name) return;
        const preset = _filterPresets.find(p => p.name === name);
        if (!preset) return;
        applyFiltersToState(preset.filters || {});
        loadModels();
    });

    saveBtn.addEventListener('click', async () => {
        const defaultName = sel.value || '';
        const name = await showPrompt({
            title: '保存筛选预设',
            message: '输入预设名称（同名会覆盖）。',
            defaultValue: defaultName,
            placeholder: '例如：仅 Flux LoRA',
            okText: '保存',
        });
        if (!name) return;
        const filters = collectCurrentFilters();
        const res = await api.saveFilterPreset(name, filters);
        if (!res || !res.success) {
            showToast('保存失败: ' + (res && res.error || ''), 'error');
            return;
        }
        // 用后端返回的权威数据更新本地列表
        const saved = res.preset;
        const idx = _filterPresets.findIndex(p => p.name === saved.name);
        if (idx >= 0) {
            _filterPresets[idx] = saved;
        } else {
            _filterPresets.push(saved);
        }
        renderPresetOptions(saved.name);
        showToast(`已保存预设"${saved.name}"`, 'success');
    });

    delBtn.addEventListener('click', async () => {
        const name = sel.value;
        if (!name) return;
        const ok = await showConfirm({
            title: '删除筛选预设',
            message: `确认删除预设"${name}"？此操作不可撤销。`,
            okText: '删除',
            danger: true,
        });
        if (!ok) return;
        const res = await api.deleteFilterPreset(name);
        if (!res || !res.success) {
            showToast('删除失败', 'error');
            return;
        }
        _filterPresets = _filterPresets.filter(p => p.name !== name);
        renderPresetOptions();
        showToast('预设已删除', 'success');
    });
}
