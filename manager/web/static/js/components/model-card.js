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
 * 单个模型卡片组件 — 丰富信息展示
 */
import { state } from '../state.js';
import * as api from '../api.js';
const { previewUrl, TRANSPARENT_PX } = api;
import { escapeHtml, escapeAttr, formatSize, formatNumber } from '../utils.js';

// 给单个媒体元素挂真实 src（+ 未命中自愈 + 视频 load）。虚拟滚动渲染卡片时调用。
function _applyRealSrc(el) {
    const realSrc = el.dataset.src;
    if (realSrc && !el.src.startsWith(realSrc)) {
        attachPreviewRetry(el);   // 未命中(404)→有界重试，后台缓存好后自愈
        el.src = realSrc;
    }
    if (el.tagName === 'VIDEO') {
        try { el.load(); } catch (_) { /* 忽略 */ }   // 触发 preload=metadata
    }
}

/**
 * 立即给 container 内所有 [data-src] 媒体挂真实 src（不走 IntersectionObserver）。
 * 虚拟滚动用：卡片本就在视口附近才创建，立即载图即可，避免「未进视口就被移除的卡」
 * 把 img 永久 observe 着造成泄漏。
 */
export function loadCardMedia(container) {
    if (!container) return;
    container.querySelectorAll('img[data-src], video[data-src]').forEach(_applyRealSrc);
}

// 本地优先预览的自愈：网格图未命中后端返回 404。
// 关键：出错时立刻把 <img> 藏起来，露出 .card-preview 的「主题占位背景」——否则 404 的
// 破图框（浅色 + alt 文字）会叠在深色占位背景上，看起来像"黑白两个 no preview"。
// 只在真正加载到非占位图时才显示；后台缓存好后某次重试即出图，无需手动刷新。
function attachPreviewRetry(el) {
    if (el._retryInit) return;
    el._retryInit = true;
    let tries = 0;
    const MAX = 3;
    const showWhenReal = () => {
        if (el.tagName === 'VIDEO' || el.naturalWidth > 2) el.style.visibility = '';
    };
    el.addEventListener(el.tagName === 'VIDEO' ? 'loadeddata' : 'load', showWhenReal);
    el.addEventListener('error', () => {
        el.style.visibility = 'hidden';   // 立刻藏破图，露出干净的主题占位背景
        const base = el.dataset.src;
        if (!base || tries >= MAX) return;
        tries++;
        // 后台缓存需要时间，逐步退避 + 抖动，避免整屏同时重试
        const delay = 3500 * tries + Math.random() * 2500;
        setTimeout(() => {
            if (!el.isConnected) return;
            // 加 r 参数破 404 缓存，触发对服务端的全新请求（后端忽略该参数）；成功才会显示
            el.src = base + (base.includes('?') ? '&' : '?') + 'r=' + tries;
            if (el.tagName === 'VIDEO') { try { el.load(); } catch (_) { /* 忽略 */ } }
        }, delay);
    });
}

const BASE_MODEL_COLORS = {
    // Stable Diffusion 系列
    'SD 1': '#4caf50',
    'SD 2': '#9c27b0',
    'SD 3': '#ff5722',
    'SDXL': '#e94560',
    'Stable Cascade': '#6366f1',
    // Flux 系列
    'Flux': '#00bcd4',
    // 风格化模型
    'Pony': '#ff9800',
    'Illustrious': '#8b5cf6',
    'NoobAI': '#a78bfa',
    'Anima': '#ec4899',
    // 国产模型
    'Hunyuan': '#06b6d4',
    'Kolors': '#10b981',
    'Qwen': '#0ea5e9',
    'ZImage': '#3b82f6',
    'Wan': '#2563eb',
    'Seedream': '#7c3aed',
    'Seedance': '#7c3aed',
    'Kling': '#0d9488',
    'Vidu': '#059669',
    // 视频模型
    'SVD': '#0ea5e9',
    'Mochi': '#d946ef',
    'LTXV': '#8b5cf6',
    'CogVideo': '#6366f1',
    'Lumina': '#d946ef',
    // 其他
    'PixArt': '#f97316',
    'AuraFlow': '#a855f7',
    'Playground': '#14b8a6',
    'HiDream': '#f43f5e',
    'Chroma': '#84cc16',
    'Sora': '#1d4ed8',
    'Veo': '#15803d',
    'Imagen': '#ea580c',
    'OpenAI': '#1d4ed8',
    'Nano Banana': '#facc15',
    'ODOR': '#64748b',
};

const MODEL_TYPE_LABELS = {
    'LORA': 'LoRA',
    'Checkpoint': 'CKPT',
    'TextualInversion': 'TI',
    'LoCon': 'LoCon',
    'DoRA': 'DoRA',
    'Controlnet': 'CN',
    'VAE': 'VAE',
    'Upscaler': 'UP',
    'TextEncoder': 'TE',
    'CLIPVision': 'CLIP-V',
    'MotionModule': 'Motion',
    'Detection': 'Det',
};

function getBaseModelColor(bm) {
    if (!bm) return '#666';
    for (const [key, color] of Object.entries(BASE_MODEL_COLORS)) {
        if (bm.toLowerCase().includes(key.toLowerCase())) return color;
    }
    return '#666';
}

export function createModelCard(model) {
    const card = document.createElement('div');
    card.className = 'model-card';
    card.dataset.sha256 = model.sha256 || '';
    card.dataset.filePath = model.file_path || '';

    const s = state.settings || {};
    const displayName = s.model_name_display === 'file_name'
        ? model.file_name
        : (model.model_name || model.file_name);
    const cardPreviewUrl = previewUrl(model.preview_url, 'card');  // 列表卡片用缩略图
    // 网格缩略图走"本地优先"：带 nofetch=1，后端未命中时不前台联网（丢后台队列 + 返回 404），
    // 防止后台跑任务/烂网络时一堆未缓存图占满浏览器连接，导致卡片卡住、滚动顿。
    const gridPreviewUrl = (cardPreviewUrl && cardPreviewUrl !== TRANSPARENT_PX)
        ? cardPreviewUrl + (cardPreviewUrl.includes('?') ? '&' : '?') + 'nofetch=1'
        : cardPreviewUrl;
    const isVideo = model.preview_type === 'video';
    // 模糊阈值：根据主预览图的 nsfw_level 决定；数据缺失时回退到 model.nsfw
    // CivitAI nsfwLevel: 1=PG, 2=PG13, 4=R, 8=X, 16=XXX, 32=Blocked
    const threshold = parseInt(s.nsfw_blur_threshold) || 4;
    const level = model.preview_nsfw_level || (model.nsfw ? 16 : 0);
    const blurNsfw = s.blur_nsfw !== false && level >= threshold;

    // 多图指示器
    const piu = model.preview_image_urls || [];
    const mediaCount = piu.length;
    let multiImgBadge = '';
    if (mediaCount > 1) {
        const vidCount = piu.filter(p => p.type === 'video').length;
        const imgCount = mediaCount - vidCount;
        const parts = [];
        if (imgCount > 0) parts.push(`<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg> ${imgCount}`);
        if (vidCount > 0) parts.push(`<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> ${vidCount}`);
        multiImgBadge = `<span class="card-img-count">${parts.join('<span class="card-count-sep"></span>')}</span>`;
    }

    // 版本名
    const versionHtml = model.version_name
        ? `<span class="card-version">${escapeHtml(model.version_name)}</span>` : '';

    // 类型标签
    const typeLabel = MODEL_TYPE_LABELS[model.civitai_model_type] || '';

    // 评分 & 下载（CivitAI 优先，回退到 HF）
    let statsHtml = '';
    const statParts = [];
    if (model.thumbs_up > 0) statParts.push(`👍 ${formatNumber(model.thumbs_up)}`);
    if (model.downloads > 0) statParts.push(`⬇ ${formatNumber(model.downloads)}`);
    if (statParts.length === 0) {
        if (model.hf_likes > 0) statParts.push(`❤ ${formatNumber(model.hf_likes)}`);
        if (model.hf_downloads > 0) statParts.push(`⬇ ${formatNumber(model.hf_downloads)}`);
    }
    if (model.usage_count > 0) statParts.push(`▶ ${formatNumber(model.usage_count)}`);
    if (statParts.length > 0) {
        statsHtml = `<div class="card-stats">${statParts.join(' · ')}</div>`;
    }

    // 作者（CivitAI creator 优先，回退到 HF author）
    const authorName = model.creator || model.hf_author || '';

    // 来源标记（可同时显示多个）
    const hasCivitai = model.source === 'civitai' && model.civitai_model_id;
    const hasHF = !!(model.hf_repo_id || model.hf_url || model.source === 'huggingface');
    let sourceHtml = '';
    if (hasCivitai) sourceHtml += `<img class="source-icon source-civitai" src="/noctyra_static/images/civitai-logo.svg" title="CivitAI" alt="CivitAI">`;
    if (hasHF) sourceHtml += `<img class="source-icon source-hf" src="/noctyra_static/images/hf-logo.svg" title="HuggingFace" alt="HuggingFace">`;
    if (!model.matched) sourceHtml = '<span class="source-icon source-unmatched" title="未匹配">?</span>';

    const isFav = model.favorite;

    const isSelected = state.selectMode && state.selectedModels.has(model.file_path);
    if (state.selectMode) card.classList.add('select-mode');
    if (isSelected) card.classList.add('selected');

    card.draggable = true;
    card.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/plain', model.file_path);
        e.dataTransfer.effectAllowed = 'move';
        card.classList.add('dragging');
    });
    card.addEventListener('dragend', () => card.classList.remove('dragging'));

    if (isVideo && s.autoplay_video_on_hover !== false) {
        card.addEventListener('mouseenter', () => {
            if (card.querySelector('video')) return;
            const img = card.querySelector('img[data-video-src]');
            const vsrc = img && img.dataset.videoSrc;
            if (!vsrc) return;
            const v = document.createElement('video');
            v.src = vsrc;
            v.muted = true; v.loop = true; v.playsInline = true;
            v.className = 'card-hover-video';
            v.setAttribute('disablepictureinpicture', '');
            v.setAttribute('disableremoteplayback', '');
            (img.parentElement || card).appendChild(v);
            v.play().catch(() => {});
        });
        card.addEventListener('mouseleave', () => {
            const v = card.querySelector('video.card-hover-video');
            if (v) v.remove();
        });
    }

    // 懒加载：data-src 等进入视口后由 IntersectionObserver 赋给 src
    // 无 preview_url（TRANSPARENT_PX）时直接 src 显示占位，不走懒加载
    const isRealPreview = cardPreviewUrl && cardPreviewUrl !== TRANSPARENT_PX;
    let mediaHtml;
    if (isVideo && isRealPreview) {
        // 无 onerror 内联隐藏：未命中由懒加载里的有界重试自愈（attachPreviewRetry）
        // 视频卡片用静态首帧（后端对视频 size=card 抽帧返回 webp），避免大量 <video> 同时解码卡顿；
        // hover 时才动态创建 <video> 播放原片（见上方 mouseenter）
        mediaHtml = `<img src="${TRANSPARENT_PX}" data-src="${escapeAttr(gridPreviewUrl)}"
                          data-video-src="${escapeAttr(previewUrl(model.preview_url))}"
                          alt="${escapeAttr(displayName)}" decoding="async">
                     <span class="card-video-icon">&#9654;</span>`;
    } else if (isRealPreview) {
        mediaHtml = `<img src="${TRANSPARENT_PX}" data-src="${escapeAttr(gridPreviewUrl)}"
                          alt="${escapeAttr(displayName)}" decoding="async">`;
    } else {
        mediaHtml = `<img src="${escapeAttr(cardPreviewUrl)}" alt="${escapeAttr(displayName)}" decoding="async"
                          onerror="this.style.visibility='hidden'">`;
    }
    card.innerHTML = `
        <div class="card-preview${blurNsfw ? ' nsfw-blur' : ''}">
            ${mediaHtml}
            ${multiImgBadge}
            ${state.selectMode ? `<div class="card-checkbox${isSelected ? ' checked' : ''}"></div>` : ''}
            <button class="card-fav-btn${isFav ? ' active' : ''}" title="收藏" data-file-path="${escapeAttr(model.file_path)}" aria-label="收藏"><svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M12 2.5l2.928 6.34 6.822.76-5.065 4.66 1.39 6.74L12 17.65 5.925 20.99l1.39-6.74-5.065-4.66 6.822-.76L12 2.5z"/></svg></button>
            ${model.file_deleted && (model.civitai_version_id || model.hf_repo_id) ? `<button class="card-redownload-btn" title="重新下载"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button>` : ''}
        </div>
        <div class="card-info">
            <div class="card-name" title="${escapeAttr(model.file_name)}">
                ${typeLabel ? `<span class="card-type-label">${typeLabel}</span>` : ''}${escapeHtml(displayName)}
            </div>
            ${versionHtml}
            <div class="card-badges">
                ${model.file_corrupt ? '<span class="badge badge-corrupt" title="文件损坏（safetensors 头/数据区不一致，ComfyUI 加载会失败）。右键「检测是否损坏」看详情，或「重新下载（覆盖）」修复">⚠ 损坏</span>' : ''}
                ${model.file_deleted ? '<span class="badge badge-deleted">已删除</span>' : ''}
                ${model.base_model && model.base_model !== 'Unknown'
                    ? `<span class="badge" style="background:${getBaseModelColor(model.base_model)}">${escapeHtml(model.base_model)}</span>` : ''}
                ${sourceHtml}
                ${(model.preview_status && model.preview_status.total > 0 && model.preview_status.missing > 0)
                    ? `<span class="badge badge-preview-missing" title="本地缺 ${model.preview_status.missing}/${model.preview_status.total} 张预览图${model.preview_status.dead > 0 ? `（其中 ${model.preview_status.dead} 张已下架）` : ''}">📷 ${model.preview_status.missing}/${model.preview_status.total}</span>` : ''}
                ${(model.preview_status && model.preview_status.dead > 0)
                    ? `<span class="badge badge-preview-failed" title="${model.preview_status.dead} 张预览已下架(404/410)，重试无用">死链 ${model.preview_status.dead}</span>` : ''}
                <span class="badge badge-size">${formatSize(model.file_size)}</span>
            </div>
            ${statsHtml}
            ${authorName ? `<div class="card-creator">by ${escapeHtml(authorName)}</div>` : ''}
        </div>
    `;

    // 重新下载按钮事件
    const redownloadBtn = card.querySelector('.card-redownload-btn');
    if (redownloadBtn) {
        redownloadBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            let downloadUrl;
            let versionId = null;
            if (model.civitai_version_id) {
                downloadUrl = `https://civitai.com/api/download/models/${model.civitai_version_id}`;
                versionId = model.civitai_version_id;
            } else if (model.hf_repo_id) {
                downloadUrl = `https://huggingface.co/${model.hf_repo_id}/resolve/main/${encodeURIComponent(model.file_name)}`;
            } else {
                return;
            }
            const lastSep = Math.max(model.file_path.lastIndexOf('/'), model.file_path.lastIndexOf('\\'));
            const saveDir = lastSep > 0 ? model.file_path.substring(0, lastSep) : model.file_path;
            redownloadBtn.disabled = true;
            const res = await api.downloadModel(downloadUrl, saveDir, model.file_name, versionId);
            if (res.success) {
                redownloadBtn.textContent = '✓';
            } else {
                redownloadBtn.disabled = false;
            }
        });
    }

    // 收藏按钮事件
    const favBtn = card.querySelector('.card-fav-btn');
    if (favBtn) {
        favBtn.addEventListener('click', async (e) => {
            e.stopPropagation(); // 阻止打开详情
            const newState = !favBtn.classList.contains('active');
            favBtn.classList.toggle('active', newState);
            model.favorite = newState;
            await api.toggleFavorite(model.file_path, newState);
        });
    }

    return card;
}

