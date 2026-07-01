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
 * API 客户端
 */

const BASE = '/api/noctyra';

async function request(method, path, body = null, timeoutMs = 30000) {
    const opts = {
        method,
        headers: { 'Content-Type': 'application/json' },
    };
    if (body) opts.body = JSON.stringify(body);

    const controller = new AbortController();
    opts.signal = controller.signal;
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
        const res = await fetch(`${BASE}${path}`, opts);
        // HTTP 4xx/5xx：服务端可能返回 HTML 错误页（如 ComfyUI 未启动时 8188 的 404），
        // res.json() 会抛 SyntaxError，掩盖真实原因；先判 status
        if (!res.ok) {
            throw new Error(`HTTP ${res.status} ${res.statusText || ''}`);
        }
        // res.json() 不受 AbortController 约束，超大响应仍可能阻塞主线程；
        // 用一次 try/catch 把 JSON 解析错误包成更友好的消息
        try {
            return await res.json();
        } catch (parseErr) {
            throw new Error(`响应非 JSON（${parseErr.message || parseErr}）`);
        }
    } catch (err) {
        if (err.name === 'AbortError') {
            throw new Error(`请求超时（${timeoutMs}ms）`);
        }
        throw err;
    } finally {
        clearTimeout(timer);
    }
}

export async function fetchModels(params = {}) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
        if (v !== '' && v !== null && v !== undefined) qs.set(k, v);
    }
    return request('GET', `/models?${qs}`);
}

export async function fetchModelDetail(identifier) {
    return request('GET', `/models/${encodeURIComponent(identifier)}`);
}

export async function fetchFolders() {
    return request('GET', '/folders');
}

export async function fetchTags(limit = 50) {
    return request('GET', `/tags?limit=${limit}`);
}

export async function fetchBaseModels() {
    return request('GET', '/base-models');
}

export async function fetchBaseModelStats() {
    return request('GET', '/base-models/stats');
}

export async function refreshBaseModels() {
    return request('POST', '/base-models/refresh', null, 10000);
}

export async function fetchStatus(source = '') {
    return request('GET', '/status' + (source ? `?source=${encodeURIComponent(source)}` : ''));
}

export async function triggerScan(force = false) {
    // force=true：全量重扫（忽略增量跳过），重新判定每个模型的类型，修正历史误分类
    return request('POST', force ? '/scan?force=1' : '/scan', null, 10000);
}

export async function triggerMatch(rematch = false) {
    return request('POST', '/match', { rematch }, 10000);
}

export async function cancelMatch() {
    return request('POST', '/cancel-match', {});
}

export async function cancelPrewarm() {
    return request('POST', '/cancel-prewarm', {});
}

export async function fetchCivitaiVersions(url) {
    return request('POST', '/civitai-versions', { url });
}

export async function fetchHfFiles(url) {
    return request('POST', '/hf-files', { url });
}

export async function downloadModel(downloadUrl, saveDir, fileName, versionId = null, expectedSha256 = '') {
    return request('POST', '/download', { download_url: downloadUrl, save_dir: saveDir, file_name: fileName, version_id: versionId, expected_sha256: expectedSha256 }, 10000);
}

// 按 CivitAI model_id / version_id 启动下载（服务端自动算目录 + 取版本信息）
// 供版本管理弹窗"下载"按钮用，省去前端两步请求
export async function downloadByCivitaiRef(modelId, versionId) {
    return request('POST', '/extension/download', { model_id: modelId, version_id: versionId }, 30000);
}

export async function fetchDownloads() {
    return request('GET', '/downloads');
}

export async function cancelDownload(downloadId) {
    return request('POST', '/download/cancel', { download_id: downloadId });
}

export async function pauseDownload(downloadId) {
    return request('POST', '/download/pause', { download_id: downloadId });
}

export async function resumeDownload(downloadId) {
    return request('POST', '/download/resume', { download_id: downloadId });
}

export async function retryDownload(downloadId) {
    return request('POST', '/download/retry', { download_id: downloadId });
}

export async function removeDownload(downloadId) {
    return request('POST', '/download/remove', { download_id: downloadId });
}

export async function checkModelUpdates() {
    return request('GET', '/check-model-updates', null, 300000);
}

export async function matchSingle(filePath, source = '') {
    return request('POST', '/match-single', { file_path: filePath, source }, 120000);
}

export async function organizeSingle(filePath) {
    return request('POST', '/organize/single', { file_path: filePath }, 60000);
}

export async function checkUpdate() {
    return request('GET', '/check-update');
}

export async function bindSource(sha256, url) {
    return request('POST', '/bind', { sha256, url });
}

export async function toggleFavorite(filePath, favorite) {
    return request('POST', '/favorite', { file_path: filePath, favorite });
}

export async function updateNotes(filePath, notes) {
    return request('POST', '/notes', { file_path: filePath, notes });
}

export async function updateCustomInfo(identifier, fields) {
    return request('POST', '/custom', { identifier, fields });
}

export async function checkIntegrity(filePath) {
    return request('POST', '/check-integrity', { file_path: filePath }, 60000);
}

export async function redownloadModel(filePath) {
    return request('POST', '/redownload', { file_path: filePath }, 30000);
}

export async function setTags(filePath, tags) {
    return request('POST', '/tags/set', { file_path: filePath, tags });
}

export async function importFromPath(path, move = false) {
    return request('POST', '/import-path', { path, move }, 3600000);
}

export async function importUpload(file, onProgress = null) {
    const fd = new FormData();
    fd.append('filename', file.name);
    fd.append('file', file, file.name);

    // 用 XHR 以便拿到上传进度
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', `${BASE}/import-upload`);
        xhr.timeout = 3600000;
        xhr.upload.onprogress = (e) => {
            if (onProgress && e.lengthComputable) {
                onProgress(e.loaded, e.total);
            }
        };
        xhr.onload = () => {
            try {
                resolve(JSON.parse(xhr.responseText));
            } catch {
                resolve({ success: false, error: '响应解析失败' });
            }
        };
        xhr.onerror = () => reject(new Error('网络错误'));
        xhr.ontimeout = () => reject(new Error('上传超时'));
        xhr.onabort = () => reject(new Error('已取消'));
        xhr.send(fd);
    });
}

export async function uploadPreview(identifier, file) {
    const fd = new FormData();
    fd.append('identifier', identifier);
    fd.append('file', file);
    const res = await fetch(`${BASE}/preview-upload`, { method: 'POST', body: fd });
    return res.json();
}

export async function deleteModel(filePath, deleteFile = false) {
    return request('POST', '/delete', { file_path: filePath, delete_file: deleteFile });
}

export async function softDeleteModel(filePath) {
    return request('POST', '/soft-delete', { file_path: filePath });
}

export async function restoreModel(filePath) {
    return request('POST', '/restore', { file_path: filePath });
}

// 在系统文件管理器中高亮目标文件
export async function revealInExplorer(filePath) {
    return request('POST', '/reveal', { file_path: filePath });
}

// 批量操作（batchDelete / batchRefresh 已在下方声明过）
export async function batchTag(filePaths, tags) {
    return request('POST', '/batch-tag', { file_paths: filePaths, tags }, 60000);
}
export async function batchSetBaseModel(filePaths, baseModel) {
    return request('POST', '/batch-set-base-model', { file_paths: filePaths, base_model: baseModel }, 60000);
}
export async function batchMove(filePaths, targetFolder) {
    return request('POST', '/batch-move', { file_paths: filePaths, target_folder: targetFolder }, 120000);
}

export async function listFilterPresets() {
    return request('GET', '/filter-presets');
}

export async function saveFilterPreset(name, filters) {
    return request('POST', '/filter-presets', { name, filters });
}

export async function deleteFilterPreset(identifier) {
    const body = typeof identifier === 'number' ? { id: identifier } : { name: identifier };
    return request('POST', '/filter-presets/delete', body);
}

export async function getSettings() {
    return request('GET', '/settings');
}

export async function saveSettings(data) {
    return request('POST', '/settings', data);
}

export async function detectDirs() {
    return request('GET', '/settings/detect-dirs');
}

export async function triggerRebuild() {
    return request('POST', '/rebuild', null, 10000);
}

export async function cleanupPreviews(force = false) {
    return request('POST', '/cleanup-previews', { force }, 60000);
}

export async function getCacheStats() {
    return request('GET', '/cache-stats', null, 15000);
}

export async function clearThumbs() {
    return request('POST', '/clear-thumbs', {}, 60000);
}

export async function cleanupMissingWorkflowImages() {
    return request('POST', '/workflow/gallery/cleanup-missing', null, 30000);
}

export async function prewarmPreviews() {
    return request('POST', '/prewarm-previews', {}, 15000);
}

export async function organizePreview() {
    return request('GET', '/organize/preview', null, 30000);
}

export async function organizeExecute(moves) {
    return request('POST', '/organize/execute', { moves }, 120000);
}

export async function fetchDuplicates() {
    return request('GET', '/duplicates');
}

export async function batchDelete(filePaths, deleteFiles = false) {
    return request('POST', '/batch-delete', { file_paths: filePaths, delete_files: deleteFiles }, 120000);
}

export async function batchRefresh(filePaths) {
    return request('POST', '/batch-refresh', { file_paths: filePaths }, 600000);
}

export async function moveModel(filePath, targetFolder) {
    return request('POST', '/move', { file_path: filePath, target_folder: targetFolder });
}

export async function addTags(filePath, tags) {
    return request('POST', '/tags/add', { file_path: filePath, tags });
}

export async function removeTag(filePath, tag) {
    return request('POST', '/tags/remove', { file_path: filePath, tag });
}

export async function exportData() {
    return request('GET', '/export', null, 60000);
}

export async function importData(models, mode = 'merge') {
    return request('POST', '/import', { models, mode }, 120000);
}

/**
 * 1x1 透明 PNG，用于无预览图时让 CSS 背景占位图显示。
 */
export const TRANSPARENT_PX = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';

/**
 * 预览图代理 URL：通过后端缓存访问远程预览图
 * 无 URL 时返回透明像素，由 CSS 背景展示主题化占位图
 */
export function previewUrl(remoteUrl, size = '') {
    if (!remoteUrl) return TRANSPARENT_PX;
    // size='card' → 后端返回 480px WebP 缩略图（列表卡片用，省带宽/解码）；详情不带 size 拿原图
    const sizeQ = size ? `&size=${encodeURIComponent(size)}` : '';
    // sidecar://<id> → 后端本地预览路由；id 里可能含 path: 前缀，整体用 encodeURIComponent
    if (typeof remoteUrl === 'string' && remoteUrl.startsWith('sidecar://')) {
        const id = remoteUrl.slice('sidecar://'.length);
        return `${BASE}/local-preview?id=${encodeURIComponent(id)}${sizeQ}`;
    }
    return `${BASE}/preview?url=${encodeURIComponent(remoteUrl)}${sizeQ}`;
}

/**
 * WebSocket 连接管理
 */
let _ws = null;
let _wsListeners = {};
let _reconnectTimer = null;
let _reconnectDelay = 1000;
let _wasConnected = false;   // 区分首次连接 vs 重连

export function connectWebSocket() {
    if (_ws && _ws.readyState <= 1) return;

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}${BASE}/ws`;

    _ws = new WebSocket(url);
    _ws.onopen = () => {
        _reconnectDelay = 1000;
        // 重连成功（非首次）：断线期间的进度事件已丢，通知各组件重新拉状态补齐
        if (_wasConnected) {
            window.dispatchEvent(new Event('noctyra-ws-reconnected'));
        }
        _wasConnected = true;
    };
    _ws.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            const event = msg.event;
            if (event && _wsListeners[event]) {
                for (const fn of _wsListeners[event]) {
                    fn(msg);
                }
            }
        } catch { /* ignore parse errors */ }
    };
    _ws.onclose = () => {
        _scheduleReconnect();
    };
    _ws.onerror = () => {
        _ws.close();
    };
}

function _scheduleReconnect() {
    if (_reconnectTimer) return;
    _reconnectTimer = setTimeout(() => {
        _reconnectTimer = null;
        _reconnectDelay = Math.min(_reconnectDelay * 1.5, 10000);
        connectWebSocket();
    }, _reconnectDelay);
}

export function onWsEvent(event, callback) {
    if (!_wsListeners[event]) _wsListeners[event] = [];
    _wsListeners[event].push(callback);
}

export function offWsEvent(event, callback) {
    if (!_wsListeners[event]) return;
    _wsListeners[event] = _wsListeners[event].filter(fn => fn !== callback);
}
