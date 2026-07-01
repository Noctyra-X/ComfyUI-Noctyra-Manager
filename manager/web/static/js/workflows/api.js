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
 * 工作流图库 API 客户端 —— 从 workflows/app.js 拆出。
 * 纯 HTTP 调用，无 DOM / 无全局状态。
 */

import { API_BASE } from './state.js';


export async function apiFetch(url) {
    const resp = await fetch(`${API_BASE}/fetch-civitai-image`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
    });
    return resp.json();
}

export async function apiSave(imageUrl, imageInfo, forceUpdate = false) {
    const resp = await fetch(`${API_BASE}/save-civitai-image`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_url: imageUrl, image_info: imageInfo, force_update: forceUpdate }),
    });
    return resp.json();
}

export async function apiGalleryList(page = 1, search = '', pageSize = 40,
                                      tag = '', favoriteOnly = false,
                                      resourcesFilter = '', workflowOnly = false,
                                      format = '', media = '', folder = '') {
    const params = new URLSearchParams({ page, page_size: pageSize });
    if (search) params.set('search', search);
    if (tag) params.set('tag', tag);
    if (favoriteOnly) params.set('favorite', '1');
    if (resourcesFilter) params.set('resources_filter', resourcesFilter);
    if (workflowOnly) params.set('workflow', '1');
    if (format) params.set('format', format);
    if (media) params.set('media', media);
    if (folder) params.set('folder', folder);
    const resp = await fetch(`${API_BASE}/gallery?${params}`);
    return resp.json();
}

// ===== Billfish 文件夹模型 =====
export async function apiGalleryFolders() {
    const resp = await fetch(`${API_BASE}/gallery-folders`);
    return resp.json();
}

export async function apiGalleryScan() {
    const resp = await fetch(`${API_BASE}/gallery-scan`, { method: 'POST' });
    return resp.json();
}

export async function apiGalleryFolderAdd(path, name = '') {
    const resp = await fetch(`${API_BASE}/gallery-folder/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, name }),
    });
    return resp.json();
}

export async function apiGalleryFolderRemove(path) {
    const resp = await fetch(`${API_BASE}/gallery-folder/remove`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
    });
    return resp.json();
}

export async function apiGalleryTags() {
    const resp = await fetch(`${API_BASE}/gallery-tags`);
    return resp.json();
}

export async function apiGalleryFormats() {
    const resp = await fetch(`${API_BASE}/gallery-formats`);
    return resp.json();
}

export async function apiGalleryDelete(id, deleteFile = true) {
    const resp = await fetch(`${API_BASE}/gallery/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, delete_file: deleteFile }),
    });
    return resp.json();
}

export async function apiCheckResources(resources) {
    const resp = await fetch(`${API_BASE}/check-resources`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resources }),
    });
    return resp.json();
}

export async function apiImportLocal(file) {
    const fd = new FormData();
    fd.append('file', file);
    const resp = await fetch(`${API_BASE}/import-local`, { method: 'POST', body: fd });
    return resp.json();
}

export async function apiUpdateInfo(id, data) {
    const resp = await fetch(`${API_BASE}/update-info`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, ...data }),
    });
    return resp.json();
}

export async function apiDownloadModel(modelId, versionId) {
    const resp = await fetch(`/api/noctyra/extension/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: modelId, version_id: versionId }),
    });
    return resp.json();
}
