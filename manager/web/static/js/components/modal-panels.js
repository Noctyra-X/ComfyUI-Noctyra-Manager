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
 * 模型详情弹窗的"来源面板"集合 — 从 modal.js 拆出，减小主文件体积。
 *
 * 导出：
 *   - buildSourceTabs(model)       ：渲染 CivitAI / 版本 / HF / 自定义 四个 tab 容器
 *   - loadVersionsIntoPanel(panel) ：懒加载版本列表到"版本"tab
 *
 * 其余 build* / sanitize* / simpleMarkdown 等为模块私有，只在本文件内使用。
 */
import * as api from '../api.js';
const { previewUrl } = api;
import { showToast } from './toast.js';
import { escapeHtml, escapeAttr, formatNumber, resolveSourceUrl } from '../utils.js';


// 自定义 Tab 里"类型"下拉的候选；值与后端 _TYPE_DIR_KEYWORDS / _TEMPLATE_TYPE_ALIAS 对齐
const _USER_TYPE_OPTIONS = [
    ['',            '自动识别（默认）'],
    ['lora',        'LoRA'],
    ['checkpoint',  'Checkpoint'],
    ['unet',        'UNet / Diffusion Model'],
    ['vae',         'VAE'],
    ['controlnet',  'ControlNet'],
    ['embedding',   'Embedding'],
    ['upscale',     'Upscaler'],
    ['clip',        'CLIP'],
    ['text_encoder', 'Text Encoder'],
    ['clip_vision', 'CLIP Vision'],
    ['motion',      'Motion'],
    ['detection',   'Detection'],
    ['hypernetwork', 'Hypernetwork'],
];

function renderUserTypeOptions(current) {
    const cur = (current || '').toLowerCase();
    return _USER_TYPE_OPTIONS.map(([val, label]) =>
        `<option value="${escapeAttr(val)}"${val === cur ? ' selected' : ''}>${escapeHtml(label)}</option>`
    ).join('');
}

/**
 * NSFW 徽章：按 CivitAI nsfwLevel 分级（2=PG13, 4=R, 8=X, 16=XXX）
 * 返回一颗带级别的胶囊，类型等级越高色调越烈。
 */
function renderNsfwBadge(level) {
    const lvl = parseInt(level) || 0;
    let label = 'NSFW', cls = 'nsfw-badge nsfw-badge-r';
    if (lvl >= 16)      { label = 'NSFW · XXX'; cls = 'nsfw-badge nsfw-badge-xxx'; }
    else if (lvl >= 8)  { label = 'NSFW · X';   cls = 'nsfw-badge nsfw-badge-x'; }
    else if (lvl >= 4)  { label = 'NSFW · R';   cls = 'nsfw-badge nsfw-badge-r'; }
    else if (lvl >= 2)  { label = 'NSFW · PG13'; cls = 'nsfw-badge nsfw-badge-pg'; }
    return `<span class="${cls}" title="CivitAI 分级: ${lvl}"><svg class="nsfw-badge-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>${label}</span>`;
}


export function buildSourceTabs(model) {
    const hasCivitai = model.source === 'civitai' && model.civitai_model_id;
    const hasHF = !!(model.hf_repo_id || model.hf_url || (model.source === 'huggingface' && model.source_url));

    // 默认激活来源：有 CivitAI → civitai；否则有 HF → huggingface；否则 custom
    const defaultSource = hasCivitai ? 'civitai' : (hasHF ? 'huggingface' : 'custom');

    const tabs = [];
    if (hasCivitai) {
        tabs.push(`<button class="source-tab${defaultSource === 'civitai' ? ' active' : ''}" data-source="civitai"><img class="source-tab-icon" src="/noctyra_static/images/civitai-logo.svg" alt=""> CivitAI</button>`);
        tabs.push(`<button class="source-tab" data-source="versions">版本</button>`);
    }
    if (hasHF) {
        tabs.push(`<button class="source-tab${defaultSource === 'huggingface' ? ' active' : ''}" data-source="huggingface"><img class="source-tab-icon" src="/noctyra_static/images/hf-logo.svg" alt=""> HuggingFace</button>`);
    }
    // 自定义 Tab 始终存在
    tabs.push(`<button class="source-tab${defaultSource === 'custom' ? ' active' : ''}" data-source="custom">自定义</button>`);

    const tabNav = `<div class="source-tabs">${tabs.join('')}</div>`;

    const civitaiPanel = hasCivitai ? buildCivitaiPanel(model, defaultSource === 'civitai') : '';
    const versionsPanel = hasCivitai ? buildVersionsPanel(model) : '';
    const hfPanel = hasHF ? buildHFPanel(model, defaultSource === 'huggingface') : '';
    const customPanel = buildCustomPanel(model, defaultSource === 'custom');

    return `
        <div class="source-info-area">
            ${tabNav}
            ${civitaiPanel}
            ${versionsPanel}
            ${hfPanel}
            ${customPanel}
        </div>`;
}

// 渲染 safetensors 结构（元数据 + 张量树）到给定容器。
// 由文件结构浮层（modal.js 的 openStructureOverlay）调用。
export function renderStructureInto(container, data) {
    if (!data || !data.success) {
        container.innerHTML = `<div class="st-error">${escapeHtml((data && data.error) || '读取失败')}</div>`;
        return;
    }
    const meta = data.metadata || {};
    const tensors = data.tensors || [];
    let html = '';

    const metaKeys = Object.keys(meta);
    if (metaKeys.length) {
        html += `<div class="st-section"><div class="st-section-title">Metadata</div><table class="st-meta">`;
        for (const k of metaKeys) {
            const v = typeof meta[k] === 'string' ? meta[k] : JSON.stringify(meta[k]);
            const long = v.length > 100;
            const cell = long
                ? `<details><summary>${escapeHtml(v.slice(0, 100))}… <span class="st-meta-len">(${v.length} 字)</span></summary><pre class="st-meta-pre">${escapeHtml(v)}</pre></details>`
                : escapeHtml(v);
            html += `<tr><td class="st-meta-k">${escapeHtml(k)}</td><td class="st-meta-v">${cell}</td></tr>`;
        }
        html += `</table></div>`;
    }

    html += `<div class="st-section"><div class="st-section-title">Tensors <span class="st-count">${tensors.length}${data.truncated ? '+' : ''}</span></div>`;
    html += tensors.length ? `<div class="st-tree"></div>` : `<div class="st-empty">无张量信息</div>`;
    html += `</div>`;
    container.innerHTML = html;

    if (tensors.length) mountTensorTree(container.querySelector('.st-tree'), tensors);
}

function _stNode() { return { children: new Map(), leaves: [], count: 0 }; }

function buildTensorTree(tensors) {
    const root = _stNode();
    for (const t of tensors) {
        const parts = String(t.name || '').split('.');
        let node = root;
        node.count++;
        for (let i = 0; i < parts.length - 1; i++) {
            const seg = parts[i];
            if (!node.children.has(seg)) node.children.set(seg, _stNode());
            node = node.children.get(seg);
            node.count++;
        }
        node.leaves.push({ name: parts[parts.length - 1], dtype: t.dtype, shape: t.shape });
    }
    return root;
}

function _leafHtml(lf) {
    const shape = Array.isArray(lf.shape) && lf.shape.length ? `[${lf.shape.join(', ')}]` : '—';
    return `<div class="st-leaf"><span class="st-leaf-name">${escapeHtml(lf.name)}</span><span class="st-leaf-shape">${escapeHtml(shape)}</span><span class="st-leaf-dtype">${escapeHtml(lf.dtype || '')}</span></div>`;
}

// 懒渲染张量树：初始只铺顶层组，折叠组的子节点留空，首次展开（toggle）时才填充。
// 2958 张量的 LoRA 一次性全渲染会塞上万 DOM 节点 → 卡顿、鼠标拖影；懒渲染后只渲染展开过的路径。
function mountTensorTree(treeEl, tensors) {
    const root = buildTensorTree(tensors);
    const nodeMap = new Map();
    let idc = 0;

    function childrenHtml(node, depth) {
        let html = '';
        const keys = [...node.children.keys()].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
        const autoOpen = depth < 2 && keys.length <= 8;   // 浅层 + 分组少才默认展开
        for (const key of keys) {
            const child = node.children.get(key);
            const id = 'n' + (idc++);
            nodeMap.set(id, child);
            const body = autoOpen ? childrenHtml(child, depth + 1) : '';
            const lazy = autoOpen ? '' : ' data-lazy="1"';
            const open = autoOpen ? ' open' : '';
            html += `<details class="st-group"${open} data-nid="${id}"${lazy}><summary><span class="st-group-name">${escapeHtml(key)}</span><span class="st-group-count">${child.count}</span></summary><div class="st-group-body" data-depth="${depth + 1}">${body}</div></details>`;
        }
        for (const lf of node.leaves) html += _leafHtml(lf);
        return html;
    }

    treeEl.innerHTML = childrenHtml(root, 0);

    // toggle 不冒泡 → 捕获阶段在容器上监听；首次展开懒节点时填充其子节点
    treeEl.addEventListener('toggle', (e) => {
        const d = e.target;
        if (!d || d.tagName !== 'DETAILS' || !d.open || !d.dataset.lazy) return;
        const node = nodeMap.get(d.dataset.nid);
        const body = d.querySelector(':scope > .st-group-body');
        if (node && body) {
            body.innerHTML = childrenHtml(node, parseInt(body.dataset.depth) || 1);
            delete d.dataset.lazy;
        }
    }, true);
}

function buildVersionsPanel(model) {
    // 懒加载：切到"版本"tab 时再 fetch，用 data-model-id 标记
    return `
    <div class="source-panel" data-source="versions" data-model-id="${model.civitai_model_id}" data-current-vid="${model.civitai_version_id || ''}">
        <div class="versions-empty" style="padding:20px;color:var(--text-muted);text-align:center;">切换到此 tab 后自动加载…</div>
    </div>`;
}

function buildCustomPanel(model, isActive) {
    const activeClass = isActive ? ' active' : '';
    const tw = Array.isArray(model.trained_words) ? model.trained_words.join(', ') : '';
    const previewLocal = (model.preview_url || '').startsWith('sidecar://');
    const previewThumb = previewLocal
        ? `<img src="${escapeAttr(previewUrl(model.preview_url))}" class="custom-preview-thumb" alt="">`
        : `<div class="custom-preview-placeholder">${model.preview_url ? '远程 URL' : '未设置'}</div>`;

    return `
    <div class="source-panel${activeClass}" data-source="custom">
        <div class="custom-form">
            <div class="info-row">
                <span class="info-label">模型名</span>
                <input type="text" class="custom-input" data-field="model_name" value="${escapeAttr(model.model_name || '')}" placeholder="显示名称">
            </div>
            <div class="info-row">
                <span class="info-label">基础模型</span>
                <input type="text" class="custom-input" data-field="base_model" value="${escapeAttr(model.base_model === 'Unknown' ? '' : (model.base_model || ''))}" placeholder="SDXL / Flux.1 D / Illustrious ...">
            </div>
            <div class="info-row">
                <span class="info-label">类型</span>
                <select class="custom-input" data-field="user_model_type" title="留空=自动识别；手动选择会覆盖所有自动判定">
                    ${renderUserTypeOptions(model.user_model_type || '')}
                </select>
            </div>
            <div class="info-row">
                <span class="info-label">作者</span>
                <input type="text" class="custom-input" data-field="creator" value="${escapeAttr(model.creator || '')}" placeholder="可留空">
            </div>
            <div class="info-row">
                <span class="info-label">版本名</span>
                <input type="text" class="custom-input" data-field="version_name" value="${escapeAttr(model.version_name || '')}" placeholder="v1.0 / Beta ...">
            </div>
            <div class="info-section">
                <div class="info-label">触发词 <span class="custom-hint">（逗号分隔）</span></div>
                <textarea class="custom-textarea" data-field="trained_words" rows="2" placeholder="word1, word2, word3">${escapeHtml(tw)}</textarea>
            </div>
            <div class="info-section">
                <div class="info-label">描述</div>
                <textarea class="custom-textarea" data-field="model_description" rows="4" placeholder="关于此模型的描述...">${escapeHtml(model.model_description || '')}</textarea>
            </div>
            <div class="info-section">
                <div class="info-label">预览图</div>
                <div class="custom-preview-row">
                    ${previewThumb}
                    <div class="custom-preview-actions">
                        <input type="file" id="custom-preview-file" accept="image/png,image/jpeg,image/webp,image/gif" style="display:none">
                        <button class="btn btn-sm" id="custom-preview-upload-btn">上传本地图片</button>
                        <button class="btn btn-sm" id="custom-preview-clear-btn" ${model.preview_url ? '' : 'disabled'}>清除</button>
                    </div>
                </div>
            </div>
            <div class="custom-form-actions">
                <button class="btn btn-primary" id="custom-save-btn">保存</button>
                <span class="custom-save-hint"></span>
            </div>
        </div>
    </div>`;
}

function buildCivitaiPanel(model, isActive) {
    const activeClass = isActive ? ' active' : '';
    const civitaiUrl = resolveSourceUrl(model.source_url || '', model.nsfw);

    const statParts = [];
    if (model.thumbs_up > 0) statParts.push(`👍 ${formatNumber(model.thumbs_up)}`);
    if (model.downloads > 0) statParts.push(`⬇ ${formatNumber(model.downloads)}`);
    if (model.rating > 0) statParts.push(`⭐ ${model.rating.toFixed(1)} (${formatNumber(model.rating_count)})`);
    if (model.comment_count > 0) statParts.push(`💬 ${formatNumber(model.comment_count)}`);

    // 作者（带头像）
    let creatorHtml = '';
    if (model.creator) {
        const avatar = model.creator_avatar
            ? `<img src="${escapeAttr(previewUrl(model.creator_avatar))}" class="creator-avatar" alt="">`
            : '';
        creatorHtml = `<div class="info-row"><span class="info-label">作者</span><span class="info-value creator-value">${avatar}${escapeHtml(model.creator)}</span></div>`;
    }

    // 发布时间
    const publishedAt = model.published_at ? model.published_at.substring(0, 10) : '';

    // 描述（CivitAI 原始 HTML，安全渲染）
    let descHtml = '';
    if (model.model_description) {
        const safeHtml = sanitizeHtml(model.model_description, { baseUrl: descBaseUrl(model) });
        if (safeHtml.trim()) {
            descHtml = `<div class="info-section"><div class="info-label">描述</div><div class="civitai-description">${safeHtml}</div></div>`;
        }
    }

    return `
    <div class="source-panel${activeClass}" data-source="civitai">
        ${civitaiUrl && isSafeUrl(civitaiUrl) ? `<div class="info-row"><span class="info-label">链接</span><span class="info-value"><a href="${escapeAttr(civitaiUrl)}" target="_blank" rel="noopener" class="source-link source-civitai"><img src="/noctyra_static/images/civitai-logo.svg" alt="" class="source-link-icon">CivitAI</a></span></div>` : ''}
        ${model.civitai_model_type ? `<div class="info-row"><span class="info-label">类型</span><span class="info-value">${escapeHtml(model.civitai_model_type)}</span></div>` : ''}
        ${creatorHtml}
        ${statParts.length > 0 ? `<div class="info-row"><span class="info-label">统计</span><span class="info-value modal-stats">${statParts.join(' · ')}</span></div>` : ''}
        ${publishedAt ? `<div class="info-row"><span class="info-label">发布时间</span><span class="info-value">${publishedAt}</span></div>` : ''}
        ${model.nsfw ? `<div class="info-row"><span class="info-label">NSFW</span><span class="info-value">${renderNsfwBadge(model.max_nsfw_level)}</span></div>` : ''}
        ${descHtml}
        ${model.civitai_tags && model.civitai_tags.length > 0 ? `
            <div class="info-section">
                <div class="info-label">标签</div>
                <div class="tag-list">${model.civitai_tags.map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('')}</div>
            </div>` : ''}
        ${model.trained_words && model.trained_words.length > 0 ? `
            <div class="info-section">
                <div class="info-label">触发词</div>
                <div class="tag-list trigger-words">${model.trained_words.slice(0, 20).map(w => `<span class="tag tag-trigger">${escapeHtml(w)}</span>`).join('')}</div>
            </div>` : ''}
    </div>`;
}

export async function loadVersionsIntoPanel(panel) {
    const modelId = parseInt(panel.dataset.modelId);
    if (!modelId) return;
    if (panel.dataset.loading || panel.dataset.loaded) return;
    panel.dataset.loading = '1';
    panel.innerHTML = '<div class="versions-empty" style="padding:20px;color:var(--text-muted);text-align:center;">加载版本列表中...</div>';

    try {
        const resp = await fetch(`/api/noctyra/model-versions?model_id=${modelId}`);
        if (!resp.ok) {
            panel.innerHTML = `<div style="padding:20px;color:var(--error);">加载失败: HTTP ${resp.status}</div>`;
            return;
        }
        const res = await resp.json();
        if (!panel.isConnected) return;  // 期间用户切了模型/关了弹窗，panel 已脱离 DOM → 别再写
        if (!res.success) {
            panel.innerHTML = `<div style="padding:20px;color:var(--error);">加载失败: ${escapeHtml(res.error || '')}</div>`;
            return;
        }
        const versions = Array.isArray(res.versions) ? res.versions : [];
        panel.innerHTML = renderVersionsList(versions, res.model_id, parseInt(panel.dataset.currentVid) || 0);
        bindVersionActions(panel);
        panel.dataset.loaded = '1';
    } catch (e) {
        panel.innerHTML = `<div style="padding:20px;color:var(--error);">网络错误: ${escapeHtml(e.message || '')}</div>`;
    } finally {
        delete panel.dataset.loading;
    }
}

function renderVersionsList(versions, modelId, currentVid) {
    if (versions.length === 0) {
        return '<div style="padding:20px;color:var(--text-muted);">该模型暂无版本信息</div>';
    }
    const rows = versions.map(v => {
        const isCurrent = v.version_id === currentVid;
        const sizeMB = v.file_size ? (v.file_size / 1024 / 1024).toFixed(1) + ' MB' : '';
        const pubDate = v.published_at ? v.published_at.slice(0, 10) : '';
        let statusHtml = '';
        if (v.local) {
            const badge = isCurrent ? ' · 当前' : '';
            statusHtml = `<span class="version-badge version-local"><svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>本地已有${badge}</span>`;
        } else if (v.ignored) {
            statusHtml = `<span class="version-badge version-ignored">已忽略</span>`;
        }

        let actionsHtml = '';
        if (!v.local && !v.ignored) {
            actionsHtml += `<button class="btn btn-sm version-action" data-action="download" data-model-id="${modelId}" data-version-id="${v.version_id}">下载</button>`;
            actionsHtml += `<button class="btn btn-sm version-action" data-action="ignore" data-model-id="${modelId}" data-version-id="${v.version_id}">忽略</button>`;
        } else if (v.ignored) {
            actionsHtml += `<button class="btn btn-sm version-action" data-action="unignore" data-model-id="${modelId}" data-version-id="${v.version_id}">取消忽略</button>`;
        } else if (v.local) {
            actionsHtml += `<span style="color:var(--text-muted);font-size:12px;">${escapeHtml(v.local_file_name || '')}</span>`;
        }

        const previewThumb = v.preview_url
            ? `<img class="version-preview" src="${escapeAttr(previewUrl(v.preview_url))}" loading="lazy" onerror="this.style.visibility='hidden'">`
            : `<div class="version-preview-placeholder"></div>`;

        return `
        <div class="version-row${isCurrent ? ' version-current' : ''}">
            ${previewThumb}
            <div class="version-info">
                <div class="version-head">
                    <span class="version-name">${escapeHtml(v.name || `v${v.version_id}`)}</span>
                    ${statusHtml}
                </div>
                <div class="version-meta">
                    ${v.base_model ? `<span>${escapeHtml(v.base_model)}</span>` : ''}
                    ${sizeMB ? `<span>${sizeMB}</span>` : ''}
                    ${pubDate ? `<span>${pubDate}</span>` : ''}
                </div>
            </div>
            <div class="version-actions">${actionsHtml}</div>
        </div>`;
    }).join('');
    return `<div class="versions-list">${rows}</div>`;
}

function bindVersionActions(panel) {
    panel.querySelectorAll('.version-action').forEach(btn => {
        btn.addEventListener('click', async () => {
            const action = btn.dataset.action;
            const modelId = parseInt(btn.dataset.modelId);
            const versionId = parseInt(btn.dataset.versionId);
            if (!modelId || !versionId) return;
            btn.disabled = true;

            try {
                if (action === 'download') {
                    const res = await api.downloadByCivitaiRef(modelId, versionId);
                    if (res && res.success) {
                        showToast('已加入下载队列', 'success');
                    } else if (res && res.already_exists) {
                        showToast(`目标文件已存在: ${res.save_dir || ''}`, 'warning');
                    } else {
                        showToast('下载启动失败: ' + (res?.error || '未知'), 'error');
                    }
                } else if (action === 'ignore') {
                    const resp = await fetch('/api/noctyra/version/ignore', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ model_id: modelId, version_id: versionId }),
                    });
                    const res = await resp.json();
                    if (res.success) {
                        showToast('已忽略该版本', 'success');
                        // 重新加载面板
                        delete panel.dataset.loaded;
                        loadVersionsIntoPanel(panel);
                    }
                } else if (action === 'unignore') {
                    const resp = await fetch('/api/noctyra/version/unignore', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ model_id: modelId, version_id: versionId }),
                    });
                    const res = await resp.json();
                    if (res.success) {
                        showToast('已取消忽略', 'success');
                        delete panel.dataset.loaded;
                        loadVersionsIntoPanel(panel);
                    }
                }
            } catch (e) {
                showToast('操作失败: ' + e.message, 'error');
            } finally {
                btn.disabled = false;
            }
        });
    });
}

function buildHFPanel(model, isActive) {
    const activeClass = isActive ? ' active' : '';
    const hfUrl = model.hf_url || (model.source === 'huggingface' ? model.source_url : '');
    const hfAuthor = model.hf_author || (model.source === 'huggingface' ? model.creator : '');
    const hfDesc = model.hf_description || (model.source === 'huggingface' ? model.model_description : '');
    const hasCivitaiAlso = !!(model.civitai_model_id);
    const hfTags = model.hf_tags || (model.source === 'huggingface' && !hasCivitaiAlso ? model.tags : []);
    const hfLastMod = model.hf_last_modified || '';

    const statParts = [];
    if (model.hf_downloads > 0) statParts.push(`⬇ ${formatNumber(model.hf_downloads)}`);
    if (model.hf_likes > 0) statParts.push(`❤ ${formatNumber(model.hf_likes)}`);

    const matchTypeMap = {
        hash: { label: 'SHA256 精确', cls: 'match-badge-hash', title: 'LFS SHA256 精确比对命中，绑定 100% 正确' },
        filename: { label: '文件名命中', cls: 'match-badge-filename', title: 'repo 里存在同名文件，较可靠' },
        fuzzy: { label: '模糊匹配', cls: 'match-badge-fuzzy', title: '仓库名相似的兜底匹配，可能不是原仓库' },
    };
    const matchBadge = (model.hf_match_type && matchTypeMap[model.hf_match_type]) ? `
        <div class="info-row"><span class="info-label">匹配级别</span><span class="info-value"><span class="match-badge ${matchTypeMap[model.hf_match_type].cls}" title="${escapeAttr(matchTypeMap[model.hf_match_type].title)}">${matchTypeMap[model.hf_match_type].label}</span></span></div>` : '';

    // 受限仓库读不到 README 时的提示（API 返回 gated=auto/manual 等）
    const isGated = model.hf_gated && model.hf_gated !== 'false' && model.hf_gated !== false;
    const gatedBanner = (isGated && !hfDesc && model.hf_repo_id) ? `
        <div class="hf-gated-banner">
            <div class="hf-gated-icon">⚠️</div>
            <div class="hf-gated-body">
                <div class="hf-gated-title">这是受限仓库（gated: <code>${escapeHtml(String(model.hf_gated))}</code>）</div>
                <div class="hf-gated-hint">
                    HuggingFace 不开放未接受协议用户的 README。请确认：<br>
                    1) 已在 <a href="https://huggingface.co/${escapeAttr(model.hf_repo_id)}" target="_blank" rel="noopener">仓库页面</a>点击 "Agree and access repository"<br>
                    2) 已在「设置 → API 密钥」填入该 <strong>同一 HF 账户</strong>的 Access Token（权限至少为 Read）<br>
                    3) 回到本页点击右上角「匹配」（或按住 Shift 点击以强制重匹配），重新获取 README
                </div>
                <div class="hf-gated-actions">
                    <a href="https://huggingface.co/${escapeAttr(model.hf_repo_id)}" target="_blank" rel="noopener" class="btn btn-sm btn-primary">前往仓库接受协议</a>
                    <a href="https://huggingface.co/settings/tokens" target="_blank" rel="noopener" class="btn btn-sm btn-secondary">管理 HF Access Token</a>
                </div>
            </div>
        </div>` : '';

    return `
    <div class="source-panel${activeClass}" data-source="huggingface">
        ${hfUrl ? `<div class="info-row"><span class="info-label">链接</span><span class="info-value"><a href="${escapeAttr(hfUrl)}" target="_blank" rel="noopener" class="source-link source-hf"><img src="/noctyra_static/images/hf-logo.svg" alt="" class="source-link-icon">HuggingFace</a></span></div>` : ''}
        ${model.hf_repo_id ? `<div class="info-row"><span class="info-label">Repo</span><span class="info-value" style="font-family:var(--font-mono,'JetBrains Mono',Consolas,monospace);font-size:12px">${escapeHtml(model.hf_repo_id)}</span></div>` : ''}
        ${matchBadge}
        ${hfAuthor ? `<div class="info-row"><span class="info-label">作者</span><span class="info-value">${escapeHtml(hfAuthor)}</span></div>` : ''}
        ${statParts.length > 0 ? `<div class="info-row"><span class="info-label">统计</span><span class="info-value modal-stats">${statParts.join(' · ')}</span></div>` : ''}
        ${hfLastMod ? `<div class="info-row"><span class="info-label">更新时间</span><span class="info-value">${escapeHtml(hfLastMod.substring(0, 10))}</span></div>` : ''}
        ${gatedBanner}
        ${hfDesc ? `<div class="info-section"><div class="info-label">描述</div><div class="hf-description">${simpleMarkdown(hfDesc, { baseUrl: descBaseUrl(model) })}</div></div>` : ''}
        ${hfTags && hfTags.length > 0 ? `
            <div class="info-section">
                <div class="info-label">标签</div>
                <div class="tag-list">${hfTags.slice(0, 30).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('')}</div>
            </div>` : ''}
        ${!hasCivitaiAlso && model.trained_words && model.trained_words.length > 0 ? `
            <div class="info-section">
                <div class="info-label">触发词</div>
                <div class="tag-list trigger-words">${model.trained_words.slice(0, 20).map(w => `<span class="tag tag-trigger">${escapeHtml(w)}</span>`).join('')}</div>
            </div>` : ''}
    </div>`;
}

function resolveRelativeUrl(src, baseUrl) {
    if (!src || !baseUrl) return src;
    const trimmed = String(src).trim();
    // 绝对 URL / data / fragment / 协议相对路径：不处理
    if (/^(https?:|data:|#|\/\/)/i.test(trimmed)) return trimmed;
    // HF README 里 /xxx 开头的一般是仓库内绝对路径，也要接到仓库根
    const cleaned = trimmed.replace(/^\.?\/+/, '');
    return baseUrl.replace(/\/$/, '') + '/' + cleaned;
}

// 描述里相对图片/链接的解析基址：HF README 的相对资源在 <repo>/resolve/main 下，
// 补成绝对地址后经 previewUrl 代理正常加载（否则相对路径解析到本机 → 404 裂图）。
// CivitAI 描述一般是绝对 URL，不猜（传空基址；解析不出的相对图由 sanitize 兜底移除）。
function descBaseUrl(model) {
    if (!model) return '';
    // HF README 里的相对图片/链接在 <repo>/resolve/main 下解析。优先 hf_url，其次主来源是 HF 时用
    // source_url —— 都不依赖可能为空的 hf_repo_id（chord 这类绑定未落 repo_id 的模型也能正常解析）。
    const hf = model.hf_url || (model.source === 'huggingface' ? model.source_url : '');
    if (hf && /huggingface\.co/i.test(hf)) {
        return hf.replace(/\/+$/, '') + '/resolve/main';
    }
    return '';
}

function sanitizeHtml(html, opts = {}) {
    if (!html) return '';
    const baseUrl = opts.baseUrl || '';
    const div = document.createElement('div');
    div.innerHTML = html;
    div.querySelectorAll('script,style,iframe,object,embed').forEach(el => el.remove());
    div.querySelectorAll('*').forEach(el => {
        for (const attr of [...el.attributes]) {
            if (attr.name.startsWith('on') || attr.name === 'srcdoc') {
                el.removeAttribute(attr.name);
            }
        }
        if (el.tagName === 'A') {
            let href = el.getAttribute('href');
            if (href) {
                href = resolveRelativeUrl(href, baseUrl);
                if (!isSafeUrl(href)) {
                    el.removeAttribute('href');
                } else {
                    el.setAttribute('href', href);
                }
            }
            el.setAttribute('target', '_blank');
            el.setAttribute('rel', 'noopener noreferrer');
        }
        if (el.tagName === 'IMG') {
            let origSrc = el.getAttribute('src');
            if (origSrc) {
                origSrc = resolveRelativeUrl(origSrc, baseUrl);
            }
            if (origSrc && !isSafeUrl(origSrc)) {
                el.remove();
                return;
            }
            if (origSrc && origSrc.startsWith('http')) {
                // 有栅格图后缀（jpg/png/webp/gif/avif）的走预览代理（服务端用代理拉取更可靠 + 缓存）；
                // 无后缀 / SVG（如 shields.io 徽章）直接加载 —— 代理按 URL 后缀猜 MIME，会把无后缀的
                // SVG 当成 jpeg 返回，浏览器解不了就裂，而且代理本身也不支持 svg。
                const raster = /\.(jpe?g|png|webp|gif|avif)(\?|#|$)/i.test(origSrc);
                el.setAttribute('src', raster ? previewUrl(origSrc) : origSrc);
            } else if (origSrc && origSrc.startsWith('data:image/')) {
                el.setAttribute('src', origSrc);   // 内联图，保留
            } else {
                // 仍是无基址可解析的相对路径 → 加载必 404 裂图，直接移除、不渲染成破图
                el.remove();
                return;
            }
            el.style.maxWidth = '100%';
            el.style.borderRadius = '6px';
            el.style.cursor = 'pointer';
            el.classList.add('desc-zoomable');
            // 如果图片被 <a> 包裹，移除链接避免下载
            const parent = el.parentElement;
            if (parent && parent.tagName === 'A') {
                parent.replaceWith(el);
            }
        }
    });
    return div.innerHTML;
}

function isSafeUrl(url) {
    if (!url) return false;
    const trimmed = String(url).trim().toLowerCase();
    // 禁止 javascript: / vbscript: / data: 协议（data:image 除外）
    if (/^(javascript|vbscript):/i.test(trimmed)) return false;
    if (/^data:/i.test(trimmed) && !/^data:image\//i.test(trimmed)) return false;
    return true;
}

function simpleMarkdown(text, opts = {}) {
    if (!text) return '';

    // 1. 提取代码块和行内代码，用占位符避免后续 Markdown 处理干扰
    const codeBlocks = [];
    let html = text.replace(/```([\w+-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
        codeBlocks.push(`<pre><code>${escapeHtml(code.replace(/\n$/, ''))}</code></pre>`);
        return `\x00CB${codeBlocks.length - 1}\x00`;
    });
    const inlineCodes = [];
    html = html.replace(/`([^`\n]+)`/g, (_, code) => {
        inlineCodes.push(`<code>${escapeHtml(code)}</code>`);
        return `\x00IC${inlineCodes.length - 1}\x00`;
    });

    // 2. Markdown 转换（保留原有 HTML 标签如 <img>，稍后由 sanitizeHtml 清洗）
    html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^# (.+)$/gm, '<h3>$1</h3>');
    // src/href/alt/label 转义引号，防属性逃逸（sanitizeHtml 是兜底，这里是纵深防御）
    html = html.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_, alt, src) => {
        return `<img src="${escapeHtml(src)}" alt="${escapeHtml(alt)}">`;
    });
    html = html.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_, label, href) => {
        return `<a href="${escapeHtml(href)}">${escapeHtml(label)}</a>`;
    });
    html = html.replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>');

    // 3. 段落和换行
    html = html.replace(/\n{2,}/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    html = `<p>${html}</p>`;
    html = html.replace(/<p>\s*<\/p>/g, '');

    // 4. 恢复代码占位符
    html = html.replace(/\x00CB(\d+)\x00/g, (_, i) => codeBlocks[parseInt(i, 10)] || '');
    html = html.replace(/\x00IC(\d+)\x00/g, (_, i) => inlineCodes[parseInt(i, 10)] || '');

    // 5. 安全清洗（自动代理图片、移除脚本、处理链接，relative URL 接到 baseUrl）
    return sanitizeHtml(html, opts);
}
