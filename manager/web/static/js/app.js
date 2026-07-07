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
 * Noctyra Model Manager - 入口
 */
import { state } from './state.js';
import * as api from './api.js';
import { setSfwEverywhere, onSfwChange } from './sfw-sync.js';
const { connectWebSocket } = api;
import { initCardGrid, loadModels, scrollToModel } from './components/card-grid.js';
import { initModal, openDetailModal } from './components/modal.js';
import { initSidebar, loadFolders } from './components/sidebar.js';
import { initHeader } from './components/header.js';
import { initFilters, loadBaseModels, loadTags, collectCurrentFilters, applyFiltersToState } from './components/filters.js';
import { initSettings } from './components/settings.js';
import { initContextMenu } from './components/context-menu.js';
import { initDownload } from './components/download.js';
import { initImport } from './components/import.js';
import { initUpdateCheck } from './components/update-check.js';
import { initDuplicates } from './components/duplicates.js';
import { initOrganize } from './components/organize.js';
import { initTaskCenter } from './components/task-center.js';
import { enterSelectMode, exitSelectMode } from './components/card-grid.js';
import { showToast } from './components/toast.js';
import { applyThemeMode } from './components/settings-interface.js';
import { initTriggerWords } from './components/trigger-words.js';
import { initFocusTrap } from './utils.js';

const TYPE_LABELS = {
    'checkpoint': 'Checkpoint',
    'lora': 'LoRA',
    'embedding': 'Embedding',
    'controlnet': 'ControlNet',
    'vae': 'VAE',
    'upscale': 'Upscale',
    'clip': 'CLIP',
    'text_encoder': 'Text Encoder',
    'clip_vision': 'CLIP Vision',
    'motion': 'Motion',
    'detection': 'Detection',
    'unet': 'UNet',
    'hypernetwork': 'Hypernetwork',
    'other': '其他',
};

function initTypeTabs() {
    const tabs = document.getElementById('type-tabs');
    if (!tabs) return;

    tabs.addEventListener('click', e => {
        const tab = e.target.closest('.type-tab');
        if (!tab) return;

        tabs.querySelectorAll('.type-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        state.currentModelType = tab.dataset.type || '';
        loadModels();
    });
}

async function updateTypeTabs() {
    const tabs = document.getElementById('type-tabs');
    if (!tabs) return;

    const res = await api.fetchStatus(state.currentSource);
    if (!res.success || !res.type_counts) return;
    state.deletedCount = res.deleted || 0;
    // 只重渲染侧栏（不能派 noctyra-refresh-sidebar：那会触发 updateTypeTabs 形成无限循环）
    window.dispatchEvent(new Event('noctyra-render-sidebar'));

    const counts = res.type_counts;
    // 保留"全部"按钮
    const totalCount = Object.values(counts).reduce((a, b) => a + b, 0);
    tabs.innerHTML = `<button class="type-tab${state.currentModelType === '' ? ' active' : ''}" data-type="">全部 <span class="type-count">${totalCount}</span></button>`;

    // 按数量排序，只显示有模型的类型
    const sorted = Object.entries(counts)
        .filter(([_, cnt]) => cnt > 0)
        .sort((a, b) => b[1] - a[1]);

    for (const [type, count] of sorted) {
        const label = TYPE_LABELS[type] || type;
        const isActive = state.currentModelType === type;
        tabs.innerHTML += `<button class="type-tab${isActive ? ' active' : ''}" data-type="${type}">${label} <span class="type-count">${count}</span></button>`;
    }
}

async function applySettings() {
    try {
        const res = await api.getSettings();
        if (!res.success) return;
        const s = res.settings;

        // 侧边栏
        const sidebar = document.querySelector('.sidebar');
        if (sidebar && s.show_sidebar === false) sidebar.style.display = 'none';
        if (sidebar && s.sidebar_width) {
            document.documentElement.style.setProperty('--sidebar-width', s.sidebar_width + 'px');
        }

        // 显示密度 / 卡片比例
        const grid = document.getElementById('card-grid');
        if (grid) {
            if (s.display_density) grid.dataset.density = s.display_density;
            if (s.card_info_display) grid.dataset.cardInfo = s.card_info_display;
            if (s.card_aspect) grid.style.setProperty('--card-aspect', s.card_aspect.replace('/', ' / '));
        }

        // 主题：dark / light / auto（跟随系统 prefers-color-scheme）
        // 同步写 localStorage，供下次刷新前的内联脚本读取
        const themeMode = s.theme || 'dark';
        applyThemeMode(themeMode);
        try { localStorage.setItem('noctyra_theme', themeMode); } catch (e) {}

        // 存储到 state 方便其他组件读取
        state.settings = s;

        // 独立模式徽章（_runtime_mode 由后端 api_settings_get 注入）
        applyRuntimeModeBadge(s._runtime_mode);
    } catch (e) {
        // 设置加载失败不阻塞启动
    }
}

function applyRuntimeModeBadge(mode) {
    const switcher = document.querySelector('.page-switcher');
    if (!switcher) return;
    const existing = document.getElementById('standalone-mode-badge');
    if (mode !== 'standalone') {
        if (existing) existing.remove();
        return;
    }
    if (existing) return;
    const badge = document.createElement('span');
    badge.id = 'standalone-mode-badge';
    badge.className = 'standalone-badge';
    badge.textContent = '独立模式';
    badge.title = '当前作为独立服务运行（不是 ComfyUI 插件）。启动 ComfyUI 后会自动退出。';
    switcher.insertAdjacentElement('afterend', badge);
}

function initStandaloneShutdownWarning() {
    // 独立模式下会收到 standalone_shutdown_warning WS 事件；集成模式下永远收不到
    api.onWsEvent('standalone_shutdown_warning', (data) => {
        const seconds = parseInt(data?.seconds) || 5;
        const port = data?.comfyui_port ? `（端口 ${data.comfyui_port}）` : '';
        showToast(
            `检测到 ComfyUI 已启动${port}，独立模式将在 ${seconds} 秒后退出`,
            'warning',
            (seconds + 1) * 1000
        );
    });
}

const ICON_MOON = '<svg class="theme-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
const ICON_SUN = '<svg class="theme-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>';

function initThemeToggle() {
    const btn = document.getElementById('btn-theme-toggle');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        // 顶栏按钮：auto → 强制跳到当前反色（让用户能快速切到非系统的状态）
        // dark → light；light → dark；不切回 auto（想回 auto 去设置里选）
        const current = state.settings?.theme || 'dark';
        const effective = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
        const newTheme = (current === 'auto' || effective === 'light') ? 'dark' : 'light';

        applyThemeMode(newTheme);
        try { localStorage.setItem('noctyra_theme', newTheme); } catch (e) {}

        btn.innerHTML = newTheme === 'light' ? ICON_SUN : ICON_MOON;
        state.settings.theme = newTheme;
        await api.saveSettings({ theme: newTheme });
    });
}

function updateThemeButton() {
    const btn = document.getElementById('btn-theme-toggle');
    if (!btn) return;
    const isLight = document.documentElement.dataset.theme === 'light';
    btn.innerHTML = isLight ? ICON_SUN : ICON_MOON;
}

// 顶栏"仅显示 SFW"快捷开关（隐藏 NSFW）。复用 state.settings.show_only_sfw，
// buildQueryParams 会据此带 sfw_only，refresh-list 重新拉取。
function initSfwToggle() {
    const btn = document.getElementById('btn-sfw-toggle');
    if (!btn) return;
    btn.addEventListener('click', async () => {
        if (!state.settings) state.settings = {};
        state.settings.show_only_sfw = !state.settings.show_only_sfw;
        updateSfwButton();
        setSfwEverywhere(state.settings.show_only_sfw);   // 写两键 + 广播到其它页/选择器
        window.dispatchEvent(new Event('noctyra-refresh-list'));
    });
    // 其它 surface（工作流页 / 画布选择器）切换时跟随
    onSfwChange((value) => {
        if (!state.settings) state.settings = {};
        if (!!state.settings.show_only_sfw === value) return;
        state.settings.show_only_sfw = value;
        updateSfwButton();
        window.dispatchEvent(new Event('noctyra-refresh-list'));
    });
}
function updateSfwButton() {
    const btn = document.getElementById('btn-sfw-toggle');
    if (!btn) return;
    const on = !!(state.settings && state.settings.show_only_sfw);
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.title = on ? '仅显示 SFW：开（点击关闭）' : '仅显示 SFW（隐藏 NSFW）';
}

// 跨标签页主题同步 + 系统主题变更监听（auto 模式下跟随系统切换）
function initCrossTabThemeSync() {
    window.addEventListener('storage', (e) => {
        if (e.key !== 'noctyra_theme') return;
        applyThemeMode(e.newValue || 'dark');
        updateThemeButton();
    });

    // prefers-color-scheme 变化 → 如果当前是 auto 模式，重新应用
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => {
        if ((state.settings?.theme || 'dark') === 'auto') {
            applyThemeMode('auto');
            updateThemeButton();
        }
    };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);  // 老 Safari 兜底
}

async function init() {
    connectWebSocket();
    initFocusTrap();
    initHeader();
    initSidebar();
    initCardGrid();
    initModal();
    initFilters();
    initSettings();
    initContextMenu();
    initDownload();
    initImport();
    initUpdateCheck();
    initThemeToggle();
    initCrossTabThemeSync();
    initTypeTabs();
    initDuplicates();
    initTriggerWords();
    initOrganize();
    initTaskCenter();
    initSelectModeButton();
    initSfwToggle();
    initSidebarResize();
    initSidebarCollapse();
    initStandaloneShutdownWarning();

    // 刷新事件
    window.addEventListener('noctyra-refresh-sidebar', () => {
        loadFolders();
        loadTags();
        updateTypeTabs();
    });
    window.addEventListener('noctyra-refresh-tabs', updateTypeTabs);

    // 加载初始数据 + 应用设置
    await Promise.all([
        loadFolders(),
        loadBaseModels(),
        loadTags(),
        updateTypeTabs(),
        applySettings(),
    ]);

    updateThemeButton();
    updateSfwButton();

    // ——— 记住浏览位置：刷新后恢复上次的筛选 + 滚动位置（sessionStorage，仅本标签页）———
    // 恢复放在筛选选项数据(base_model/tag/文件夹)加载完之后，胶囊/侧栏才能正确高亮已选项。
    let _restoreScroll = 0;
    try {
        const saved = JSON.parse(sessionStorage.getItem('noctyra_view') || 'null');
        if (saved) {
            if (saved.f) applyFiltersToState(saved.f);   // 恢复筛选并同步 UI（搜索框/胶囊/排序/侧栏）
            _restoreScroll = Number(saved.s) || 0;
        }
    } catch (_) { /* 隐私模式禁用存储 / 数据损坏，忽略 */ }

    // 存：pagehide 捕获刷新/关闭前的最终状态；滚动节流兜底（万一 pagehide 没触发）
    const _saveView = () => {
        try {
            const sc = document.querySelector('.content-area');
            sessionStorage.setItem('noctyra_view', JSON.stringify({
                f: collectCurrentFilters(), s: sc ? sc.scrollTop : 0,
            }));
        } catch (_) { /* 忽略 */ }
    };
    window.addEventListener('pagehide', _saveView);
    {
        const sc = document.querySelector('.content-area');
        let _svt = null;
        if (sc) sc.addEventListener('scroll', () => {
            if (_svt) return;
            _svt = setTimeout(() => { _svt = null; _saveView(); }, 500);
        }, { passive: true });
    }

    await loadModels();

    // 恢复滚动位置：网格已布局，设 scrollTop 触发虚拟滚动重渲染到原处（双 rAF 等布局稳定）
    if (_restoreScroll > 0) {
        const sc = document.querySelector('.content-area');
        if (sc) requestAnimationFrame(() => requestAnimationFrame(() => { sc.scrollTop = _restoreScroll; }));
    }

    // 深链：?model=<sha256 或 file_path> 自动打开该模型详情弹窗
    // 让工作流图库的资源名链接能"跨页跳转"进来
    try {
        const params = new URLSearchParams(location.search);
        const target = (params.get('model') || '').trim();
        if (target) {
            // 延一帧（布局稳定 + modal 容器就绪）：先把列表滚动定位到该卡片，再开详情，
            // 这样关闭详情后就停在该卡片附近，不用重新翻找
            setTimeout(() => {
                scrollToModel(target);
                openDetailModal(target);
            }, 0);
        }
    } catch (e) {
        console.debug('[Noctyra] deep link open failed:', e);
    }
}

function initSelectModeButton() {
    const btn = document.getElementById('btn-select-mode');
    if (!btn) return;

    btn.addEventListener('click', () => {
        if (state.selectMode) {
            exitSelectMode();
            btn.classList.remove('active');
        } else {
            enterSelectMode();
            btn.classList.add('active');
        }
    });

    // 按 Escape 退出选择模式
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && state.selectMode) {
            exitSelectMode();
            btn.classList.remove('active');
        }
    });
}

function initSidebarResize() {
    const handle = document.getElementById('sidebar-resize-handle');
    const sidebar = document.querySelector('.sidebar');
    if (!handle || !sidebar) return;

    const MIN = 180, MAX = 480;
    let saveTimer = null;

    const startResize = (e) => {
        e.preventDefault();
        const startX = e.clientX;
        const startW = sidebar.getBoundingClientRect().width;
        handle.classList.add('resizing');
        document.body.classList.add('sidebar-resizing');

        const onMove = (ev) => {
            const w = Math.min(MAX, Math.max(MIN, startW + (ev.clientX - startX)));
            document.documentElement.style.setProperty('--sidebar-width', w + 'px');
        };
        const onUp = () => {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            handle.classList.remove('resizing');
            document.body.classList.remove('sidebar-resizing');

            const finalW = parseInt(sidebar.getBoundingClientRect().width, 10);
            state.settings.sidebar_width = finalW;
            clearTimeout(saveTimer);
            saveTimer = setTimeout(() => api.saveSettings({ sidebar_width: finalW }), 300);
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    };
    handle.addEventListener('mousedown', startResize);

    handle.addEventListener('dblclick', () => {
        document.documentElement.style.setProperty('--sidebar-width', '230px');
        state.settings.sidebar_width = 230;
        api.saveSettings({ sidebar_width: 230 });
    });
}

function initSidebarCollapse() {
    const sidebar = document.getElementById('sidebar');
    const pinBtn = document.getElementById('btn-pin-sidebar');
    if (!sidebar || !pinBtn) return;

    const isPinned = () => {
        const s = state.settings || {};
        if (s.sidebar_pinned !== undefined) return s.sidebar_pinned !== false;
        if (s.sidebar_collapsed === true) return false;
        return true;
    };

    const apply = (pinned) => {
        document.body.classList.toggle('sidebar-unpinned', !pinned);
        pinBtn.classList.toggle('active', pinned);
    };

    apply(isPinned());

    pinBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const next = !isPinned();
        state.settings.sidebar_pinned = next;
        apply(next);
        api.saveSettings({ sidebar_pinned: next });
    });
}

document.addEventListener('DOMContentLoaded', init);

