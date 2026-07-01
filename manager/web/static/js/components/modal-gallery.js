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
 * 模型详情弹窗 — 图片/视频轮播、元数据显示、媒体放大、剪贴板复制。
 *
 * 状态封装在模块内部，通过 setGalleryImages/clearGallery 管理，
 * 通过 getCurrentMedia/getGalleryCount 读取（供 modal.js 的按钮处理器使用）。
 */
import * as api from '../api.js';
import { escapeHtml, escapeAttr } from '../utils.js';

const { previewUrl } = api;

// 无预览占位图随主题切换（与 modal.js 的 themePlaceholder 一致，浅色主题不再回退到深色图）
function themePlaceholder() {
    return document.documentElement.dataset.theme === 'light'
        ? '/noctyra_static/images/placeholder-light.svg'
        : '/noctyra_static/images/placeholder-dark.svg';
}

let currentImages = [];
let currentImageIndex = 0;

export function setGalleryImages(images) {
    currentImages = Array.isArray(images) ? images : [];
    currentImageIndex = 0;
}

export function clearGallery() {
    currentImages = [];
    currentImageIndex = 0;
}

export function getGalleryImages() {
    return currentImages;
}

export function getGalleryCount() {
    return currentImages.length;
}

export function getGalleryIndex() {
    return currentImageIndex;
}

export function getCurrentMedia() {
    return currentImages[currentImageIndex] || null;
}

export function navigateImage(delta) {
    if (currentImages.length <= 1) return;
    const newIndex = (currentImageIndex + delta + currentImages.length) % currentImages.length;
    setImage(newIndex);
}

export function setImage(index) {
    if (index < 0 || index >= currentImages.length) return;
    currentImageIndex = index;

    const item = currentImages[index];
    const isVid = item.type === 'video';
    const src = previewUrl(item.url);

    const container = document.querySelector('.modal-preview');
    const old = document.getElementById('modal-main-media');
    if (container && old) {
        let el;
        if (isVid) {
            el = document.createElement('video');
            el.src = src;
            el.autoplay = true;
            el.muted = true;
            el.loop = true;
            el.playsInline = true;
            el.controls = true;
            el.disablePictureInPicture = true;
            el.disableRemotePlayback = true;
            el.setAttribute('controlsList', 'nodownload noplaybackrate noremoteplayback');
        } else {
            el = document.createElement('img');
            el.src = src;
            el.onerror = () => { el.src = themePlaceholder(); };
        }
        el.id = 'modal-main-media';
        old.replaceWith(el);
    }

    const counter = document.getElementById('gallery-counter');
    if (counter) {
        counter.textContent = `${index + 1} / ${currentImages.length}`;
    }

    const thumbs = document.querySelectorAll('.gallery-thumb');
    thumbs.forEach((t, i) => t.classList.toggle('active', i === index));

    const activeThumb = thumbs[index];
    if (activeThumb) {
        activeThumb.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    }

    updateImageMeta();
}

export function updateImageMeta() {
    const metaEl = document.getElementById('modal-img-meta');
    if (!metaEl) return;

    const img = currentImages[currentImageIndex];
    if (!img || !img.prompt) {
        metaEl.innerHTML = '';
        metaEl.style.display = 'none';
        return;
    }

    const rows = [];

    if (img.prompt) rows.push(metaRow('Prompt', img.prompt));
    if (img.negative_prompt) rows.push(metaRow('Negative', img.negative_prompt));
    if (img.sampler) rows.push(metaRow('Sampler', img.sampler));
    if (img.steps) rows.push(metaRow('Steps', String(img.steps)));
    if (img.cfg_scale) rows.push(metaRow('CFG', String(img.cfg_scale)));
    if (img.seed) rows.push(metaRow('Seed', String(img.seed)));
    if (img.model) rows.push(metaRow('Model', img.model));
    if (img.width && img.height) rows.push(metaRow('Size', `${img.width}×${img.height}`));

    if (rows.length > 0) {
        metaEl.innerHTML = rows.join('');
        metaEl.style.display = 'block';
        bindMetaCopyButtons(metaEl);
    } else {
        metaEl.innerHTML = '';
        metaEl.style.display = 'none';
    }
}

function metaRow(label, text) {
    return `<div class="meta-row">
        <span class="meta-label">${label}</span>
        <span class="meta-text">${escapeHtml(text)}</span>
        <button class="meta-copy-btn" data-text="${escapeAttr(text)}">复制</button>
    </div>`;
}

function bindMetaCopyButtons(container) {
    container.querySelectorAll('.meta-copy-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            await copyToClipboard(btn.dataset.text);
            btn.textContent = '✓';
            btn.classList.add('copied');
            setTimeout(() => { btn.textContent = '复制'; btn.classList.remove('copied'); }, 1200);
        });
    });
}

export async function copyImageToClipboard(imgEl) {
    const canvas = document.createElement('canvas');
    canvas.width = imgEl.naturalWidth;
    canvas.height = imgEl.naturalHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(imgEl, 0, 0);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (blob) {
        await navigator.clipboard.write([
            new ClipboardItem({ 'image/png': blob })
        ]);
    }
    return blob;
}

export async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
    } catch {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;opacity:0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
    }
}

export function openMediaZoom(mediaUrl, isVideo = false) {
    const zoomOverlay = document.createElement('div');
    zoomOverlay.className = 'img-zoom-overlay';
    zoomOverlay.innerHTML = isVideo
        ? `<video src="${escapeAttr(mediaUrl)}" autoplay muted loop playsinline controls controlsList="nodownload noplaybackrate noremoteplayback" disablepictureinpicture disableremoteplayback></video>
           <button class="img-zoom-close">&times;</button>`
        : `<img src="${escapeAttr(mediaUrl)}">
           <button class="img-zoom-close">&times;</button>`;
    document.body.appendChild(zoomOverlay);

    function close() { document.removeEventListener('keydown', onKey, true); zoomOverlay.remove(); }
    function onKey(e) { if (e.key === 'Escape') { e.stopImmediatePropagation(); close(); } }
    zoomOverlay.addEventListener('click', (e) => { e.stopPropagation(); close(); });
    document.addEventListener('keydown', onKey, true);
}
