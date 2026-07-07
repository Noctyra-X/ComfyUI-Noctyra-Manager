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
 * Noctyra Workflow Manager — 入口
 *
 * 本文件保留：DOMContentLoaded 事件绑定、handleFetch / handleSave、
 *             loadGallery、本地导入、批量导入、WebSocket 客户端、
 *             runtime mode 徽章、独立模式退出提示。
 *
 * 已拆出的模块（见同目录）：
 *   - state.js      ：共享 wfState / API_BASE
 *   - api.js        ：所有 HTTP 客户端
 *   - renderers.js  ：HTML 渲染器（generation params / resources）
 *   - detail.js     ：openDetail / zoomImage / bindDownloadButtons / openSameRecipeModal
 */

import { escapeAttr, initFocusTrap } from '../utils.js';
import { API_BASE, wfState } from './state.js';
import {
    apiFetch, apiSave, apiGalleryList, apiGalleryDelete, apiImportLocal,
} from './api.js';
import { openDetail } from './detail.js';
import { initFolderSidebar } from './folders.js';
import { initSettings } from '../components/settings.js';
import { showConfirm } from '../components/dialog.js';
import { initThemeToggle } from '../theme.js';
import { setSfwEverywhere, onSfwChange } from '../sfw-sync.js';


// ========== Runtime info & 独立模式徽章 ==========

async function loadRuntimeInfo() {
    try {
        const r = await fetch('/api/noctyra/settings');
        const j = await r.json();
        const s = (j && j.settings) || {};
        const host = s.civitai_source_host || '';
        if (host === 'civitai.red' || host === 'civitai.green') wfState.civitaiHost = host;
        // 图库 NSFW 用独立键，未设置时回退到全局（?? 仅在 undefined/null 时回退）
        wfState.blurNsfw = (s.gallery_blur_nsfw ?? s.blur_nsfw) !== false;
        wfState.nsfwBlurThreshold = parseInt(s.gallery_nsfw_blur_threshold ?? s.nsfw_blur_threshold) || 4;
        gallerySfwOnly = !!(s.gallery_show_only_sfw ?? s.show_only_sfw);
        updateSfwToggleBtn();
        applyRuntimeModeBadge(s._runtime_mode);
        applyGallerySettings(s);
    } catch (_) { /* 保持默认 */ }
    loadGalleryTags();  // 下拉数据
    loadGalleryFormats();
    loadGallery();
}

let galleryTags = [];
async function loadGalleryTags() {
    try {
        const { apiGalleryTags } = await import('./api.js');
        const res = await apiGalleryTags();
        if (!res.success) return;
        galleryTags = res.tags || [];
        renderWfChips('tag');
    } catch (e) {
        console.warn('loadGalleryTags failed:', e);
    }
}

let galleryFormats = [];
async function loadGalleryFormats() {
    try {
        const { apiGalleryFormats } = await import('./api.js');
        const res = await apiGalleryFormats();
        if (!res.success) return;
        galleryFormats = res.formats || [];
        renderWfChips('format');
    } catch (e) {
        console.warn('loadGalleryFormats failed:', e);
    }
}

// ========== 图库筛选胶囊（标签 / 资源 / 收藏）—— 与模型页一致 ==========
const WF_RESOURCE_OPTIONS = [['missing', '有缺失'], ['complete', '齐全']];

function wfFilterOptions(cat) {
    if (cat === 'tag') return galleryTags.map(t => [t.name, `${t.name} (${t.count})`]);
    if (cat === 'format') return galleryFormats.map(f => [f.name, `${f.name.toUpperCase()} (${f.count})`]);
    if (cat === 'resources') return WF_RESOURCE_OPTIONS;
    if (cat === 'fav') return [['1', '只看收藏']];
    if (cat === 'workflow') return [['1', '可发送到画布']];
    return [];
}
function wfFilterCurrent(cat) {
    if (cat === 'tag') return galleryTag;
    if (cat === 'format') return galleryFormat;
    if (cat === 'resources') return galleryResourcesFilter;
    if (cat === 'fav') return galleryFavOnly ? '1' : '';
    if (cat === 'workflow') return galleryWorkflowOnly ? '1' : '';
    return '';
}
function renderWfChips(cat) {
    const wrap = document.getElementById('wf-chips-' + cat);
    if (!wrap) return;
    const cur = wfFilterCurrent(cat);
    const opts = wfFilterOptions(cat);
    if (!opts.length) { wrap.innerHTML = '<span class="filter-chips-empty">暂无</span>'; return; }
    wrap.innerHTML = opts.map(([v, l]) =>
        `<button class="filter-chip${v === cur ? ' active' : ''}" data-cat="${cat}" data-val="${escapeAttr(v)}">${escapeAttr(l)}</button>`
    ).join('');
}
function renderAllWfChips() { ['tag', 'format', 'resources', 'fav', 'workflow'].forEach(renderWfChips); }
function wfFilterLabel(cat, v) {
    if (cat === 'tag') return v;
    if (cat === 'format') return v.toUpperCase();
    if (cat === 'fav') return '只看收藏';
    if (cat === 'workflow') return '可发送到画布';
    const f = WF_RESOURCE_OPTIONS.find(o => o[0] === v);
    return f ? f[1] : v;
}
function syncWfActiveFilters() {
    const active = [];
    if (galleryTag) active.push(['tag', galleryTag]);
    if (galleryFormat) active.push(['format', galleryFormat]);
    if (galleryResourcesFilter) active.push(['resources', galleryResourcesFilter]);
    if (galleryFavOnly) active.push(['fav', '1']);
    if (galleryWorkflowOnly) active.push(['workflow', '1']);
    const countEl = document.getElementById('wf-filter-count');
    if (countEl) { countEl.textContent = String(active.length); countEl.hidden = active.length === 0; }
    document.getElementById('wf-filter-toggle')?.classList.toggle('has-active', active.length > 0);
    const strip = document.getElementById('wf-active-filters');
    if (strip) {
        strip.innerHTML = active.map(([cat, v]) =>
            `<span class="active-filter-chip">${escapeAttr(wfFilterLabel(cat, v))}<button class="active-filter-x" data-cat="${cat}" aria-label="移除" title="移除">×</button></span>`
        ).join('');
    }
}
function setWfFilter(cat, val) {
    if (cat === 'tag') galleryTag = val || '';
    else if (cat === 'format') galleryFormat = val || '';
    else if (cat === 'resources') galleryResourcesFilter = val || '';
    else if (cat === 'fav') galleryFavOnly = !!val;
    else if (cat === 'workflow') galleryWorkflowOnly = !!val;
    renderWfChips(cat);
    syncWfActiveFilters();
    loadGallery();   // loadGallery 内部已重置到第 1 页
}
function clearWfFilters() {
    if (!galleryTag && !galleryFormat && !galleryResourcesFilter && !galleryFavOnly && !galleryWorkflowOnly) return;
    galleryTag = '';
    galleryFormat = '';
    galleryResourcesFilter = '';
    galleryFavOnly = false;
    galleryWorkflowOnly = false;
    renderAllWfChips();
    syncWfActiveFilters();
    loadGallery();
}
function initWfFilterPanel() {
    renderAllWfChips();
    syncWfActiveFilters();
    const toggle = document.getElementById('wf-filter-toggle');
    const panel = document.getElementById('wf-filter-panel');
    if (toggle && panel) {
        toggle.addEventListener('click', (e) => {
            e.stopPropagation();
            const open = panel.hidden;
            panel.hidden = !open;
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
        panel.addEventListener('click', (e) => {
            e.stopPropagation();   // 面板内点击不冒泡到 document（否则触发外部关闭）
            const chip = e.target.closest('.filter-chip');
            if (!chip) return;
            const cat = chip.dataset.cat;
            const val = chip.dataset.val;
            setWfFilter(cat, wfFilterCurrent(cat) === val ? '' : val);   // 再点一次取消
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
    const strip = document.getElementById('wf-active-filters');
    if (strip) {
        strip.addEventListener('click', (e) => {
            const x = e.target.closest('.active-filter-x');
            if (x) setWfFilter(x.dataset.cat, '');
        });
    }
    const clearBtn = document.getElementById('wf-clear-filters');
    if (clearBtn) clearBtn.addEventListener('click', clearWfFilters);
}

// 顶栏"仅显示 SFW"快捷开关（隐藏 NSFW）。过滤在后端按 gallery_show_only_sfw 配置生效。
function updateSfwToggleBtn() {
    const btn = document.getElementById('wf-sfw-toggle');
    if (!btn) return;
    btn.classList.toggle('active', gallerySfwOnly);
    btn.setAttribute('aria-pressed', gallerySfwOnly ? 'true' : 'false');
    btn.title = gallerySfwOnly ? '仅显示 SFW：开（点击关闭）' : '仅显示 SFW（隐藏 NSFW）';
}
async function toggleGallerySfw() {
    gallerySfwOnly = !gallerySfwOnly;
    updateSfwToggleBtn();
    setSfwEverywhere(gallerySfwOnly);   // 写两键 + 广播到模型页/选择器
    loadGallery();   // 后端按 config 过滤，重载即生效
}
// 其它 surface（模型页 / 画布选择器）切换 SFW 时跟随
function initSfwSync() {
    onSfwChange((value) => {
        if (gallerySfwOnly === value) return;
        gallerySfwOnly = value;
        updateSfwToggleBtn();
        loadGallery();
    });
}

function applyGallerySettings(s) {
    const pageSize = parseInt(s.gallery_page_size);
    if (pageSize > 0) galleryPageSize = pageSize;

    const grid = document.getElementById('gallery-grid');
    if (grid) {
        const thumb = s.gallery_thumb_size || 'medium';
        grid.dataset.thumb = ['small', 'medium', 'large'].includes(thumb) ? thumb : 'medium';
        grid.dataset.showFilename = s.gallery_show_filename ? 'true' : 'false';
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
    onBatchWs('standalone_shutdown_warning', (data) => {
        const seconds = parseInt(data?.seconds) || 5;
        const port = data?.comfyui_port ? `（端口 ${data.comfyui_port}）` : '';
        showToast(
            `检测到 ComfyUI 已启动${port}，独立模式将在 ${seconds} 秒后退出`,
            'warning'
        );
    });
}

// ========== 本地 showToast（工作流页独立实现，没引入主管理器的 toast 组件） ==========

function showToast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    container.appendChild(el);
    // 保底清理，避免 CSS 过渡损坏时 DOM 累积
    setTimeout(() => el.remove(), 3400);
}
// 暴露给 detail.js 通过 window._wfShowToast 调用，避免循环依赖
window._wfShowToast = showToast;


// ========== Fetch Section ==========

async function handleFetch() {
    const input = document.getElementById('civitai-url-input');
    const fetchBtn = document.getElementById('fetch-btn');
    const statusEl = document.getElementById('fetch-status');
    const url = input.value.trim();
    if (!url) { showToast('请粘贴 CivitAI 图片链接', 'error'); return; }

    fetchBtn.disabled = true;
    fetchBtn.textContent = '获取中...';
    if (statusEl) statusEl.textContent = '正在拉取图片信息...';

    try {
        // Step 1: 拉取 CivitAI 图片/视频元数据
        const res = await apiFetch(url);
        if (!res.success) {
            showToast(res.error || '获取失败', 'error');
            if (statusEl) statusEl.textContent = '';
            return;
        }

        const image = res.image;
        if (statusEl) statusEl.textContent = `正在下载并入库（${image.width}×${image.height}, id=${image.id}）...`;

        // Step 2: 直接调 save，跳过手动确认
        const saveRes = await apiSave(image.url, image);
        if (saveRes.success) {
            if (saveRes.already_exists) {
                showToast('已在图库中', 'info');
                if (statusEl) statusEl.textContent = '已在图库中';
            } else {
                showToast('保存成功', 'success');
                if (statusEl) statusEl.textContent = `已保存 · id=${image.id}`;
            }
            input.value = '';
            loadGallery();
        } else {
            showToast(saveRes.error || '保存失败', 'error');
            if (statusEl) statusEl.textContent = '保存失败：' + (saveRes.error || '');
        }
    } catch (e) {
        showToast('请求异常: ' + e.message, 'error');
        if (statusEl) statusEl.textContent = '异常：' + e.message;
    } finally {
        fetchBtn.disabled = false;
        fetchBtn.textContent = '获取并保存';
        // 3 秒后清空状态文字，避免长期占屏
        setTimeout(() => { if (statusEl && !fetchBtn.disabled) statusEl.textContent = ''; }, 3000);
    }
}


// ========== Gallery（瀑布流列分配 + 无限滚动 scroll-sentinel） ==========

let galleryPage = 1;
let gallerySearch = '';
let galleryPageSize = 40;
let galleryTag = '';
let galleryFormat = '';
let galleryFavOnly = false;
let galleryResourcesFilter = '';
let galleryWorkflowOnly = false;
let gallerySfwOnly = false;
let galleryFolder = '';              // Billfish 文件夹过滤（''=全部）
let galleryTotalPages = 1;
let galleryLoadingMore = false;
let galleryGen = 0;                  // 防过期：筛选变了就丢弃在途的旧请求
const gallerySeen = new Set();       // 已渲染的 image id，跨页去重（后端分页偶有重叠）

// ========== 瀑布流布局（JS 列分配：每张放进最短列，追加不回流，适合无限滚动） ==========
const GALLERY_GAP = 12;              // 列间距 / 卡片间距（与 .wf-masonry 的 gap 一致）
let masonryCols = [];                // 各列 DOM 容器
let masonryHeights = [];             // 各列当前累计高度（用于挑最短列）
let masonryColW = 0;                 // 单列宽度（px），按宽高比算卡片高
let loadedGalleryImages = [];        // 已放置的全部 img（resize / 缩略图尺寸变更时整体重排）

function galleryThumbMin() {
    const grid = document.getElementById('gallery-grid');
    const t = grid?.dataset.thumb || 'medium';
    return t === 'small' ? 140 : t === 'large' ? 260 : 200;
}

// 宽/高比；缺失（部分视频/旧记录无尺寸）默认 3:4
function imgAspect(img) {
    const w = img.width, h = img.height;
    return (w > 0 && h > 0) ? (w / h) : 0.75;
}

// 重建空列：按容器宽与缩略图档位算列数，清空 grid 后铺设列容器
function setupMasonry(grid) {
    const width = grid.clientWidth || grid.offsetWidth || 0;
    const minCol = galleryThumbMin();
    const count = Math.max(1, Math.floor((width + GALLERY_GAP) / (minCol + GALLERY_GAP)));
    masonryColW = count > 0 ? (width - (count - 1) * GALLERY_GAP) / count : width;
    grid.classList.add('wf-masonry');
    grid.innerHTML = '';
    masonryCols = [];
    masonryHeights = [];
    for (let i = 0; i < count; i++) {
        const col = document.createElement('div');
        col.className = 'wf-col';
        grid.appendChild(col);
        masonryCols.push(col);
        masonryHeights.push(0);
    }
}

// 把一张卡放进当前最短列
function placeGalleryCard(img) {
    if (!masonryCols.length) return;
    let idx = 0;
    for (let i = 1; i < masonryHeights.length; i++) {
        if (masonryHeights[i] < masonryHeights[idx]) idx = i;
    }
    masonryCols[idx].insertAdjacentHTML('beforeend', galleryCardHtml(img));
    masonryHeights[idx] += masonryColW / imgAspect(img) + GALLERY_GAP;  // 信息条是浮层不占高
}

// resize / 缩略图档位变更：列数变了才整体重排（同列数仅微调 colW，旧卡按 aspect-ratio 等比缩放无需重排）
function relayoutGalleryMasonry() {
    const grid = document.getElementById('gallery-grid');
    if (!grid || !loadedGalleryImages.length) return;
    if (galleryVideoObserver) { galleryVideoObserver.disconnect(); _wfVideoRatios.clear(); }
    setupMasonry(grid);
    const all = loadedGalleryImages;
    let i = 0;
    const step = () => {
        const end = Math.min(i + 24, all.length);
        for (; i < end; i++) placeGalleryCard(all[i]);
        observeGalleryVideosIn(grid);
        if (i < all.length) requestAnimationFrame(step);
    };
    step();
}

let _galleryResizeT = null;
function onGalleryResize() {
    clearTimeout(_galleryResizeT);
    _galleryResizeT = setTimeout(() => {
        const grid = document.getElementById('gallery-grid');
        if (!grid || !loadedGalleryImages.length) return;
        const minCol = galleryThumbMin();
        const width = grid.clientWidth || grid.offsetWidth || 0;
        const count = Math.max(1, Math.floor((width + GALLERY_GAP) / (minCol + GALLERY_GAP)));
        if (count !== masonryCols.length) {
            relayoutGalleryMasonry();
        } else {
            masonryColW = count > 0 ? (width - (count - 1) * GALLERY_GAP) / count : width;
        }
    }, 150);
}

// 只播放进入视口的图库视频：避免几十个视频同时解码/播放拖垮滚动。
// 连续流下不再整盘 disconnect，只观察新追加进来的视频。
let galleryVideoObserver = null;
const _wfVideoRatios = new Map();   // <video> → 最新可见比例（只保留当前可见的）
let _wfVideoPaused = false;         // 详情弹窗打开时暂停整个图库视频，把连接让给详情

// 在所有"当前可见"的视频里只播放最居中（可见比例最高）的那一个，其余暂停。
// 关键：同一时刻最多 1 路视频流。多个 loop 视频各占一条 HTTP 连接持续下载，会把浏览器
// 对本站 ~6 条连接打满，导致点开详情时 fetch/大图拿不到连接而空白——这是"点进去空白"的主因。
function _updateGalleryVideoPlayback() {
    let best = null, bestRatio = 0;
    for (const [v, ratio] of _wfVideoRatios) {
        if (ratio > bestRatio) { bestRatio = ratio; best = v; }
    }
    for (const [v] of _wfVideoRatios) {
        const shouldPlay = !_wfVideoPaused && v === best && bestRatio > 0.1;
        if (shouldPlay) { if (v.paused) v.play().catch(() => {}); }
        else if (!v.paused) v.pause();
    }
}
function ensureGalleryVideoObserver() {
    if (!galleryVideoObserver) {
        // 多档阈值才能比较"谁更居中"；不加 rootMargin → 视口外不预载视频，省连接
        galleryVideoObserver = new IntersectionObserver((entries) => {
            for (const e of entries) {
                if (e.isIntersecting) _wfVideoRatios.set(e.target, e.intersectionRatio);
                else { _wfVideoRatios.delete(e.target); if (!e.target.paused) e.target.pause(); }
            }
            _updateGalleryVideoPlayback();
        }, { threshold: [0, 0.1, 0.25, 0.5, 0.75, 1] });
    }
    return galleryVideoObserver;
}
function observeGalleryVideosIn(container) {
    const ob = ensureGalleryVideoObserver();
    container.querySelectorAll('video').forEach(v => ob.observe(v));
}
// 详情弹窗开/关调用：开→暂停所有图库视频释放连接；关→恢复按可见度自动播放
function setGalleryVideoPaused(paused) {
    _wfVideoPaused = paused;
    if (paused) { for (const [v] of _wfVideoRatios) { if (!v.paused) v.pause(); } }
    else _updateGalleryVideoPlayback();
}

function galleryCardHtml(img) {
    const name = img.custom_name || img.file_name;
    const hasWf = img.has_workflow
        ? '<span class="wf-gallery-badge">含 Workflow</span>' : '';
    const isVideo = img.media_type === 'video';
    const ext = (img.file_name || '').split('.').pop().toLowerCase();
    const formatLabel = ext ? ext.toUpperCase() : (isVideo ? 'VIDEO' : 'IMG');
    const formatBadge = `<span class="wf-gallery-fmt wf-gallery-fmt-${isVideo ? 'video' : 'image'}">${escapeAttr(formatLabel)}</span>`;
    const src = `/api/noctyra/workflow/image/${img.id}`;
    // 卡片图片用 480px WebP 缩略图（视频不缩略，详情/放大仍用原图）
    const mediaTag = isVideo
        // poster 用 card 尺寸小 WebP 封面：离屏只付一张封面图、消黑框；preload="none" 不预下载视频，
        // 仍是 <video>（保留 IntersectionObserver 视口内自动预览），进入视口才 play() 触发解码。
        ? `<video src="${src}" poster="${src}?size=card" muted loop playsinline preload="none"
                  disablepictureinpicture disableremoteplayback></video>`
        : `<img src="${src}?size=card" alt="${escapeAttr(name)}" loading="lazy" decoding="async">`;
    // NSFW 判定：用户手动标 OR CivitAI level 达阈值
    const isNsfw = !!img.user_nsfw || (img.nsfw_level || 0) >= wfState.nsfwBlurThreshold;
    const shouldBlur = wfState.blurNsfw && isNsfw;
    const cardCls = 'wf-gallery-card' + (shouldBlur ? ' wf-blurred' : '');
    const favBadge = img.favorite ? '<span class="wf-gallery-fav-mark" title="已收藏">♥</span>' : '';
    const nsfwBadge = img.user_nsfw
        ? '<span class="wf-gallery-badge wf-gallery-badge-nsfw">NSFW</span>' : '';
    // 资源缺失徽章：0 缺 → 不显示；>0 → 红色徽章显示 "X/N 缺失"
    const rs = img.resource_status;
    const resBadge = (rs && rs.total > 0 && rs.missing > 0)
        ? `<span class="wf-gallery-badge wf-gallery-badge-miss" title="本地缺 ${rs.missing} 个资源（共 ${rs.total} 个）">${rs.missing}/${rs.total} 缺</span>`
        : '';
    // 瀑布流：卡片用图片原始宽高比，整列等宽不等高（缺尺寸退回 3:4）
    const arStr = (img.width > 0 && img.height > 0) ? `${img.width} / ${img.height}` : '3 / 4';
    return `
        <div class="${cardCls}" data-id="${img.id}" data-media="${escapeAttr(img.media_type || 'image')}" style="aspect-ratio:${arStr}">
            ${mediaTag}
            ${formatBadge}
            ${favBadge}
            <div class="wf-gallery-info">
                <div class="wf-gallery-name" title="${escapeAttr(name)}">${escapeAttr(name)}</div>
                <div class="wf-gallery-badges">${nsfwBadge}${resBadge}${hasWf}</div>
            </div>
            <button class="wf-gallery-card-delete" data-id="${img.id}" title="删除">×</button>
        </div>
    `;
}

// 把一页 images 按瀑布流分配进各列（追加，不回流）。返回本次新增卡片数（去重后）。
function appendGalleryChunk(grid, images) {
    const fresh = images.filter(img => !gallerySeen.has(img.id));
    if (fresh.length === 0) return 0;
    fresh.forEach(img => { gallerySeen.add(img.id); loadedGalleryImages.push(img); });
    if (!masonryCols.length) setupMasonry(grid);   // 兜底：未铺列时先建列
    // 分帧批量：每帧 ~12 张，避免整页一次性放置的同步长任务
    const gen = galleryGen;
    let i = 0;
    const step = () => {
        if (gen !== galleryGen) return;   // 已被新一轮 loadGallery 接管 → 停止
        const end = Math.min(i + 12, fresh.length);
        for (; i < end; i++) placeGalleryCard(fresh[i]);
        observeGalleryVideosIn(grid);     // 给新批视频登记（重复 observe 同一元素是无操作）
        if (i < fresh.length) requestAnimationFrame(step);
    };
    step();
    return fresh.length;
}

// 从第 1 页重新加载（筛选/搜索/设置变更入口）。后续滚动由 loadMoreGallery 续接。
// 图库加载骨架(慢加载才显示)
function showGallerySkeleton(grid) {
    if (!grid) return;
    grid.classList.remove('wf-masonry');
    grid.classList.add('wf-skeleton-grid');
    grid.innerHTML = Array.from({ length: 10 },
        () => '<div class="wf-skeleton-card"><div class="skeleton-shimmer"></div></div>').join('');
}
function hideGallerySkeleton(grid) {
    if (grid) grid.classList.remove('wf-skeleton-grid');
}

function _galleryHasFilters() {
    return !!(gallerySearch || galleryTag || galleryFavOnly || galleryWorkflowOnly ||
        galleryFormat || (galleryResourcesFilter && galleryResourcesFilter !== 'all'));
}

// 空状态分两种:筛选无结果(给清空) vs 真空库(给引导)。带图标 + 主副文案。
function renderGalleryEmpty(empty) {
    if (!empty) return;
    if (_galleryHasFilters()) {
        empty.innerHTML = `
            <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
            <p class="wf-empty-title">没有符合条件的图片</p>
            <p class="wf-empty-sub">试试调整或清空筛选条件</p>
            <button class="wf-btn wf-btn-primary wf-btn-sm" id="wf-empty-clear">清空筛选</button>`;
        empty.querySelector('#wf-empty-clear')?.addEventListener('click', () => {
            const s = document.getElementById('gallery-search');
            if (s) s.value = '';
            gallerySearch = '';
            document.getElementById('wf-clear-filters')?.click();
        });
    } else {
        empty.innerHTML = `
            <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>
            <p class="wf-empty-title">图库为空</p>
            <p class="wf-empty-sub">粘贴 CivitAI 图片/视频链接并保存,或注册文件夹后扫描</p>`;
    }
    empty.style.display = 'flex';
}

async function loadGallery() {
    const grid = document.getElementById('gallery-grid');
    const empty = document.getElementById('gallery-empty');
    const pagination = document.getElementById('gallery-pagination');
    const countEl = document.getElementById('gallery-count');

    galleryPage = 1;
    const gen = ++galleryGen;
    gallerySeen.clear();
    loadedGalleryImages = [];

    const skTimer = setTimeout(() => showGallerySkeleton(grid), 180);
    try {
        const res = await apiGalleryList(galleryPage, gallerySearch, galleryPageSize,
                                          galleryTag, galleryFavOnly, galleryResourcesFilter, galleryWorkflowOnly,
                                          galleryFormat, '', galleryFolder);
        clearTimeout(skTimer);
        hideGallerySkeleton(grid);
        if (gen !== galleryGen) return;   // 期间筛选又变了，丢弃这次结果
        if (!res.success) { showToast('加载图库失败', 'error'); return; }

        galleryTotalPages = res.total_pages || 1;
        countEl.textContent = `(${res.total})`;
        if (pagination) pagination.innerHTML = '';   // 翻页器已退役，连续滚动接管

        if (res.images.length === 0) {
            grid.innerHTML = '';
            renderGalleryEmpty(empty);
            return;
        }
        empty.style.display = 'none';
        if (galleryVideoObserver) { galleryVideoObserver.disconnect(); _wfVideoRatios.clear(); }  // 旧视频随重建列清掉
        setupMasonry(grid);   // 清空 + 按当前宽度铺列
        appendGalleryChunk(grid, res.images);
    } catch (e) {
        clearTimeout(skTimer);
        hideGallerySkeleton(grid);
        console.error('loadGallery error:', e);
        showToast('加载图库失败：' + (e.message || '网络错误'), 'error');
        grid.innerHTML = '<div class="wf-gallery-empty"><p>加载失败，请刷新重试</p></div>';
        empty.style.display = 'none';
    }
}

// 滚到底部哨兵时续拉下一页（追加，不清空）
async function loadMoreGallery() {
    if (galleryLoadingMore || galleryPage >= galleryTotalPages) return;
    galleryLoadingMore = true;
    const gen = galleryGen;
    const grid = document.getElementById('gallery-grid');
    try {
        // 后端分页偶有整页重复（排序值并列等）→ 去重后可能 0 新增；继续向后取，直到有新卡或到末页
        while (galleryPage < galleryTotalPages) {
            galleryPage++;
            const res = await apiGalleryList(galleryPage, gallerySearch, galleryPageSize,
                                          galleryTag, galleryFavOnly, galleryResourcesFilter, galleryWorkflowOnly,
                                          galleryFormat, '', galleryFolder);
            if (gen !== galleryGen) return;
            if (!res.success || res.images.length === 0) break;
            if (appendGalleryChunk(grid, res.images) > 0) break;
        }
    } catch (e) {
        if (gen === galleryGen) galleryPage--;
    } finally {
        galleryLoadingMore = false;
    }
}

function initGalleryInfiniteScroll() {
    const sentinel = document.getElementById('wf-scroll-sentinel');
    if (!sentinel) return;
    new IntersectionObserver(entries => {
        if (entries[0].isIntersecting && !galleryLoadingMore && galleryPage < galleryTotalPages) {
            loadMoreGallery();
        }
    }, { rootMargin: '200px' }).observe(sentinel);
}


// ========== Local Import ==========

function setupLocalImport() {
    const pickBtn = document.getElementById('import-drop-zone');
    const fileInput = document.getElementById('import-file-input');
    if (!pickBtn || !fileInput) return;

    // 点按钮 → 弹系统选择框
    pickBtn.addEventListener('click', () => fileInput.click());

    // 仍支持拖拽：整个 .wf-main 都接住 drop 事件
    const dropRegion = document.querySelector('.wf-main');
    if (dropRegion) {
        ['dragenter', 'dragover'].forEach(evt => {
            dropRegion.addEventListener(evt, e => { e.preventDefault(); pickBtn.classList.add('dragover'); });
        });
        ['dragleave', 'drop'].forEach(evt => {
            dropRegion.addEventListener(evt, e => { e.preventDefault(); pickBtn.classList.remove('dragover'); });
        });
        dropRegion.addEventListener('drop', e => {
            const files = e.dataTransfer?.files;
            if (files && files.length > 0) handleImportFile(files[0]);
        });
    }

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) handleImportFile(fileInput.files[0]);
        fileInput.value = '';
    });
}

async function handleImportFile(file) {
    if (!file.type.startsWith('image/') && !file.type.startsWith('video/')) {
        showToast('请选择图片或视频文件', 'error');
        return;
    }
    const pickBtn = document.getElementById('import-drop-zone');
    const statusEl = document.getElementById('fetch-status');
    pickBtn.classList.add('uploading');
    if (statusEl) statusEl.textContent = `上传中：${file.name}`;
    try {
        const res = await apiImportLocal(file);
        if (res.success) {
            showToast(`导入成功${res.has_workflow ? '（含 Workflow）' : ''}`, 'success');
            if (statusEl) statusEl.textContent = `已导入 ${file.name}`;
            loadGallery();
        } else {
            showToast(res.error || '导入失败', 'error');
            if (statusEl) statusEl.textContent = '导入失败：' + (res.error || '');
        }
    } catch (e) {
        showToast('上传异常: ' + e.message, 'error');
    } finally {
        pickBtn.classList.remove('uploading');
    }
}


// ========== 批量导入 WS ==========

// 轻量 WebSocket 客户端（只给批量导入监听 recipe_import_progress 用）
let _wsBatch = null;
const _wsBatchHandlers = new Map(); // event name -> fn
function ensureBatchWs() {
    if (_wsBatch && _wsBatch.readyState === WebSocket.OPEN) return _wsBatch;
    if (_wsBatch && _wsBatch.readyState === WebSocket.CONNECTING) return _wsBatch;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/api/noctyra/ws`;
    _wsBatch = new WebSocket(url);
    _wsBatch.addEventListener('message', (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        const h = _wsBatchHandlers.get(msg.event);
        if (h) h(msg.data || {});
    });
    return _wsBatch;
}
function onBatchWs(eventName, fn) {
    _wsBatchHandlers.set(eventName, fn);
    ensureBatchWs();
}

function setupBatchImport() {
    const openBtn = document.getElementById('batch-import-btn');
    const overlay = document.getElementById('batch-import-overlay');
    const closeBtn = document.getElementById('batch-import-close');
    const cancelBtn = document.getElementById('batch-import-cancel');
    const submitBtn = document.getElementById('batch-import-submit');
    const textarea = document.getElementById('batch-import-textarea');
    const progress = document.getElementById('batch-import-progress');
    const fillEl = document.getElementById('batch-import-fill');
    const textEl = document.getElementById('batch-import-text');
    if (!openBtn || !overlay) return;

    const show = () => {
        overlay.style.display = 'flex';
        requestAnimationFrame(() => overlay.classList.add('show'));
        setTimeout(() => textarea && textarea.focus(), 50);
    };
    const hide = () => {
        overlay.classList.remove('show');
        overlay.style.display = 'none';
    };

    openBtn.addEventListener('click', show);
    closeBtn.addEventListener('click', hide);
    cancelBtn.addEventListener('click', hide);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) hide(); });

    // WS 监听导入进度
    onBatchWs('recipe_import_progress', (data) => {
        if (data.stage === 'complete') {
            const r = data;
            textEl.textContent = `完成 · 成功 ${r.ok || 0} · 重复 ${r.duplicate || 0} · 失败 ${r.failed || 0}`;
            fillEl.style.width = '100%';
            submitBtn.disabled = false;
            submitBtn.textContent = '开始导入';
            showToast(`批量导入完成: 成功 ${r.ok || 0} / 重复 ${r.duplicate || 0} / 失败 ${r.failed || 0}`, 'success');
            loadGallery();
            setTimeout(() => { progress.style.display = 'none'; fillEl.style.width = '0%'; }, 4000);
            return;
        }
        if (data.stage === 'error') {
            textEl.textContent = `错误: ${data.error || ''}`;
            submitBtn.disabled = false;
            submitBtn.textContent = '开始导入';
            return;
        }
        // progress
        const cur = data.current || 0;
        const total = data.total || 1;
        const pct = Math.min(100, Math.round(cur * 100 / total));
        fillEl.style.width = pct + '%';
        textEl.textContent = `${cur}/${total} · ${data.file || ''} → ${data.result || ''}`;
    });

    // 刷新后恢复：若后端正在批量导入，重开进度弹窗（WS 会接着推进度）
    fetch('/api/noctyra/recipe/batch-import/status').then(r => r.json()).then(st => {
        if (st && st.success && st.running) {
            show();
            progress.style.display = '';
            fillEl.style.width = '0%';
            textEl.textContent = `批量导入进行中…（共 ${st.total || '?'} 个）`;
            submitBtn.disabled = true;
            submitBtn.textContent = '导入中...';
        }
    }).catch(() => {});

    submitBtn.addEventListener('click', async () => {
        const raw = textarea.value || '';
        const urls = raw.split('\n').map(s => s.trim()).filter(Boolean);
        if (urls.length === 0) {
            showToast('请粘贴至少一个 URL', 'warning');
            return;
        }
        submitBtn.disabled = true;
        submitBtn.textContent = '提交中...';
        progress.style.display = '';
        fillEl.style.width = '0%';
        textEl.textContent = '正在解析 URL...';
        try {
            const resp = await fetch('/api/noctyra/recipe/batch-import', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ urls }),
            });
            const res = await resp.json();
            if (!res.success) {
                showToast(res.error || '启动失败', 'error');
                submitBtn.disabled = false;
                submitBtn.textContent = '开始导入';
                progress.style.display = 'none';
                return;
            }
            textEl.textContent = `已派发 ${res.total} 个任务，等待 WS 进度...`;
        } catch (e) {
            showToast('请求异常: ' + e.message, 'error');
            submitBtn.disabled = false;
            submitBtn.textContent = '开始导入';
            progress.style.display = 'none';
        }
    });
}


// ========== Init ==========

document.addEventListener('DOMContentLoaded', () => {
    initFocusTrap();

    // ——— 记住浏览位置：刷新后恢复上次的筛选/文件夹/搜索/滚动（sessionStorage，仅本标签页）———
    // 从 gallery-grid 向上找真正滚动的祖先容器（图库滚的是 .wf-main 之类，非 window）。
    const _wfScrollEl = () => {
        const main = document.querySelector('.wf-main');   // 已知滚动容器（overflow-y:auto），直接用最稳
        if (main) return main;
        // 兜底：从 gallery-grid 向上找滚动祖先
        let el = document.getElementById('gallery-grid');
        while (el && el !== document.body) {
            const oy = getComputedStyle(el).overflowY;
            if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 4) return el;
            el = el.parentElement;
        }
        return document.scrollingElement || document.documentElement;
    };
    // 恢复筛选变量（放在 loadRuntimeInfo→loadGallery 之前，首次加载即用这些值；chips/侧栏随后读变量自动高亮）
    let _wfRestoreScroll = 0;
    try {
        const saved = JSON.parse(sessionStorage.getItem('noctyra_wf_view') || 'null');
        if (saved && saved.f) {
            const f = saved.f;
            gallerySearch = f.search || '';
            galleryTag = f.tag || '';
            galleryFormat = f.format || '';
            galleryFavOnly = !!f.fav;
            galleryResourcesFilter = f.resources || '';
            galleryWorkflowOnly = !!f.workflow;
            galleryFolder = f.folder || '';
            const si = document.getElementById('gallery-search');
            if (si) si.value = gallerySearch;
            _wfRestoreScroll = Number(saved.s) || 0;
        }
    } catch (_) { /* 隐私模式/损坏数据，忽略 */ }
    // 存视图：pagehide 兜底 + 滚动时节流保存（瀑布流下 pagehide 那刻读 scrollTop 时机不稳，边滚边存更可靠）
    const _saveWfView = () => {
        try {
            const sc = _wfScrollEl();
            sessionStorage.setItem('noctyra_wf_view', JSON.stringify({
                f: { search: gallerySearch, tag: galleryTag, format: galleryFormat, fav: galleryFavOnly,
                     resources: galleryResourcesFilter, workflow: galleryWorkflowOnly, folder: galleryFolder },
                s: sc ? sc.scrollTop : 0,
            }));
        } catch (_) { /* 忽略 */ }
    };
    window.addEventListener('pagehide', _saveWfView);
    const _wfMain = document.querySelector('.wf-main');   // 真正滚动的容器（overflow-y:auto）
    if (_wfMain) {
        let _svT = null;
        _wfMain.addEventListener('scroll', () => {
            if (_svT) return;
            _svT = setTimeout(() => { _svT = null; _saveWfView(); }, 500);
        }, { passive: true });
    }
    // 恢复滚动：无限滚动首屏内容不够高，先续拉到能滚到原位再设 scrollTop（限次防跑飞）
    if (_wfRestoreScroll > 0) {
        let _tries = 0;
        const _restoreWfScroll = async () => {
            const sc = _wfScrollEl();
            if (!sc) return;
            while (sc.scrollHeight - sc.clientHeight < _wfRestoreScroll && galleryPage < galleryTotalPages && _tries < 30) {
                _tries++;
                await loadMoreGallery();
            }
            sc.scrollTop = _wfRestoreScroll;
        };
        const _wait = setInterval(() => {
            const grid = document.getElementById('gallery-grid');
            if (grid && grid.children.length > 0) { clearInterval(_wait); _restoreWfScroll(); }
        }, 100);
        setTimeout(() => clearInterval(_wait), 8000);   // 兜底停表
    }

    // 先拉一下运行时信息（civitai_source_host + _runtime_mode 徽章），非阻塞
    loadRuntimeInfo();
    initStandaloneShutdownWarning();

    // Fetch（一步式：拉取 + 入库，详情看详情弹窗）
    document.getElementById('fetch-btn').addEventListener('click', handleFetch);
    document.getElementById('civitai-url-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') handleFetch();
    });

    // Gallery events (delegation)
    document.getElementById('gallery-grid').addEventListener('click', async (e) => {
        const deleteBtn = e.target.closest('.wf-gallery-card-delete');
        if (deleteBtn) {
            e.stopPropagation();
            if (!await showConfirm({ title: '从图库删除', danger: true, okText: '删除', message: '从图库删除这张图片？\n你本地文件夹里的原图不会被删除，只移除图库索引。' })) return;
            const id = deleteBtn.dataset.id;
            try {
                const res = await apiGalleryDelete(id);
                if (res.success) {
                    showToast('已删除', 'success');
                    loadGallery();
                } else {
                    showToast(res.error || '删除失败', 'error');
                }
            } catch (err) {
                showToast('删除失败：网络错误', 'error');  // 防 fetch 异常变成未捕获 Promise
            }
            return;
        }
        const card = e.target.closest('.wf-gallery-card');
        if (card) { setGalleryVideoPaused(true); openDetail(card.dataset.id); }   // 暂停图库视频，把连接让给详情
    });

    // 滚到底自动续拉下一页（替代翻页器，消除"页末补空位"）
    initGalleryInfiniteScroll();

    // Billfish 文件夹树侧边栏：点选切换过滤、扫描、增删注册文件夹
    initFolderSidebar({
        onSelect: (path) => {
            galleryFolder = path || '';
            galleryPage = 1;
            loadGallery();
        },
        toast: showToast,
        initial: galleryFolder,   // 恢复上次选中的文件夹并高亮（galleryFolder 已在上方从 sessionStorage 恢复）
    });

    // 窗口尺寸变化时按需重排瀑布流（列数变了才整体重排）
    window.addEventListener('resize', onGalleryResize);

    // Detail overlay
    const detailOverlay = document.getElementById('detail-overlay');
    document.getElementById('detail-close').addEventListener('click', () => {
        detailOverlay.classList.remove('show');
        setGalleryVideoPaused(false);
    });
    detailOverlay.addEventListener('click', (e) => {
        if (e.target === detailOverlay) { detailOverlay.classList.remove('show'); setGalleryVideoPaused(false); }
    });

    // Gallery search
    let searchTimer = null;
    document.getElementById('gallery-search').addEventListener('input', (e) => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
            gallerySearch = e.target.value.trim();
            galleryPage = 1;
            loadGallery();
        }, 300);
    });

    // 筛选胶囊（标签 / 资源完整度 / 收藏）—— 取代原先的下拉 + 收藏按钮，和模型页一致
    initWfFilterPanel();

    // 顶栏：明暗主题切换 + 仅显示 SFW 快捷开关
    initThemeToggle('wf-theme-toggle');
    document.getElementById('wf-sfw-toggle')?.addEventListener('click', toggleGallerySfw);
    initSfwSync();   // 跟随模型页/选择器的 SFW 切换

    // 批量导入
    setupBatchImport();

    // 设置弹窗（与模型管理器共用；data-section="gallery" 让齿轮按钮默认打开图库 Tab）
    initSettings();

    // 设置变更 → 实时应用到当前页
    window.addEventListener('noctyra-gallery-settings-changed', (ev) => {
        const { key, value } = ev.detail || {};
        if (key === 'gallery_page_size') {
            const n = parseInt(value) || 40;
            if (n !== galleryPageSize) {
                galleryPageSize = n;
                galleryPage = 1;
                loadGallery();
            }
        } else if (key === 'gallery_thumb_size') {
            const grid = document.getElementById('gallery-grid');
            if (grid) grid.dataset.thumb = value;
            relayoutGalleryMasonry();   // 档位变 → 列数/列宽变 → 整体重排
        } else if (key === 'gallery_show_filename') {
            const grid = document.getElementById('gallery-grid');
            if (grid) grid.dataset.showFilename = value ? 'true' : 'false';
        } else if (key === 'gallery_blur_nsfw') {
            wfState.blurNsfw = !!value;
            loadGallery();  // 重绘以应用 .wf-blurred 类
        } else if (key === 'gallery_nsfw_blur_threshold') {
            wfState.nsfwBlurThreshold = parseInt(value) || 4;
            loadGallery();
        } else if (key === 'gallery_show_only_sfw') {
            // SFW 过滤影响后端返回的条目，重载整个列表
            galleryPage = 1;
            loadGallery();
        }
    });

    // Keyboard
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && detailOverlay.classList.contains('show')) {
            detailOverlay.classList.remove('show');
            setGalleryVideoPaused(false);
        }
    });

    // Local import
    setupLocalImport();

    // detail.js 通过事件请求重新加载图库（避免循环依赖）
    window.addEventListener('noctyra-wf-reload-gallery', () => {
        loadGallery();
        loadGalleryTags();  // 标签/收藏变更可能影响下拉项
    });

    // 跨标签页主题同步：支持 dark / light / auto
    const _applyTheme = (mode) => {
        let effective = mode;
        if (mode === 'auto') {
            effective = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
        }
        if (effective === 'light') {
            document.documentElement.dataset.theme = 'light';
        } else {
            delete document.documentElement.dataset.theme;
        }
    };
    window.addEventListener('storage', (e) => {
        if (e.key !== 'noctyra_theme') return;
        _applyTheme(e.newValue || 'dark');
    });
    // auto 模式下跟随系统主题变更
    const _mq = window.matchMedia('(prefers-color-scheme: light)');
    const _onOsThemeChange = () => {
        if ((localStorage.getItem('noctyra_theme') || 'dark') === 'auto') {
            _applyTheme('auto');
        }
    };
    if (_mq.addEventListener) _mq.addEventListener('change', _onOsThemeChange);
    else if (_mq.addListener) _mq.addListener(_onOsThemeChange);

    // 首次图库加载在 loadRuntimeInfo 完成后触发（那里拿到 page_size）
});
