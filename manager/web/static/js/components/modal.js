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
 * 模型详情弹窗 — 支持多图轮播 & 生成参数展示
 */
import { state } from '../state.js';
import * as api from '../api.js';
const { previewUrl } = api;
import { showToast, showOpError } from './toast.js';
import { showConfirm } from './dialog.js';
import { loadModels, updateGridItem } from './card-grid.js';
import { escapeHtml, escapeAttr, formatSize, formatNumber } from '../utils.js';
import {
    setGalleryImages, clearGallery, getCurrentMedia,
    navigateImage, setImage, updateImageMeta,
    copyImageToClipboard, copyToClipboard, openMediaZoom,
} from './modal-gallery.js';

// 图片操作按钮图标（SVG，替代原 emoji；复制成功时换成对勾再换回）
const IMG_ICONS = {
    zoom: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>',
    copyImg: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    link: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
    check: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
};

// 无预览占位图随主题切换（与卡片 .card-preview 的 placeholder-dark/light 保持一致）
function themePlaceholder() {
    return document.documentElement.dataset.theme === 'light'
        ? '/noctyra_static/images/placeholder-light.svg'
        : '/noctyra_static/images/placeholder-dark.svg';
}
import { buildSourceTabs, loadVersionsIntoPanel, renderStructureInto } from './modal-panels.js';

let overlay = null;
let currentModel = null;

export function initModal() {
    overlay = document.getElementById('modal-overlay');
    if (!overlay) return;

    overlay.addEventListener('click', e => {
        if (e.target === overlay) closeModal();
    });

    // 描述区域图片点击放大（委托在 overlay 上，只注册一次，避免多次打开详情导致累积多个 listener）
    overlay.addEventListener('click', (e) => {
        const img = e.target.closest('.desc-zoomable');
        if (img) {
            e.preventDefault();
            e.stopPropagation();
            openMediaZoom(img.src);
        }
    });

    document.addEventListener('keydown', e => {
        if (!overlay.classList.contains('show')) return;
        // 如果正在编辑笔记，不拦截方向键
        const isEditing = document.activeElement && document.activeElement.id === 'modal-notes';
        if (e.key === 'Escape') closeModal();
        if (isEditing) return;
        if (e.key === 'ArrowLeft') {
            if (e.altKey) navigateModel(-1);
            else navigateImage(-1);
        }
        if (e.key === 'ArrowRight') {
            if (e.altKey) navigateModel(1);
            else navigateImage(1);
        }
    });
}

let detailSession = 0;

export async function openDetailModal(identifier) {
    if (!overlay) return;
    const session = ++detailSession;

    // 详情秒开：先用内存里已加载的模型瞬间渲染（不等网络）；找不到再纯靠网络结果。
    // 后台预缓存/扫描占满磁盘时，详情数据请求会被拖慢，这一步让弹窗先即时弹出。
    const memModel = state.models.find(m =>
        m.file_path === identifier || (m.sha256 && m.sha256 === identifier));
    if (memModel) _renderDetail(memModel, [], [], session, true);

    const res = await api.fetchModelDetail(identifier);
    // 期间又开了别的详情（快速重开/上传后重开）→ 丢弃这次过期响应，避免覆盖当前弹窗
    if (session !== detailSession) return;
    if (!res.success || !res.model) {
        if (!memModel) showToast('加载模型详情失败', 'error');   // 内存已渲染则不打扰
        return;
    }
    // 完整数据回来 → 用完整版重渲染（补全描述 / 全部示例图 / 其他版本 / 相关模型）
    _renderDetail(res.model, res.local_versions || [], res.related_models || [], session);
}

// 渲染详情弹窗主体。会被调用两次：先用内存模型秒开(isPreview=true)，完整数据回来后再重渲染补全。
// isPreview 时跳过"异步子加载 + 可写控件绑定"，避免双重请求 + 防抖 timer 跨重渲染误覆盖笔记。
function _renderDetail(model, localVersions, relatedModels, session, isPreview = false) {
    if (session !== detailSession) return;   // 重渲染期间又切了别的详情 → 丢弃
    currentModel = model;
    const displayName = model.model_name || model.file_name;

    // 准备图片列表
    let images = model.preview_images || [];
    if (images.length === 0 && model.preview_url) {
        images = [{ url: model.preview_url }];
    }
    setGalleryImages(images);

    const fallbackUrl = themePlaceholder();
    const firstMedia = images.length > 0 ? images[0] : null;
    const mainImgUrl = firstMedia ? previewUrl(firstMedia.url) : fallbackUrl;
    const firstIsVideo = firstMedia?.type === 'video';

    // 轮播控件
    const hasMultipleImages = images.length > 1;
    const galleryNav = hasMultipleImages ? `
        <button class="gallery-prev" id="gallery-prev">‹</button>
        <button class="gallery-next" id="gallery-next">›</button>
        <div class="gallery-counter" id="gallery-counter">1 / ${images.length}</div>
    ` : '';

    // 缩略图条
    const thumbsHtml = hasMultipleImages ? `
        <div class="gallery-thumbs" id="gallery-thumbs">
            ${images.map((img, i) => {
                const isVid = img.type === 'video';
                const thumbUrl = escapeAttr(previewUrl(img.url));
                const cls = `gallery-thumb${i === 0 ? ' active' : ''}`;
                return isVid
                    ? `<video src="${thumbUrl}" class="${cls}" data-index="${i}" muted playsinline preload="metadata" disablepictureinpicture disableremoteplayback></video>`
                    : `<img src="${thumbUrl}" class="${cls}" data-index="${i}" loading="lazy" onerror="this.src='${themePlaceholder()}'">`;
            }).join('')}
        </div>
    ` : '';

    // 版本名
    const versionHtml = model.version_name
        ? `<div class="modal-version">${escapeHtml(model.version_name)}</div>` : '';

    // 统计信息
    let statsHtml = '';
    const statParts = [];
    if (model.thumbs_up > 0) statParts.push(`👍 ${formatNumber(model.thumbs_up)}`);
    if (model.downloads > 0) statParts.push(`⬇ ${formatNumber(model.downloads)}`);
    if (model.rating > 0) statParts.push(`⭐ ${model.rating.toFixed(1)} (${formatNumber(model.rating_count)})`);
    if (statParts.length > 0) {
        statsHtml = `<div class="info-row"><span class="info-label">统计</span><span class="info-value modal-stats">${statParts.join(' · ')}</span></div>`;
    }

    // 判断是否有前后模型可导航
    const currentIdx = state.models.findIndex(m =>
        (model.sha256 && m.sha256 === model.sha256) || m.file_path === model.file_path
    );
    const hasPrev = currentIdx > 0;
    const hasNext = currentIdx >= 0 && currentIdx < state.models.length - 1;

    const content = overlay.querySelector('.modal-content');
    content.innerHTML = `
        <div class="modal-header-bar">
            <div class="modal-actions">
                ${model.file_deleted && (model.civitai_version_id || model.hf_repo_id) ? `<button class="modal-action-btn modal-redownload-btn" id="modal-redownload-btn" title="重新下载"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button>` : ''}
                ${(model.file_name || '').toLowerCase().endsWith('.safetensors') ? `<button class="modal-action-btn" id="modal-structure-btn" title="文件结构（张量 / 元数据）"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg></button>` : ''}
                <button class="modal-action-btn${model.favorite ? ' active' : ''}" id="modal-fav-btn" title="收藏">&#9733;</button>
                <button class="modal-action-btn modal-delete-btn" id="modal-delete-btn" title="删除模型">&#128465;</button>
            </div>
            <button class="modal-close" id="modal-close-btn">&times;</button>
        </div>
        ${hasPrev || hasNext ? `
        <button class="modal-nav modal-nav-prev${hasPrev ? '' : ' disabled'}" id="modal-prev-btn" title="上一个">&#8249;</button>
        <button class="modal-nav modal-nav-next${hasNext ? '' : ' disabled'}" id="modal-next-btn" title="下一个">&#8250;</button>
        ` : ''}
        <div class="modal-body">
            <div class="modal-preview-section">
                <div class="modal-preview">
                    ${firstIsVideo
                        ? `<video src="${escapeAttr(mainImgUrl)}" id="modal-main-media" autoplay muted loop playsinline controls controlsList="nodownload noplaybackrate noremoteplayback" disablepictureinpicture disableremoteplayback></video>`
                        : `<img src="${escapeAttr(mainImgUrl)}" alt="${escapeAttr(displayName)}" id="modal-main-media"
                               onerror="this.src='${themePlaceholder()}'">`}
                    ${galleryNav}
                    ${firstMedia ? `
                    <div class="img-actions">
                        <button class="img-action-btn" id="img-zoom-btn" title="放大查看" aria-label="放大查看">${IMG_ICONS.zoom}</button>
                        <button class="img-action-btn" id="img-copy-img-btn" title="复制图片" aria-label="复制图片">${IMG_ICONS.copyImg}</button>
                        <button class="img-action-btn" id="img-copy-btn" title="复制图片链接" aria-label="复制图片链接">${IMG_ICONS.link}</button>
                    </div>` : ''}
                </div>
                ${thumbsHtml}
                <div class="modal-img-meta" id="modal-img-meta"></div>
            </div>
            <div class="modal-info">
                <h2 class="modal-title">${escapeHtml(displayName)}</h2>
                ${model.file_deleted ? `
                    <div class="modal-deleted-banner">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                        <span class="modal-deleted-text">文件已删除</span>
                    </div>
                ` : ''}
                ${versionHtml}

                ${model.model_name && model.model_name !== model.file_name ?
                    `<div class="info-row"><span class="info-label">文件名</span><span class="info-value">${escapeHtml(model.file_name)}</span></div>` : ''}
                <div class="info-row"><span class="info-label">大小</span><span class="info-value">${formatSize(model.file_size)}</span></div>
                <div class="info-row"><span class="info-label">基础模型</span><span class="info-value">${escapeHtml(model.base_model)}</span></div>
                <div class="info-row"><span class="info-label">SHA256</span><span class="info-value hash-value">${escapeHtml(model.sha256 || '未计算')}</span></div>
                <div class="info-row"><span class="info-label">路径</span><span class="info-value path-value" title="${escapeAttr(model.file_path)}">${escapeHtml(model.file_path)}</span></div>

                ${buildSourceTabs(model)}

                <div class="info-section">
                    <div class="info-label">自定义标签</div>
                    <div class="custom-tags-editor" id="modal-custom-tags">
                        ${(model.tags || []).map(t => `<span class="tag tag-editable">${escapeHtml(t)}<button class="tag-remove" data-tag="${escapeAttr(t)}">&times;</button></span>`).join('')}
                        <button class="tag-add-btn" id="tag-add-btn" title="添加标签">+</button>
                    </div>
                </div>

                <div class="info-section">
                    <div class="info-label">笔记</div>
                    <div class="notes-editor" contenteditable="true" id="modal-notes"
                         data-placeholder="添加笔记...">${escapeHtml(model.notes || '')}</div>
                </div>

                ${localVersions.length > 0 ? `
                    <div class="info-section">
                        <div class="info-label">本地其他版本 (${localVersions.length})</div>
                        <div class="local-versions-list">
                            ${localVersions.map(v => `
                                <div class="local-version-item" data-file-path="${escapeAttr(v.file_path)}">
                                    <div class="local-version-name">${escapeHtml(v.version_name || v.file_name)}</div>
                                    <div class="local-version-meta">
                                        ${v.base_model && v.base_model !== 'Unknown' ? `<span class="badge badge-sm">${escapeHtml(v.base_model)}</span>` : ''}
                                        <span>${formatSize(v.file_size)}</span>
                                    </div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                ` : ''}

                ${relatedModels.length > 0 ? `
                    <div class="info-section">
                        <div class="info-label">相关模型 (${relatedModels.length})</div>
                        <div class="related-models-list">
                            ${relatedModels.map(r => `
                                <div class="related-model-item" data-file-path="${escapeAttr(r.file_path)}">
                                    <img class="related-model-thumb" src="${escapeAttr(previewUrl(r.preview_url))}"
                                         onerror="this.src='${themePlaceholder()}'" loading="lazy">
                                    <div class="related-model-info">
                                        <div class="related-model-name">${escapeHtml(r.model_name || r.file_name)}</div>
                                        <div class="related-model-meta">
                                            <span class="badge badge-sm">${escapeHtml(r.civitai_model_type || r.model_type || '')}</span>
                                            <span>${formatSize(r.file_size)}</span>
                                        </div>
                                    </div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                ` : ''}

                ${(model.civitai_version_id || model.civitai_model_id) ? `
                    <div class="info-section recipe-usage-section" id="modal-recipe-usage" style="display:none">
                        <div class="info-label">被图库配方使用 <span id="recipe-usage-count"></span></div>
                        <div class="recipe-usage-list" id="recipe-usage-list"></div>
                    </div>
                ` : ''}

                <div class="bind-section">
                    <div class="info-label">${model.matched ? '重新绑定 / 换源' : '手动绑定'}</div>
                    <div class="bind-row">
                        <input type="text" id="bind-url" placeholder="粘贴 CivitAI 或 HuggingFace URL" class="bind-input">
                        <button id="bind-btn" class="btn btn-primary btn-sm">${model.matched ? '换源' : '绑定'}</button>
                    </div>
                </div>
            </div>
        </div>
    `;

    // 关闭按钮
    content.querySelector('#modal-close-btn').addEventListener('click', closeModal);

    // 轮播导航
    if (hasMultipleImages) {
        content.querySelector('#gallery-prev').addEventListener('click', () => navigateImage(-1));
        content.querySelector('#gallery-next').addEventListener('click', () => navigateImage(1));

        const thumbsContainer = content.querySelector('#gallery-thumbs');
        if (thumbsContainer) {
            thumbsContainer.addEventListener('click', e => {
                const thumb = e.target.closest('.gallery-thumb');
                if (thumb) {
                    const idx = parseInt(thumb.dataset.index, 10);
                    setImage(idx);
                }
            });
        }
    }

    // 放大按钮
    const zoomBtn = content.querySelector('#img-zoom-btn');
    if (zoomBtn) {
        zoomBtn.addEventListener('click', () => {
            const cur = getCurrentMedia();
            if (cur?.url) openMediaZoom(previewUrl(cur.url), cur.type === 'video');
        });
    }

    // 复制图片到剪贴板
    const copyImgImgBtn = content.querySelector('#img-copy-img-btn');
    if (copyImgImgBtn) {
        copyImgImgBtn.addEventListener('click', async () => {
            const imgEl = document.getElementById('modal-main-media');
            if (!imgEl) return;
            try {
                const blob = await copyImageToClipboard(imgEl);
                if (blob) {
                    copyImgImgBtn.innerHTML = IMG_ICONS.check;
                    copyImgImgBtn.classList.add('copied');
                    setTimeout(() => { copyImgImgBtn.innerHTML = IMG_ICONS.copyImg; copyImgImgBtn.classList.remove('copied'); }, 1200);
                }
            } catch {
                showToast('复制图片失败', 'error');
            }
        });
    }

    // 图片链接复制按钮
    const copyImgBtn = content.querySelector('#img-copy-btn');
    if (copyImgBtn) {
        copyImgBtn.addEventListener('click', async () => {
            const imgUrl = getCurrentMedia()?.url;
            if (imgUrl) {
                await copyToClipboard(imgUrl);
                copyImgBtn.innerHTML = IMG_ICONS.check;
                copyImgBtn.classList.add('copied');
                setTimeout(() => { copyImgBtn.innerHTML = IMG_ICONS.link; copyImgBtn.classList.remove('copied'); }, 1200);
            }
        });
    }

    // 显示第一张图的元数据
    updateImageMeta();

    // 绑定按钮
    const bindBtn = content.querySelector('#bind-btn');
    if (bindBtn) {
        bindBtn.addEventListener('click', async () => {
            const url = content.querySelector('#bind-url').value.trim();
            if (!url) return;
            // 已匹配的"换源"会覆盖当前匹配信息，二次确认（锁定的自定义字段不受影响，仍保留）
            if (model.matched && !await showConfirm({ title: '换源', okText: '覆盖', message: '用此链接覆盖当前匹配信息？\n（你锁定的自定义字段会保留）' })) return;
            const verb = model.matched ? '换源' : '绑定';
            bindBtn.disabled = true;
            bindBtn.textContent = verb + '中...';
            const res = await api.bindSource(model.sha256, url);
            if (res.success) {
                showToast(verb + '成功', 'success');
                closeModal();
                loadModels();
            } else {
                showOpError(res, verb + '失败');
                bindBtn.disabled = false;
                bindBtn.textContent = verb;
            }
        });
    }

    // 收藏按钮
    const favBtn = content.querySelector('#modal-fav-btn');
    if (favBtn) {
        favBtn.addEventListener('click', async () => {
            const newState = !favBtn.classList.contains('active');
            favBtn.classList.toggle('active', newState);
            await api.toggleFavorite(model.file_path, newState);
            // 同步网格数据 + 可视卡片（虚拟滚动下视口外的卡靠数据，滚回来才不会回退收藏态）
            model.favorite = newState;
            updateGridItem(model.file_path, { favorite: newState });
        });
    }

    // 文件结构按钮（safetensors）：打开独立浮层
    const structBtn = content.querySelector('#modal-structure-btn');
    if (structBtn) {
        structBtn.addEventListener('click', () => openStructureOverlay(model));
    }

    // 删除按钮
    const deleteBtn = content.querySelector('#modal-delete-btn');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', () => {
            showDeleteConfirm(model);
        });
    }

    // 重新下载按钮（在 header 操作区）
    const redownloadBtn = content.querySelector('#modal-redownload-btn');
    if (redownloadBtn) {
        redownloadBtn.addEventListener('click', async () => {
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
            redownloadBtn.classList.add('downloading');
            const res = await api.downloadModel(downloadUrl, saveDir, model.file_name, versionId);
            if (res.success) {
                showToast('下载任务已启动', 'success');
            } else {
                showToast('下载失败: ' + (res.error || ''), 'error');
                redownloadBtn.disabled = false;
                redownloadBtn.classList.remove('downloading');
            }
        });
    }

    // 自定义标签编辑器
    const tagsContainer = content.querySelector('#modal-custom-tags');
    if (tagsContainer) {
        tagsContainer.addEventListener('click', async (e) => {
            const removeBtn = e.target.closest('.tag-remove');
            if (removeBtn) {
                const tag = removeBtn.dataset.tag;
                const res = await api.removeTag(model.file_path, tag);
                if (res.success) {
                    removeBtn.parentElement.remove();
                    model.tags = (model.tags || []).filter(t => t !== tag);
                }
                return;
            }
            if (e.target.closest('.tag-editable') && !e.target.closest('.tag-remove')) {
                const tag = e.target.closest('.tag-editable').textContent.replace('×', '').trim();
                if (tag) {
                    overlay.classList.remove('show');
                    // 标签筛选已改胶囊：通过事件让 filters.js 同步胶囊 + 已选条并重载列表
                    window.dispatchEvent(new CustomEvent('noctyra-set-filter', { detail: { cat: 'tag', value: tag, reload: true } }));
                }
            }
        });

        const addBtn = tagsContainer.querySelector('#tag-add-btn');
        if (addBtn) {
            addBtn.addEventListener('click', () => {
                if (tagsContainer.querySelector('.tag-input-inline')) return;
                const input = document.createElement('input');
                input.type = 'text';
                input.className = 'tag-input-inline';
                input.placeholder = '输入标签名...';
                tagsContainer.insertBefore(input, addBtn);
                input.focus();

                const commit = async () => {
                    const val = input.value.trim();
                    if (val) {
                        const res = await api.addTags(model.file_path, [val]);
                        if (res.success) {
                            const span = document.createElement('span');
                            span.className = 'tag tag-editable';
                            span.innerHTML = `${escapeHtml(val)}<button class="tag-remove" data-tag="${escapeAttr(val)}">&times;</button>`;
                            tagsContainer.insertBefore(span, addBtn);
                            if (!model.tags) model.tags = [];
                            model.tags.push(val);
                        }
                    }
                    input.remove();
                };
                input.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') commit();
                    if (e.key === 'Escape') input.remove();
                });
                input.addEventListener('blur', commit);
            });
        }
    }

    // 自定义 Tab 表单：保存按钮 + 预览图上传
    const customSaveBtn = content.querySelector('#custom-save-btn');
    if (customSaveBtn) {
        const hintEl = content.querySelector('.custom-save-hint');
        customSaveBtn.addEventListener('click', async () => {
            const panel = content.querySelector('.source-panel[data-source="custom"]');
            if (!panel) return;
            const fields = {};
            panel.querySelectorAll('[data-field]').forEach(el => {
                fields[el.dataset.field] = el.value;
            });
            customSaveBtn.disabled = true;
            if (hintEl) hintEl.textContent = '保存中...';
            const identifier = model.sha256 || model.file_path;
            const res = await api.updateCustomInfo(identifier, fields);
            customSaveBtn.disabled = false;
            if (res.success) {
                if (hintEl) hintEl.textContent = '已保存';
                setTimeout(() => { if (hintEl) hintEl.textContent = ''; }, 2000);
                // 回写本地 model：填了用新值，清空的保留现值（与后端"清空=解锁保留现值"一致）
                const _t = (s) => (s || '').trim();
                Object.assign(model, {
                    model_name: _t(fields.model_name) || model.model_name,
                    base_model: _t(fields.base_model) || model.base_model,
                    creator: _t(fields.creator) || model.creator,
                    version_name: _t(fields.version_name) || model.version_name,
                    model_description: _t(fields.model_description) || model.model_description,
                    trained_words: _t(fields.trained_words)
                        ? fields.trained_words.split(',').map(s => s.trim()).filter(Boolean)
                        : model.trained_words,
                    user_model_type: _t(fields.user_model_type),
                });
                // 触发整理预览 / 卡片网格刷新
                window.dispatchEvent(new Event('noctyra-custom-updated'));
                loadModels();
            } else {
                showToast('保存失败: ' + (res.error || '未知错误'), 'error');
                if (hintEl) hintEl.textContent = '';
            }
        });
    }

    const uploadBtn = content.querySelector('#custom-preview-upload-btn');
    const fileInput = content.querySelector('#custom-preview-file');
    if (uploadBtn && fileInput) {
        uploadBtn.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', async () => {
            const file = fileInput.files && fileInput.files[0];
            if (!file) return;
            uploadBtn.disabled = true;
            uploadBtn.textContent = '上传中...';
            const identifier = model.sha256 || model.file_path;
            const res = await api.uploadPreview(identifier, file);
            uploadBtn.disabled = false;
            uploadBtn.textContent = '上传本地图片';
            fileInput.value = '';
            if (res.success) {
                showToast('预览图已更新', 'success');
                // 重新打开 modal 以刷新预览
                openDetailModal(identifier);
                window.dispatchEvent(new Event('noctyra-custom-updated'));
                loadModels();
            } else {
                showToast('上传失败: ' + (res.error || '未知错误'), 'error');
            }
        });
    }

    const clearPreviewBtn = content.querySelector('#custom-preview-clear-btn');
    if (clearPreviewBtn) {
        clearPreviewBtn.addEventListener('click', async () => {
            if (!await showConfirm({ title: '清除预览图', okText: '清除', message: '确定清除当前预览图？' })) return;
            const identifier = model.sha256 || model.file_path;
            const res = await api.updateCustomInfo(identifier, { preview_url: '' });
            if (res.success) {
                showToast('预览图已清除', 'success');
                openDetailModal(identifier);
                loadModels();
            }
        });
    }

    // 笔记编辑器（首渲染秒开时不绑：防抖 timer 会在第二次重渲染替换 DOM 后仍触发、用旧内容覆盖笔记）
    const notesEl = content.querySelector('#modal-notes');
    if (notesEl && !isPreview) {
        let saveTimer = null;
        notesEl.addEventListener('input', () => {
            clearTimeout(saveTimer);
            saveTimer = setTimeout(() => {
                api.updateNotes(model.file_path, notesEl.textContent.trim());
            }, 800);
        });
        notesEl.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') { notesEl.blur(); e.stopPropagation(); }
        });
    }

    // 本地其他版本点击跳转
    content.querySelectorAll('.local-version-item, .related-model-item').forEach(item => {
        item.addEventListener('click', () => {
            const fp = item.dataset.filePath;
            if (fp) openDetailModal(fp);
        });
    });

    // 来源 tab 切换
    const sourceTabs = content.querySelectorAll('.source-tab');
    if (sourceTabs.length > 0) {
        sourceTabs.forEach(tab => {
            tab.addEventListener('click', () => {
                sourceTabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                content.querySelectorAll('.source-panel').forEach(p => p.classList.remove('active'));
                const panel = content.querySelector(`.source-panel[data-source="${tab.dataset.source}"]`);
                if (panel) panel.classList.add('active');
                // 版本 tab 懒加载（loading 互斥 + loaded 缓存，避免快速来回切出现多次 fetch）
                if (tab.dataset.source === 'versions' && panel
                    && !panel.dataset.loaded && !panel.dataset.loading) {
                    loadVersionsIntoPanel(panel);
                }
            });
        });
    }

    // 前后导航
    const prevBtn = content.querySelector('#modal-prev-btn');
    const nextBtn = content.querySelector('#modal-next-btn');
    if (prevBtn) {
        prevBtn.addEventListener('click', () => navigateModel(-1));
    }
    if (nextBtn) {
        nextBtn.addEventListener('click', () => navigateModel(1));
    }

    // 反查：被哪些图库配方用过（异步，命中才显示）。首渲染(秒开)跳过，避免双重请求，完整渲染再发。
    if (!isPreview) loadRecipeUsage(model, content, session);

    overlay.classList.add('show');
}

/** 反查模型详情页：列出用过此模型的图库配方缩略图，点击放大原图。命中才展开区块。 */
async function loadRecipeUsage(model, content, session) {
    const section = content.querySelector('#modal-recipe-usage');
    if (!section) return;
    const params = new URLSearchParams();
    if (model.civitai_version_id) params.set('version_id', model.civitai_version_id);
    if (model.civitai_model_id) params.set('model_id', model.civitai_model_id);
    let recipes = [];
    try {
        const res = await fetch(`/api/noctyra/model/recipes?${params.toString()}`);
        const data = await res.json();
        recipes = (data && data.success && Array.isArray(data.recipes)) ? data.recipes : [];
    } catch (e) {
        return; // 静默失败，不打扰详情页
    }
    if (!recipes.length) return;
    // 弹窗可能在 await 期间已切换到别的模型/关闭：以 detailSession 为准（比仅 isConnected 更稳），
    // 再叠加 section 仍在文档内的兜底校验
    if (session !== detailSession || !section.isConnected) return;

    const countEl = section.querySelector('#recipe-usage-count');
    if (countEl) countEl.textContent = `(${recipes.length})`;
    const list = section.querySelector('#recipe-usage-list');
    list.innerHTML = recipes.map(r => {
        const isVid = r.media_type === 'video';
        const thumb = escapeAttr(`/api/noctyra/workflow/image/${r.id}?size=card`);
        const title = escapeAttr(r.custom_name || r.file_name || `#${r.id}`);
        return `<div class="recipe-usage-item" data-id="${r.id}" data-video="${isVid ? 1 : 0}" title="${title}">
            ${isVid
                ? `<video src="${thumb}" muted playsinline preload="metadata" disablepictureinpicture disableremoteplayback></video>`
                : `<img src="${thumb}" loading="lazy" onerror="this.style.visibility='hidden'">`}
        </div>`;
    }).join('');
    list.addEventListener('click', e => {
        const item = e.target.closest('.recipe-usage-item');
        if (!item) return;
        const id = item.dataset.id;
        openMediaZoom(`/api/noctyra/workflow/image/${id}`, item.dataset.video === '1');
    });
    section.style.display = '';
}

function navigateModel(delta) {
    if (!currentModel) return;
    const idx = state.models.findIndex(m =>
        (currentModel.sha256 && m.sha256 === currentModel.sha256) || m.file_path === currentModel.file_path
    );
    const newIdx = idx + delta;
    if (newIdx < 0 || newIdx >= state.models.length) return;
    const next = state.models[newIdx];
    const identifier = next.sha256 || next.file_path;
    if (identifier) openDetailModal(identifier);
}

// 文件结构浮层：宽敞展示 safetensors 元数据 + 张量树（独立 overlay，不挤在 tab 里）
async function openStructureOverlay(model) {
    const overlay = document.createElement('div');
    overlay.className = 'st-overlay';
    overlay.innerHTML = `
        <div class="st-modal">
            <div class="st-modal-head">
                <span class="st-modal-title" title="${escapeAttr(model.file_name || '')}">${escapeHtml(model.model_name || model.file_name || '文件结构')}</span>
                <button class="st-modal-close" title="关闭">&times;</button>
            </div>
            <div class="st-modal-body"><div class="st-loading">读取文件结构…</div></div>
        </div>`;
    document.body.appendChild(overlay);

    const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    overlay.querySelector('.st-modal-close').addEventListener('click', close);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    document.addEventListener('keydown', onKey);

    const body = overlay.querySelector('.st-modal-body');
    const id = model.sha256 || model.file_path || '';
    try {
        const res = await fetch('/api/noctyra/model-safetensors?id=' + encodeURIComponent(id)).then(r => r.json());
        renderStructureInto(body, res);
    } catch (e) {
        body.innerHTML = `<div class="st-error">读取失败：${escapeHtml(e.message || '网络错误')}</div>`;
    }
}

function showDeleteConfirm(model) {
    const name = model.model_name || model.file_name;
    const confirmEl = document.createElement('div');
    confirmEl.className = 'delete-confirm-overlay';
    confirmEl.innerHTML = `
        <div class="delete-confirm">
            <h3>确认删除</h3>
            <p>确定要删除模型 <strong>${escapeHtml(name)}</strong> 吗？</p>
            <label class="delete-file-option">
                <input type="checkbox" id="delete-file-check"> 同时删除磁盘文件
            </label>
            <div class="delete-confirm-actions">
                <button class="btn" id="delete-cancel-btn">取消</button>
                <button class="btn btn-danger" id="delete-confirm-btn">删除</button>
            </div>
        </div>
    `;
    document.body.appendChild(confirmEl);

    confirmEl.querySelector('#delete-cancel-btn').addEventListener('click', () => {
        confirmEl.remove();
    });
    confirmEl.querySelector('#delete-confirm-btn').addEventListener('click', async () => {
        const deleteFile = confirmEl.querySelector('#delete-file-check').checked;
        confirmEl.remove();
        const res = await api.deleteModel(model.file_path, deleteFile);
        if (res.success) {
            showToast(`已删除: ${name}`, 'success');
            closeModal();
            loadModels();
            window.dispatchEvent(new Event('noctyra-refresh-tabs'));
            window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
        } else {
            showOpError(res, '删除失败');
        }
    });
    confirmEl.addEventListener('click', e => {
        if (e.target === confirmEl) confirmEl.remove();
    });
}

function closeModal() {
    if (overlay) overlay.classList.remove('show');
    clearGallery();
    currentModel = null;
}


