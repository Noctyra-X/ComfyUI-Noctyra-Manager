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
 * 工作流图库的"详情弹窗"+ 子弹窗（图片放大、同配方、下载按钮绑定）
 * 从 workflows/app.js 拆出，主要逻辑 openDetail 约 330 行。
 */

import { escapeHtml, escapeAttr, resolveSourceUrl, isSafeUrl } from '../utils.js';
import { API_BASE } from './state.js';
import {
    apiFetch, apiSave, apiCheckResources, apiUpdateInfo, apiDownloadModel,
} from './api.js';
import {
    renderCivitaiParamsHtml, renderComfyParamsHtml, renderA1111EmbedHtml,
    getWorkflowJsonText, renderResourcesHtml, bindCopyButtons,
} from './renderers.js';


// 详情弹窗 session token：每次 openDetail 递增，异步回调前校验未过期，防止
// 用户快速切换不同图片时，上一张的 check-resources 回调往已替换的 DOM 写数据
let _detailSession = 0;

// 用于通知 app.js 重新加载图库（如保存信息后）。避免 detail.js 直接引用 app.js 造成循环依赖
function _dispatchGalleryReload() {
    window.dispatchEvent(new Event('noctyra-wf-reload-gallery'));
}

// 用于通知 app.js 显示 toast（同样避免循环依赖）——实际上 app.js 挂上全局 window._wfShowToast
function _toast(msg, type = 'info') {
    if (typeof window._wfShowToast === 'function') {
        window._wfShowToast(msg, type);
    } else {
        console.log(`[Noctyra-WF] ${type}: ${msg}`);
    }
}

async function _copyText(text) {
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


// 详情加载中的骨架屏：进入即渲染，让弹窗立刻出现（不再"点了没反应"）
function _skeletonHtml() {
    const bar = (w, h) => `<div class="skeleton-shimmer" style="width:${w};height:${h};border-radius:6px;margin-bottom:10px"></div>`;
    return `
        <div class="wf-preview-panel">
            <div class="wf-preview-container">
                <div class="skeleton-shimmer" style="width:100%;height:auto;aspect-ratio:3/4;border-radius:12px"></div>
            </div>
        </div>
        <div class="wf-params-panel">
            ${bar('55%', '20px')}
            ${bar('90%', '14px')}
            ${bar('80%', '14px')}
            ${bar('70%', '14px')}
            ${bar('85%', '14px')}
        </div>`;
}

// 详情加载失败/空态：给出可见反馈，避免静默
function _detailErrorHtml(msg) {
    return `
        <div class="wf-params-empty" style="margin:auto">
            <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            <p>无法加载该项详情</p>
            <span>${escapeHtml(msg || '数据不存在或已被移除')}</span>
        </div>`;
}

export function openDetail(imageId) {
    const overlay = document.getElementById('detail-overlay');
    const body = document.getElementById('detail-body');

    // 新会话 token，作废之前任何还没返回的异步回调
    const mySession = ++_detailSession;
    const isStale = () => mySession !== _detailSession;

    // 进入即先显示弹窗 + 骨架：无论后续加载成功/失败/慢，点击都立刻有反馈
    if (body) body.innerHTML = _skeletonHtml();
    if (overlay) overlay.classList.add('show');

    fetch(`${API_BASE}/gallery/${imageId}`)
        .then(r => r.json())
        .then(res => {
            if (isStale()) return;
            if (!res.success || !res.image) {
                if (body) body.innerHTML = _detailErrorHtml(res && res.error);
                return;
            }
            const img = res.image;
            const meta = img.meta || {};
            const resources = img.resources || [];

            const imgSrc = `/api/noctyra/workflow/image/${img.id}`;
            const civitaiHtml = renderCivitaiParamsHtml(meta);
            const comfyHtml = renderComfyParamsHtml(img);
            const a1111Html = renderA1111EmbedHtml(img);
            const wfJsonText = comfyHtml ? getWorkflowJsonText(img) : '';

            // 参数区分两个 tab：CivitAI 数据（接口）/ 图片文件内嵌（ComfyUI 工作流 + A1111）
            const hasCivitai = !!civitaiHtml;
            const hasEmbed = !!(comfyHtml || a1111Html);
            // 有内嵌 ComfyUI 工作流（editor 格式）时给个"发送到画布"按钮
            const sendCanvasBtn = wfJsonText
                ? `<button class="wf-btn-sm wf-send-canvas-btn" type="button" title="把内嵌的 ComfyUI 工作流加载到 ComfyUI 画布（需 ComfyUI 标签页打开）">发送到画布</button>`
                : '';

            // Workflow 标识
            let workflowBadge = '';
            if (img.has_workflow) {
                workflowBadge = '<span class="wf-gallery-badge" style="margin-left:8px">含 ComfyUI Workflow</span>';
            }

            const tags = img.tags || [];
            const customName = img.custom_name || '';
            const notes = img.notes || '';

            // NSFW 感知：图库图片若 nsfw_level >= 4（R 及以上），跳 civitai.red
            const imgIsNsfw = (img.nsfw_level || 0) >= 4;
            const civitaiHref = resolveSourceUrl(img.source_url, imgIsNsfw);

            const refetchBtn = img.source_url
                ? `<button class="wf-btn wf-btn-sm wf-refetch-btn" data-id="${img.id}" data-url="${escapeAttr(img.source_url)}">重新获取</button>`
                : '';

            const isVideo = img.media_type === 'video';
            const mediaTag = isVideo
                ? `<video src="${imgSrc}" class="wf-preview-img" id="detail-preview-img"
                          controls autoplay muted loop playsinline controlsList="nodownload noplaybackrate noremoteplayback" disablepictureinpicture disableremoteplayback></video>`
                : `<img src="${imgSrc}" class="wf-preview-img" id="detail-preview-img">`;

            // 格式 + 内嵌元数据分类：一眼看出文件能装什么、能不能拖到画布出节点
            const fmtLabel = ((img.file_name || '').split('.').pop() || (isVideo ? 'video' : 'img')).toUpperCase();
            const metaTags = [];
            if (wfJsonText) metaTags.push('<span class="wf-meta-tag wf-meta-comfy" title="含 ComfyUI 工作流，可拖/发送到画布出节点">ComfyUI 工作流 · 可拖画布</span>');
            if (a1111Html) metaTags.push('<span class="wf-meta-tag wf-meta-a1111">A1111 参数</span>');
            if (hasCivitai) metaTags.push('<span class="wf-meta-tag wf-meta-civitai">CivitAI 数据</span>');
            const classifyHtml = `<div class="wf-classify"><span class="wf-fmt-label">${escapeHtml(fmtLabel)}</span>${metaTags.join('') || '<span class="wf-meta-tag wf-meta-none">无生成元数据</span>'}</div>`;

            body.innerHTML = `
                <div class="wf-preview-panel">
                    <div class="wf-preview-container">
                        ${mediaTag}
                    </div>
                    ${classifyHtml}
                    <div class="wf-image-meta">
                        ${img.width && img.height ? `${img.width} × ${img.height}` : ''}
                        ${civitaiHref && isSafeUrl(civitaiHref) ? `· <a href="${escapeAttr(civitaiHref)}" target="_blank" rel="noopener">CivitAI</a>` : ''}
                        ${img.fingerprint ? `
                            <span class="wf-fingerprint" title="配方指纹（SHA256 of 归一化 LoRA 组合）：\n${escapeAttr(img.fingerprint)}\n\n同样指纹 = 同样的 base_model + LoRA 组合，可跨图查重。"
                                  data-fp="${escapeAttr(img.fingerprint)}">
                                · 指纹 <code>${escapeHtml(img.fingerprint.slice(0, 8))}</code>
                            </span>
                            <button class="wf-btn-sm wf-same-recipe-btn"
                                    data-fp="${escapeAttr(img.fingerprint)}"
                                    data-exclude="${img.id}"
                                    title="查找所有使用同样 LoRA 组合的配方">同配方</button>` : ''}
                    </div>
                    <div class="wf-detail-actions">
                        ${refetchBtn}
                    </div>
                    <div class="wf-detail-edit" data-id="${img.id}">
                        <div class="wf-edit-row">
                            <label>名称</label>
                            <input type="text" class="wf-input wf-edit-name" value="${escapeAttr(customName)}" placeholder="自定义名称...">
                        </div>
                        <div class="wf-edit-row">
                            <label>标签</label>
                            <input type="text" class="wf-input wf-edit-tags" value="${escapeAttr(tags.join(', '))}" placeholder="多个标签用逗号分隔，例如：flux, portrait">
                        </div>
                        <div class="wf-edit-row">
                            <label>备注</label>
                            <textarea class="wf-input wf-edit-notes" rows="2" placeholder="备注...">${escapeHtml(notes)}</textarea>
                        </div>
                        <div class="wf-edit-flags">
                            <label class="wf-edit-check wf-check-fav">
                                <input type="checkbox" class="wf-edit-favorite" ${img.favorite ? 'checked' : ''}>
                                <svg class="wf-check-ic" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
                                <span>收藏</span>
                            </label>
                            <label class="wf-edit-check wf-check-nsfw">
                                <input type="checkbox" class="wf-edit-user-nsfw" ${img.user_nsfw ? 'checked' : ''}>
                                <svg class="wf-check-ic" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>
                                <span>标记 NSFW（强制模糊）</span>
                            </label>
                        </div>
                        <button class="wf-btn wf-btn-sm wf-save-info-btn">保存信息</button>
                    </div>
                </div>
                <div class="wf-params-panel">
                    ${(hasCivitai || hasEmbed) ? `
                    <div class="wf-tabs" role="tablist">
                        ${hasCivitai ? `<button class="wf-tab active" data-tab="civitai" role="tab">CivitAI 数据</button>` : ''}
                        ${hasEmbed ? `<button class="wf-tab${hasCivitai ? '' : ' active'}" data-tab="embed" role="tab">图片文件内嵌</button>` : ''}
                    </div>
                    ${hasCivitai ? `
                    <div class="wf-tab-pane active" data-pane="civitai">
                        <div class="wf-params-content">${civitaiHtml}</div>
                    </div>` : ''}
                    ${hasEmbed ? `
                    <div class="wf-tab-pane${hasCivitai ? '' : ' active'}" data-pane="embed">
                        ${comfyHtml ? `
                        <section class="wf-section">
                            <header class="wf-section-header">
                                <div class="wf-section-header-title">ComfyUI 工作流</div>
                                <div class="wf-section-header-actions">
                                    ${sendCanvasBtn}
                                    ${wfJsonText ? `<button class="wf-btn-sm wf-toggle-wf-json" type="button">查看 JSON</button>` : ''}
                                </div>
                            </header>
                            <div class="wf-section-body">
                                <div class="wf-params-content">${comfyHtml}</div>
                                ${wfJsonText ? `
                                <div class="wf-wf-json-box" style="display:none;">
                                    <div class="wf-wf-json-header">
                                        <span>workflow.json</span>
                                        <button class="wf-copy-wf-json-btn">复制</button>
                                    </div>
                                    <pre class="wf-wf-json-pre"></pre>
                                </div>
                                ` : ''}
                            </div>
                        </section>` : ''}
                        ${a1111Html}
                    </div>` : ''}
                    ` : `<div class="wf-params-empty">
                        <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><line x1="9" y1="14" x2="15" y2="14"/><line x1="9" y1="18" x2="13" y2="18"/></svg>
                        <p>该${isVideo ? '视频' : '图片'}没有生成参数</p>
                        <span>${isVideo
                            ? '没有 CivitAI 数据；视频内嵌仅 ComfyUI 动图 webp 能解析，mp4/webm 读不到工作流'
                            : '既没有 CivitAI 数据，也没从文件里解析出 A1111 / ComfyUI 信息'}</span>
                    </div>`}
                    ${resources.length > 0 ? `
                    <section class="wf-section">
                        <header class="wf-section-header">
                            <div class="wf-section-header-title">使用的资源</div>
                            <div class="wf-section-header-actions">
                                <button class="wf-btn-sm wf-copy-lora-btn" data-recipe-id="${img.id}" title="把本配方里本地命中的 LoRA 拼成 <lora:名字:权重> 复制到剪贴板">
                                    复制 LoRA 语法
                                </button>
                                <button class="wf-btn-sm wf-fetch-missing-btn" data-recipe-id="${img.id}" title="把本配方里所有本地缺失的 CivitAI 资源批量加入下载队列">
                                    补全缺失资源
                                </button>
                            </div>
                        </header>
                        <div class="wf-section-body">
                            <div class="wf-resources-content" id="detail-resources">${renderResourcesHtml(resources, null)}</div>
                        </div>
                    </section>
                    ` : ''}
                </div>
            `;
            bindCopyButtons(body);

            // 参数区 tab 切换（CivitAI 数据 / 图片文件内嵌）
            body.querySelectorAll('.wf-tab').forEach(tab => {
                tab.addEventListener('click', () => {
                    const which = tab.dataset.tab;
                    body.querySelectorAll('.wf-tab').forEach(t => t.classList.toggle('active', t === tab));
                    body.querySelectorAll('.wf-tab-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === which));
                });
            });

            // 发送到画布：把内嵌的 ComfyUI 工作流广播给同源的 ComfyUI 画布（画布扩展收后 loadGraphData）
            const sendCanvasBtnEl = body.querySelector('.wf-send-canvas-btn');
            if (sendCanvasBtnEl) {
                sendCanvasBtnEl.addEventListener('click', () => {
                    let wf = null;
                    try { wf = JSON.parse(getWorkflowJsonText(img)); } catch (e) {}
                    if (!wf) { _toast('没有可用的工作流数据', 'error'); return; }
                    const payload = { type: 'load-workflow', workflow: wf,
                                      name: img.custom_name || img.file_name || '', ts: Date.now() };
                    // 暂存（画布后开/刷新时也能捞到，60s 内有效）
                    try { localStorage.setItem('noctyra_pending_workflow', JSON.stringify(payload)); } catch (e) {}
                    // 实时广播给已打开的 ComfyUI 画布
                    let sent = false;
                    try { const ch = new BroadcastChannel('noctyra-canvas'); ch.postMessage(payload); ch.close(); sent = true; } catch (e) {}
                    _toast(sent ? '已发送到 ComfyUI 画布，切到 ComfyUI 标签页查看'
                                : '已暂存，打开/刷新 ComfyUI 画布即可加载', 'success');
                });
            }

            // "同配方"按钮：查询同指纹其他图
            const sameRecipeBtn = body.querySelector('.wf-same-recipe-btn');
            if (sameRecipeBtn) {
                sameRecipeBtn.addEventListener('click', async () => {
                    const fp = sameRecipeBtn.dataset.fp;
                    const exclude = sameRecipeBtn.dataset.exclude;
                    if (!fp) return;
                    sameRecipeBtn.disabled = true;
                    sameRecipeBtn.textContent = '查询中...';
                    try {
                        const resp = await fetch(`/api/noctyra/recipe/by-fingerprint?fingerprint=${encodeURIComponent(fp)}&exclude_id=${encodeURIComponent(exclude)}`);
                        const res = await resp.json();
                        if (!res.success) {
                            _toast('查询失败: ' + (res.error || ''), 'error');
                            return;
                        }
                        const recipes = res.recipes || [];
                        if (recipes.length === 0) {
                            _toast('没有找到同指纹的其他配方', 'info');
                        } else {
                            openSameRecipeModal(recipes, fp);
                        }
                    } catch (e) {
                        _toast('查询异常: ' + e.message, 'error');
                    } finally {
                        sameRecipeBtn.disabled = false;
                        sameRecipeBtn.textContent = '同配方';
                    }
                });
            }

            // 资源状态检查（session 过期则作废，避免写已被替换的 DOM）
            if (resources.length > 0) {
                apiCheckResources(resources).then(checkRes => {
                    if (isStale()) return;
                    if (checkRes.success) {
                        const el = body.querySelector('#detail-resources');
                        if (el) {
                            el.innerHTML = renderResourcesHtml(resources, checkRes.results);
                            bindDownloadButtons(el);
                        }
                    }
                });
            }

            // 复制 LoRA 语法（<lora:名字:权重>，只含本地命中的）
            const copyLoraBtn = body.querySelector('.wf-copy-lora-btn');
            if (copyLoraBtn) {
                copyLoraBtn.addEventListener('click', async () => {
                    const id = parseInt(copyLoraBtn.dataset.recipeId);
                    if (!id) return;
                    try {
                        const resp = await fetch(`/api/noctyra/recipe/lora-syntax?id=${id}`);
                        const res = await resp.json();
                        if (!res.success) { _toast('获取 LoRA 语法失败', 'error'); return; }
                        if (res.included === 0) {
                            _toast(res.missing > 0 ? `本配方的 ${res.missing} 个 LoRA 都不在本地` : '本配方没有 LoRA', 'warning');
                            return;
                        }
                        await _copyText(res.combined || res.syntax);
                        const trig = res.trigger_count > 0 ? ` + ${res.trigger_count} 个触发词` : '';
                        const tip = res.missing > 0 ? `（另有 ${res.missing} 个本地缺失，未包含）` : '';
                        _toast(`已复制 ${res.included} 个 LoRA${trig}${tip}`, 'success');
                    } catch (e) {
                        _toast('复制失败', 'error');
                    }
                });
            }

            // 补全缺失资源按钮
            const fetchMissingBtn = body.querySelector('.wf-fetch-missing-btn');
            if (fetchMissingBtn) {
                fetchMissingBtn.addEventListener('click', async () => {
                    const recipeId = parseInt(fetchMissingBtn.dataset.recipeId);
                    if (!recipeId) return;
                    fetchMissingBtn.disabled = true;
                    const originalText = fetchMissingBtn.textContent;
                    fetchMissingBtn.textContent = '提交中...';
                    try {
                        const resp = await fetch(`/api/noctyra/recipe/fetch-missing`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ recipe_id: recipeId }),
                        });
                        const res = await resp.json();
                        if (!res.success) {
                            _toast('补全失败: ' + (res.error || '未知'), 'error');
                            fetchMissingBtn.textContent = originalText;
                            fetchMissingBtn.disabled = false;
                            return;
                        }
                        const started = (res.started || []).length;
                        const failed = (res.failed || []).length;
                        const local = res.already_local || 0;
                        const missing = res.missing || 0;

                        if (missing === 0) {
                            _toast('Noctyra: 本配方资源已全部在本地', 'success');
                        } else if (started > 0 && failed === 0) {
                            _toast(`已启动 ${started} 个下载${local > 0 ? `，另有 ${local} 个已在本地` : ''}`, 'success');
                        } else if (started > 0 && failed > 0) {
                            _toast(`启动 ${started} 成功 · ${failed} 失败`, 'warning');
                        } else {
                            _toast(`全部 ${failed} 个下载启动失败（详见 ComfyUI 控制台）`, 'error');
                        }
                        // 稍等 1.5s 后重跑 check-resources，让"已有/缺失"标记刷新
                        setTimeout(() => {
                            if (isStale()) return;
                            apiCheckResources(resources).then(checkRes => {
                                if (isStale()) return;
                                if (checkRes.success) {
                                    const el = body.querySelector('#detail-resources');
                                    if (el) {
                                        el.innerHTML = renderResourcesHtml(resources, checkRes.results);
                                        bindDownloadButtons(el);
                                    }
                                }
                            });
                        }, 1500);
                    } catch (e) {
                        _toast('补全异常: ' + e.message, 'error');
                    } finally {
                        fetchMissingBtn.textContent = originalText;
                        fetchMissingBtn.disabled = false;
                    }
                });
            }

            // 保存信息按钮
            const saveInfoBtn = body.querySelector('.wf-save-info-btn');
            if (saveInfoBtn) {
                saveInfoBtn.addEventListener('click', async () => {
                    const editDiv = body.querySelector('.wf-detail-edit');
                    const id = parseInt(editDiv.dataset.id);
                    const name = editDiv.querySelector('.wf-edit-name').value.trim();
                    const tagsStr = editDiv.querySelector('.wf-edit-tags').value;
                    const tagsList = tagsStr.split(',').map(t => t.trim()).filter(Boolean);
                    const notesVal = editDiv.querySelector('.wf-edit-notes').value.trim();
                    const favorite = editDiv.querySelector('.wf-edit-favorite').checked;
                    const userNsfw = editDiv.querySelector('.wf-edit-user-nsfw').checked;
                    saveInfoBtn.disabled = true;
                    const res = await apiUpdateInfo(id, {
                        custom_name: name,
                        tags: tagsList,
                        notes: notesVal,
                        favorite,
                        user_nsfw: userNsfw,
                    });
                    if (res.success) {
                        _toast('信息已保存', 'success');
                        _dispatchGalleryReload();
                    } else {
                        _toast(res.error || '保存失败', 'error');
                    }
                    saveInfoBtn.disabled = false;
                });
            }

            // 工作流 JSON 切换（懒加载，避免大 JSON 冻结 UI）
            const toggleBtn = body.querySelector('.wf-toggle-wf-json');
            const jsonBox = body.querySelector('.wf-wf-json-box');
            if (toggleBtn && jsonBox && wfJsonText) {
                const pre = jsonBox.querySelector('.wf-wf-json-pre');
                const copyJsonBtn = jsonBox.querySelector('.wf-copy-wf-json-btn');
                let rendered = false;
                toggleBtn.addEventListener('click', () => {
                    const visible = jsonBox.style.display !== 'none';
                    if (!visible && !rendered) {
                        pre.textContent = wfJsonText;
                        rendered = true;
                    }
                    jsonBox.style.display = visible ? 'none' : 'block';
                    toggleBtn.textContent = visible ? '查看 JSON' : '收起 JSON';
                });
                if (copyJsonBtn) {
                    copyJsonBtn.addEventListener('click', async (e) => {
                        e.stopPropagation();
                        await _copyText(wfJsonText);
                        copyJsonBtn.textContent = '✓';
                        copyJsonBtn.classList.add('copied');
                        setTimeout(() => {
                            copyJsonBtn.textContent = '复制';
                            copyJsonBtn.classList.remove('copied');
                        }, 1200);
                    });
                }
            }

            // 重新获取按钮
            const refetchBtnEl = body.querySelector('.wf-refetch-btn');
            if (refetchBtnEl) {
                refetchBtnEl.addEventListener('click', async () => {
                    const url = refetchBtnEl.dataset.url;
                    refetchBtnEl.disabled = true;
                    refetchBtnEl.textContent = '获取中...';
                    try {
                        const res = await apiFetch(url);
                        if (!res.success) {
                            _toast(res.error || '获取失败', 'error');
                            refetchBtnEl.disabled = false;
                            refetchBtnEl.textContent = '重新获取';
                            return;
                        }
                        refetchBtnEl.textContent = '保存中...';
                        const saveRes = await apiSave(res.image.url, res.image, true);
                        if (saveRes.success) {
                            _toast('已更新', 'success');
                            _dispatchGalleryReload();
                            openDetail(img.id);
                        } else {
                            _toast(saveRes.error || '保存失败', 'error');
                            refetchBtnEl.disabled = false;
                            refetchBtnEl.textContent = '重新获取';
                        }
                    } catch (e) {
                        _toast('异常: ' + e.message, 'error');
                        refetchBtnEl.disabled = false;
                        refetchBtnEl.textContent = '重新获取';
                    }
                });
            }

            // 只有图片支持点开放大；视频自身已有 controls，不要拦截点击
            const detailImg = body.querySelector('img#detail-preview-img');
            if (detailImg) {
                detailImg.addEventListener('click', () => zoomImage(detailImg.src));
            }

            overlay.classList.add('show');
        })
        .catch(err => {
            if (isStale()) return;
            if (body) body.innerHTML = _detailErrorHtml(err && err.message);
            _toast('加载详情失败：' + ((err && err.message) || '网络错误'), 'error');
        });
}

export function zoomImage(src) {
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.9);display:flex;align-items:center;justify-content:center;z-index:10000;cursor:zoom-out';
    overlay.innerHTML = `<img src="${src}" style="max-width:95vw;max-height:95vh;object-fit:contain">`;
    document.body.appendChild(overlay);
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    const close = () => {
        document.removeEventListener('keydown', onKey);
        overlay.remove();
    };
    overlay.addEventListener('click', close);
    document.addEventListener('keydown', onKey);
}

// 详情弹窗 / 同配方弹窗里的"下载"按钮：直接调 extension download
export function bindDownloadButtons(container) {
    container.querySelectorAll('.wf-btn-download-res').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const modelId = parseInt(btn.dataset.modelId) || null;
            const versionId = parseInt(btn.dataset.versionId) || null;
            if (!modelId && !versionId) { _toast('缺少模型信息', 'error'); return; }
            btn.disabled = true;
            btn.textContent = '下载中...';
            try {
                const res = await apiDownloadModel(modelId, versionId);
                if (res.success) {
                    btn.textContent = '已提交';
                    btn.classList.add('submitted');
                    _toast('下载任务已提交', 'success');
                } else {
                    btn.textContent = '失败';
                    _toast(res.error || '下载失败', 'error');
                    setTimeout(() => { btn.textContent = '下载'; btn.disabled = false; }, 2000);
                }
            } catch (err) {
                btn.textContent = '失败';
                _toast('请求异常', 'error');
                setTimeout(() => { btn.textContent = '下载'; btn.disabled = false; }, 2000);
            }
        });
    });
}

// "同配方"弹窗：网格展示同指纹的其他图片，点击切换到该图详情
export function openSameRecipeModal(recipes, fingerprint) {
    // 关掉已有的同配方弹窗，避免叠加
    const existing = document.getElementById('same-recipe-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'same-recipe-overlay';
    overlay.className = 'wf-detail-overlay show';
    overlay.style.display = 'flex';

    const grid = recipes.map(r => {
        const name = r.custom_name || r.file_name || '(无名)';
        const thumb = `/api/noctyra/workflow/image/${r.id}`;
        return `<a class="wf-same-recipe-card" href="#" data-id="${r.id}">
            <img src="${escapeAttr(thumb)}" alt="${escapeAttr(name)}" loading="lazy">
            <div class="wf-same-recipe-name" title="${escapeAttr(name)}">${escapeHtml(name)}</div>
        </a>`;
    }).join('');

    overlay.innerHTML = `
        <div class="wf-detail-modal" style="max-width:900px;">
            <button class="wf-detail-close" type="button">&times;</button>
            <div style="padding:20px 24px;">
                <h3 style="margin:0 0 6px 0;">同配方（指纹 ${escapeHtml(fingerprint.slice(0, 8))}…）</h3>
                <p style="color:var(--text-muted);margin:0 0 16px 0;font-size:13px;">
                    共 ${recipes.length} 张其他图使用了同样的 base_model + LoRA 组合。点击打开详情。
                </p>
                <div class="wf-same-recipe-grid">${grid}</div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    overlay.querySelector('.wf-detail-close').addEventListener('click', close);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) close();
    });
    // 点卡片 → 跳到该图详情（关当前弹窗，打开 openDetail）
    overlay.querySelectorAll('.wf-same-recipe-card').forEach(card => {
        card.addEventListener('click', (e) => {
            e.preventDefault();
            const id = card.dataset.id;
            close();
            if (id) openDetail(parseInt(id, 10));
        });
    });
}
