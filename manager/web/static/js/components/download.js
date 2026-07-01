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
 * CivitAI 下载弹窗 — 粘贴 URL → 选版本 → 选目录 → 下载
 * 支持多任务并发下载，通过 WebSocket 接收实时进度
 */
import * as api from '../api.js';
const { previewUrl, onWsEvent } = api;
import { state } from '../state.js';
import { showToast } from './toast.js';
import { loadModels } from './card-grid.js';
import { escapeAttr as esc, formatSize } from '../utils.js';

let overlay = null;
const activeDownloads = new Map();
let restoring = false;   // restoreDownloads 防并发重入（WS 未知下载会高频触发）

// 下载操作图标(与任务中心一致;复用全局 .tc-act-btn 样式)
const DL_ACT_ICONS = {
    pause:  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="9" y1="5" x2="9" y2="19"/><line x1="15" y1="5" x2="15" y2="19"/></svg>',
    resume: '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" stroke="none"><polygon points="6 4 20 12 6 20"/></svg>',
    retry:  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/></svg>',
    cancel: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    remove: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
};
const DL_TASK_LABELS = {
    queued: '排队中', downloading: '下载中', complete: '已完成',
    error: '失败', cancelled: '已取消', paused: '已暂停', interrupted: '已中断',
};

function dlTaskButtons(id, status) {
    const mk = (act, label) =>
        `<button class="tc-act-btn tc-act-${act}" data-dl-act="${act}" data-dl-id="${esc(id)}" title="${label}" aria-label="${label}">${DL_ACT_ICONS[act]}</button>`;
    if (status === 'downloading' || status === 'queued') return mk('pause', '暂停') + mk('cancel', '取消');
    if (status === 'paused') return mk('resume', '继续') + mk('cancel', '取消');
    if (status === 'interrupted' || status === 'error' || status === 'cancelled') return mk('retry', '重试') + mk('remove', '移除');
    return '';
}

/** 给右下角任务中心读取当前下载任务(统一展示进度)。下载本身仍由本模块 WS 驱动更新。 */
export function getActiveDownloads() {
    return Array.from(activeDownloads.values());
}

// 下载任务镜像变化时通知任务中心刷新(乐观更新不走 WS，需主动广播)
function notifyDownloadsChanged() {
    window.dispatchEvent(new Event('noctyra-downloads-changed'));
}

/** 任务中心「移除」终态任务后调用：后端已删记录且不再发 WS，需同步清掉前端镜像。 */
export function dropActiveDownload(id) {
    if (activeDownloads.delete(id)) { refreshTasksArea(); notifyDownloadsChanged(); }
}

/** 乐观更新本地下载状态并刷新弹窗。用于操作成功后即时反馈，不依赖 WS 回推
 *  （如 queued 任务在等信号量时被暂停，后端不一定广播状态）。WS 后续会确认/修正。 */
function applyLocalDownloadStatus(id, status) {
    const dl = activeDownloads.get(id);
    if (!dl) return;
    dl.status = status;
    if (status === 'paused') { dl.speed = 0; dl.eta = 0; }
    refreshTasksArea();
    notifyDownloadsChanged();
}

const _OPTIMISTIC_NEXT = { pause: 'paused', resume: 'queued', retry: 'queued', cancel: 'cancelled' };

/** 执行下载任务操作(暂停/继续/重试/取消/移除)。任务中心与下载弹窗共用这一入口。
 *  操作成功即乐观更新状态(不等 WS)；被拒则重置按钮并提示。btnEl 可选,用于禁用防连点。 */
export async function runDownloadAction(action, id, btnEl) {
    if (btnEl) btnEl.disabled = true;
    try {
        let res;
        if (action === 'pause') res = await api.pauseDownload(id);
        else if (action === 'resume') res = await api.resumeDownload(id);
        else if (action === 'retry') res = await api.retryDownload(id);
        else if (action === 'cancel') res = await api.cancelDownload(id);
        else if (action === 'remove') res = await api.removeDownload(id);
        else return;

        if (action === 'remove') {
            if (res && res.success) dropActiveDownload(id);
            else if (btnEl) btnEl.disabled = false;
            return;
        }
        if (res && res.success) {
            const next = _OPTIMISTIC_NEXT[action];
            if (next) applyLocalDownloadStatus(id, next);
        } else {
            // 被拒(状态已变 / resume·retry 旧任务收尾未完)：重置按钮，靠 WS 或下次刷新纠正
            if (btnEl) btnEl.disabled = false;
            if (action === 'resume' || action === 'retry') showToast('任务正在收尾，请稍候再点', 'info');
        }
    } catch (_) {
        if (btnEl) btnEl.disabled = false;
    }
}

export function initDownload() {
    const el = document.createElement('div');
    el.className = 'settings-overlay';
    el.id = 'download-overlay';
    el.innerHTML = '<div class="settings-content"></div>';
    document.body.appendChild(el);
    overlay = el;

    el.addEventListener('click', e => {
        if (e.target === el) closeDownload();
    });

    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && overlay.classList.contains('show')) closeDownload();
    });

    const btn = document.getElementById('btn-download');
    if (btn) btn.addEventListener('click', openDownload);

    onWsEvent('download_progress', handleDownloadProgress);

    // 刷新后恢复：下载在后端跑着，但前端 activeDownloads 内存刷新即空，
    // 导致 WS 进度事件被 handleDownloadProgress 的 `if (!dl) return` 丢弃 → 下载"消失"。
    // 启动时从 /downloads 拉回进行中的任务，之后 WS 就能正常更新。
    restoreDownloads();

    // WS 断线重连后，断线期间的进度事件已丢，重新拉一次补齐
    window.addEventListener('noctyra-ws-reconnected', restoreDownloads);
}

async function restoreDownloads() {
    if (restoring) return;
    restoring = true;
    try {
        const res = await api.fetchDownloads();
        if (!res || !res.success || !Array.isArray(res.downloads)) return;
        let any = false;
        for (const d of res.downloads) {
            if (!d.id || activeDownloads.has(d.id)) continue;
            if (['downloading', 'queued', 'paused', 'interrupted'].includes(d.status)) {
                activeDownloads.set(d.id, {
                    id: d.id, file_name: d.file_name || '', status: d.status,
                    downloaded: d.downloaded || 0, total: d.total || 0,
                    speed: 0, eta: 0, progress: d.progress || 0, error: d.error || '',
                });
                any = true;
            }
        }
        if (any) refreshTasksArea();
    } catch (_) { /* 拉不到就算了，不影响新下载 */ }
    finally { restoring = false; }
}

function openDownload() {
    if (!overlay) return;
    const content = overlay.querySelector('.settings-content');
    content.innerHTML = `
        <div class="settings-header">
            <h2>下载模型</h2>
            <button class="modal-close" id="dl-close">&times;</button>
        </div>
        <div class="dl-body">
            <div class="dl-step" id="dl-step-url">
                <div class="dl-step-title">粘贴 CivitAI 或 HuggingFace 链接</div>
                <div class="dl-url-row">
                    <input type="text" id="dl-url" class="settings-input" placeholder="civitai.com/models/... 或 civitai.red / huggingface.co/user/repo">
                    <button class="btn btn-primary" id="dl-fetch-btn">获取</button>
                </div>
                <div class="dl-hint">支持 CivitAI 模型页（civitai.com / civitai.red）和 HuggingFace repo 链接</div>
            </div>
            <div class="dl-step" id="dl-step-version" style="display:none"></div>
            <div id="dl-tasks-area">${renderDownloadTasks()}</div>
        </div>
    `;

    content.querySelector('#dl-close').addEventListener('click', closeDownload);
    content.querySelector('#dl-fetch-btn').addEventListener('click', fetchVersions);
    content.querySelector('#dl-url').addEventListener('keydown', e => {
        if (e.key === 'Enter') fetchVersions();
    });

    bindTaskActions(content);
    overlay.classList.add('show');
    setTimeout(() => content.querySelector('#dl-url')?.focus(), 100);
}

async function fetchVersions() {
    const input = document.getElementById('dl-url');
    const btn = document.getElementById('dl-fetch-btn');
    const url = input?.value.trim();
    if (!url) return;

    btn.disabled = true;
    btn.textContent = '获取中...';

    const isHF = /huggingface\.co/i.test(url);

    try {
        const res = isHF ? await api.fetchHfFiles(url) : await api.fetchCivitaiVersions(url);
        if (!res.success) {
            showToast(res.error || '获取失败', 'error');
            btn.disabled = false;
            btn.textContent = '获取';
            return;
        }

        if (isHF) {
            renderHfFilesStep(res);
        } else {
            renderVersionStep(res);
        }
    } catch (e) {
        showToast('请求失败: ' + e.message, 'error');
        btn.disabled = false;
        btn.textContent = '获取';
    }
}

function renderHfFilesStep(data) {
    const step = document.getElementById('dl-step-version');
    document.getElementById('dl-step-url').style.display = 'none';
    step.style.display = 'block';

    const dirs = state.settings.model_roots || [];
    const defaultDir = guessDefaultDir(dirs, guessHfModelType(data), data.base_model);

    if (!data.files || data.files.length === 0) {
        step.innerHTML = `
            <div class="dl-model-header">
                <div class="dl-model-name">${esc(data.model_name)}</div>
                <div class="dl-model-meta">HuggingFace · ${esc(data.author)}</div>
            </div>
            <div class="dl-empty">该 repo 中未找到模型权重文件</div>
            <div class="dl-actions">
                <button class="btn" id="dl-back-btn">返回</button>
            </div>
        `;
        step.querySelector('#dl-back-btn').addEventListener('click', backToUrl);
        return;
    }

    step.innerHTML = `
        <div class="dl-model-header">
            <div class="dl-model-name">${esc(data.model_name)}</div>
            <div class="dl-model-meta">HuggingFace · ${esc(data.author)}${data.base_model && data.base_model !== 'Unknown' ? ' · ' + esc(data.base_model) : ''}</div>
        </div>
        <div class="dl-section-title">选择要下载的文件</div>
        <div class="dl-version-list">
            ${data.files.map((f, i) => `
                <label class="dl-version-item${i === 0 ? ' selected' : ''}">
                    <input type="radio" name="dl-hf-file" value="${i}" ${i === 0 ? 'checked' : ''}>
                    <div class="dl-version-info">
                        <div class="dl-version-name">${esc(f.file_name)}</div>
                        <div class="dl-version-meta">${formatSize(f.file_size)}</div>
                    </div>
                </label>
            `).join('')}
        </div>
        <div class="dl-section-title">保存目录</div>
        <select id="dl-save-dir" class="settings-select dl-dir-select">
            ${dirs.map(d => `<option value="${esc(d)}"${d === defaultDir ? ' selected' : ''}>${esc(d)}</option>`).join('')}
            ${dirs.length === 0 ? '<option value="">未配置模型目录</option>' : ''}
        </select>
        <div class="dl-actions">
            <button class="btn" id="dl-back-btn">返回</button>
            <button class="btn btn-primary" id="dl-start-btn" ${dirs.length === 0 ? 'disabled' : ''}>开始下载</button>
        </div>
    `;

    step.querySelectorAll('input[name="dl-hf-file"]').forEach(radio => {
        radio.addEventListener('change', () => {
            step.querySelectorAll('.dl-version-item').forEach(el => el.classList.remove('selected'));
            radio.closest('.dl-version-item').classList.add('selected');
        });
    });

    step.querySelector('#dl-back-btn').addEventListener('click', backToUrl);
    step.querySelector('#dl-start-btn').addEventListener('click', () => {
        const idx = parseInt(step.querySelector('input[name="dl-hf-file"]:checked')?.value || '0');
        const file = data.files[idx];
        const saveDir = step.querySelector('#dl-save-dir').value;
        if (!file || !saveDir) return;
        startDownload({
            download_url: file.download_url,
            file_name: file.file_name.split('/').pop(),
            file_size: file.file_size,
            version_id: null,
        }, saveDir);
    });
}

function guessHfModelType(data) {
    const haystack = (data.base_model + ' ' + (data.tags || []).join(' ') + ' ' + data.model_name).toLowerCase();
    if (haystack.includes('lora')) return 'LORA';
    if (haystack.includes('vae')) return 'VAE';
    if (haystack.includes('controlnet')) return 'ControlNet';
    if (haystack.includes('embedding') || haystack.includes('textual')) return 'TextualInversion';
    return 'Checkpoint';
}

function backToUrl() {
    const step = document.getElementById('dl-step-version');
    step.style.display = 'none';
    document.getElementById('dl-step-url').style.display = 'block';
    document.getElementById('dl-fetch-btn').disabled = false;
    document.getElementById('dl-fetch-btn').textContent = '获取';
}

function renderVersionStep(data) {
    const step = document.getElementById('dl-step-version');
    document.getElementById('dl-step-url').style.display = 'none';
    step.style.display = 'block';

    const dirs = state.settings.model_roots || [];
    const defaultDir = guessDefaultDir(dirs, data.model_type, data.versions?.[0]?.base_model);

    step.innerHTML = `
        <div class="dl-model-header">
            <div class="dl-model-name">${esc(data.model_name)}</div>
            <div class="dl-model-meta">${esc(data.model_type)} · ${esc(data.creator)}</div>
        </div>
        <div class="dl-section-title">选择版本</div>
        <div class="dl-version-list">
            ${data.versions.map((v, i) => `
                <label class="dl-version-item${i === 0 ? ' selected' : ''}">
                    <input type="radio" name="dl-version" value="${i}" ${i === 0 ? 'checked' : ''}>
                    <div class="dl-version-info">
                        <div class="dl-version-name">${esc(v.version_name)}</div>
                        <div class="dl-version-meta">
                            ${esc(v.base_model)} · ${formatSize(v.file_size)} · ${esc(v.file_name)}
                            ${v.published_at ? ' · ' + v.published_at.substring(0, 10) : ''}
                        </div>
                    </div>
                    ${v.preview_url ? `<img src="${esc(previewUrl(v.preview_url))}" class="dl-version-thumb">` : ''}
                </label>
            `).join('')}
        </div>
        <div class="dl-section-title">保存目录</div>
        <select id="dl-save-dir" class="settings-select dl-dir-select">
            ${dirs.map(d => `<option value="${esc(d)}"${d === defaultDir ? ' selected' : ''}>${esc(d)}</option>`).join('')}
            ${dirs.length === 0 ? '<option value="">未配置模型目录</option>' : ''}
        </select>
        <div class="dl-actions">
            <button class="btn" id="dl-back-btn">返回</button>
            <button class="btn btn-primary" id="dl-start-btn" ${dirs.length === 0 ? 'disabled' : ''}>开始下载</button>
        </div>
    `;

    step.querySelectorAll('input[name="dl-version"]').forEach(radio => {
        radio.addEventListener('change', () => {
            step.querySelectorAll('.dl-version-item').forEach(el => el.classList.remove('selected'));
            radio.closest('.dl-version-item').classList.add('selected');
        });
    });

    step.querySelector('#dl-back-btn').addEventListener('click', () => {
        step.style.display = 'none';
        document.getElementById('dl-step-url').style.display = 'block';
        document.getElementById('dl-fetch-btn').disabled = false;
        document.getElementById('dl-fetch-btn').textContent = '获取';
    });

    step.querySelector('#dl-start-btn').addEventListener('click', () => {
        const idx = parseInt(step.querySelector('input[name="dl-version"]:checked')?.value || '0');
        const version = data.versions[idx];
        const saveDir = step.querySelector('#dl-save-dir').value;
        if (!version || !saveDir) return;
        startDownload(version, saveDir);
    });
}

async function startDownload(version, saveDir) {
    try {
        const res = await api.downloadModel(version.download_url, saveDir, version.file_name, version.version_id, version.sha256 || '');
        if (!res.success) {
            showToast('下载启动失败: ' + (res.error || ''), 'error');
            return;
        }

        activeDownloads.set(res.download_id, {
            id: res.download_id,
            file_name: version.file_name,
            status: 'queued',
            downloaded: 0,
            total: version.file_size || 0,
            speed: 0,
            eta: 0,
            progress: 0,
        });

        showToast(`下载已开始: ${version.file_name}`, 'info');

        // 回到 URL 输入步骤，允许添加更多下载
        const stepVersion = document.getElementById('dl-step-version');
        const stepUrl = document.getElementById('dl-step-url');
        if (stepVersion) stepVersion.style.display = 'none';
        if (stepUrl) {
            stepUrl.style.display = 'block';
            const urlInput = document.getElementById('dl-url');
            const fetchBtn = document.getElementById('dl-fetch-btn');
            if (urlInput) urlInput.value = '';
            if (fetchBtn) { fetchBtn.disabled = false; fetchBtn.textContent = '获取'; }
        }

        refreshTasksArea();
    } catch (e) {
        showToast('下载出错: ' + e.message, 'error');
    }
}

function handleDownloadProgress(msg) {
    const dl = activeDownloads.get(msg.download_id);
    if (!dl) {
        // 未在本地登记的下载(图库/扩展/配方批量等不经本弹窗发起的)：从 /downloads 拉回纳入任务条，
        // 之后的 WS 进度事件就能正常更新。restoring 标志防高频事件重复拉取。
        restoreDownloads();
        return;
    }

    const prevStatus = dl.status;
    if (msg.status) {
        dl.status = msg.status;
    } else {
        dl.status = 'downloading';
    }

    if (msg.downloaded !== undefined) dl.downloaded = msg.downloaded;
    if (msg.total !== undefined) dl.total = msg.total;
    if (msg.speed !== undefined) dl.speed = msg.speed;
    if (msg.eta !== undefined) dl.eta = msg.eta;
    if (msg.progress !== undefined) dl.progress = msg.progress;
    if (msg.error) dl.error = msg.error;

    if (msg.status === 'complete') {
        dl.progress = 100;
        showToast(`下载完成: ${dl.file_name}`, 'success');
        loadModels();
        window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
        window.dispatchEvent(new Event('noctyra-refresh-tabs'));
        setTimeout(() => {
            activeDownloads.delete(msg.download_id);
            refreshTasksArea();
        }, 5000);
    } else if (msg.status === 'error') {
        showToast(`下载失败: ${dl.file_name} — ${msg.error || ''}`, 'error');
    } else if (msg.status === 'cancelled') {
        // 保留任务条(带重试/移除按钮),用户自行清理,与 error/interrupted 一致
        showToast(`下载已取消: ${dl.file_name}`, 'info');
    }

    // 状态切换 → 整体重渲染(按钮组随状态变);纯进度更新 → 增量,避免闪烁
    if (dl.status !== prevStatus) refreshTasksArea();
    else updateTaskUI(msg.download_id);
}

function renderDownloadTasks() {
    if (activeDownloads.size === 0) return '';

    let html = '<div class="dl-tasks-header">下载任务</div>';
    for (const [id, dl] of activeDownloads) {
        html += renderSingleTask(id, dl);
    }
    return html;
}

function renderSingleTask(id, dl) {
    const statusLabel = DL_TASK_LABELS[dl.status] || dl.status;

    const statusClass = dl.status === 'complete' ? 'dl-status-ok'
        : dl.status === 'paused' ? 'dl-status-paused'
        : (dl.status === 'error' || dl.status === 'cancelled' || dl.status === 'interrupted') ? 'dl-status-err'
        : '';

    const speedText = dl.speed > 0 ? formatSize(dl.speed) + '/s' : '';
    const etaText = dl.eta > 0 ? formatEta(dl.eta) : '';
    const sizeText = dl.total > 0 ? `${formatSize(dl.downloaded)} / ${formatSize(dl.total)}` : '';

    const barCls = dl.status === 'paused' ? ' dl-bar-paused'
        : (dl.status === 'error' || dl.status === 'cancelled' || dl.status === 'interrupted') ? ' dl-bar-err' : '';

    return `
        <div class="dl-task" data-dl-id="${esc(id)}">
            <div class="dl-task-top">
                <span class="dl-task-name" title="${esc(dl.file_name)}">${esc(dl.file_name)}</span>
                <span class="dl-task-status ${statusClass}">${statusLabel}</span>
            </div>
            <div class="dl-progress-bar-wrap${barCls}">
                <div class="dl-progress-bar" style="width:${dl.progress}%"></div>
            </div>
            <div class="dl-task-bottom">
                <span class="dl-task-size">${sizeText}</span>
                <span class="dl-task-speed">${speedText}</span>
                <span class="dl-task-eta">${etaText}</span>
                <span class="dl-task-acts">${dlTaskButtons(id, dl.status)}</span>
            </div>
        </div>
    `;
}

function refreshTasksArea() {
    const area = document.getElementById('dl-tasks-area');
    if (!area) return;
    area.innerHTML = renderDownloadTasks();
    bindTaskActions(area);
}

function bindTaskActions(container) {
    container.querySelectorAll('.tc-act-btn[data-dl-act]').forEach(btn => {
        btn.addEventListener('click', e => {
            e.stopPropagation();
            runDownloadAction(btn.dataset.dlAct, btn.dataset.dlId, btn);
        });
    });
}

function updateTaskUI(downloadId) {
    const taskEl = document.querySelector(`.dl-task[data-dl-id="${downloadId}"]`);
    const dl = activeDownloads.get(downloadId);
    if (!taskEl || !dl) {
        refreshTasksArea();
        return;
    }

    const bar = taskEl.querySelector('.dl-progress-bar');
    if (bar) bar.style.width = `${dl.progress}%`;

    // updateTaskUI 只在状态未变(纯进度更新)时调用;状态切换由 handleDownloadProgress
    // 走 refreshTasksArea 整体重渲染(含按钮组),故这里只更新进度/速度/大小等数值
    const sizeEl = taskEl.querySelector('.dl-task-size');
    if (sizeEl && dl.total > 0) sizeEl.textContent = `${formatSize(dl.downloaded)} / ${formatSize(dl.total)}`;

    const speedEl = taskEl.querySelector('.dl-task-speed');
    if (speedEl) speedEl.textContent = dl.speed > 0 ? formatSize(dl.speed) + '/s' : '';

    const etaEl = taskEl.querySelector('.dl-task-eta');
    if (etaEl) etaEl.textContent = dl.eta > 0 ? formatEta(dl.eta) : '';
}

function closeDownload() {
    if (overlay) overlay.classList.remove('show');
}

function formatEta(seconds) {
    if (seconds <= 0) return '';
    if (seconds < 60) return `${Math.ceil(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.ceil(seconds % 60)}s`;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
}

const TYPE_DIR_MAP = {
    'Checkpoint': ['checkpoint'],
    'LORA': ['lora'],
    'LoCon': ['lora'],
    'DoRA': ['lora'],
    'LyCORIS': ['lora'],
    'TextualInversion': ['embedding', 'textual_inversion'],
    'VAE': ['vae'],
    'ControlNet': ['controlnet'],
    'Upscaler': ['upscale', 'esrgan'],
    'AestheticGradient': ['lora'],
    'Hypernetwork': ['hypernetwork'],
    'TextEncoder': ['text_encoders', 'text_encoder'],
    'CLIPVision': ['clip_vision'],
    'MotionModule': ['animatediff_models', 'motion'],
    'Detection': ['ultralytics', 'detection'],
    // 内部 key（经视频 UNet 覆盖后可能变成这个）
    'unet': ['unet', 'diffusion_model'],
};

// CivitAI/HF model_type -> 内部配置 key（用于 default_roots 查找）
const TYPE_TO_INTERNAL = {
    'Checkpoint': 'checkpoint',
    'LORA': 'lora',
    'LoCon': 'lora',
    'DoRA': 'lora',
    'LyCORIS': 'lora',
    'AestheticGradient': 'lora',
    'TextualInversion': 'embedding',
    'VAE': 'vae',
    'ControlNet': 'controlnet',
};

/**
 * 视频模型 / 纯 transformer 的 Checkpoint 强制走 unet。
 * 与后端 _apply_diffusion_override 对齐（config.diffusion_model_base_models）。
 * 只对 Checkpoint 类型生效；modelType 返回内部 key（可能是 'unet'）或原串。
 */
function applyDiffusionOverride(modelType, baseModel) {
    if (modelType !== 'Checkpoint' || !baseModel) return modelType;
    const list = state.settings?.diffusion_model_base_models || [];
    const bm = String(baseModel).trim().toLowerCase();
    for (const entry of list) {
        const needle = String(entry || '').trim().toLowerCase();
        if (needle && bm.includes(needle)) return 'unet';
    }
    return modelType;
}

function guessDefaultDir(dirs, modelType, baseModel) {
    if (!modelType || dirs.length === 0) return dirs[0] || '';
    const effective = applyDiffusionOverride(modelType, baseModel || '');

    // 1. 用户在设置里配置的"每类型默认目录"优先
    const internalKey = effective === 'unet' ? 'unet' : TYPE_TO_INTERNAL[effective];
    const configured = internalKey && state.settings?.default_roots?.[internalKey];
    if (configured && dirs.includes(configured)) return configured;

    // 2. 回退：按目录名关键字匹配
    const keywords = TYPE_DIR_MAP[effective];
    if (!keywords) return dirs[0];
    for (const kw of keywords) {
        const match = dirs.find(d => d.toLowerCase().includes(kw));
        if (match) return match;
    }
    return dirs[0];
}

