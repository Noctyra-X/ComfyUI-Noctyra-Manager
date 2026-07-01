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
 * 文件夹侧边栏 — 树形层级 + 展开/折叠 + 拖拽移动
 */
import { state } from '../state.js';
import * as api from '../api.js';
import { loadModels } from './card-grid.js';
import { showToast, showOpError } from './toast.js';
import { escapeHtml, escapeAttr } from '../utils.js';

let sidebarEl = null;

export function initSidebar() {
    sidebarEl = document.getElementById('sidebar-folders');

    // 持久化展开状态
    try {
        const saved = JSON.parse(sessionStorage.getItem('noctyra_expanded_folders') || '[]');
        state.expandedFolders = new Set(saved);
    } catch { /* ignore */ }

    // 仅重渲染侧栏（软删计数/高亮变化时由 updateTypeTabs 派发）——
    // 用独立事件，避免与 app.js 的 noctyra-refresh-sidebar(会重载数据+updateTypeTabs)循环
    window.addEventListener('noctyra-render-sidebar', () => renderFolders());
}

export async function loadFolders() {
    const res = await api.fetchFolders();
    if (!res.success) return;
    state.folders = res.folders;
    renderFolders();
}

function buildTree(folders) {
    // folders: [{folder: "loras/sd15", count: 5}, ...]
    const root = { name: '', path: '', count: 0, ownCount: 0, children: new Map() };

    for (const f of folders) {
        const path = f.folder || '';
        if (!path) {
            root.ownCount += f.count;
            continue;
        }
        const parts = path.split('/');
        let node = root;
        let acc = '';
        for (let i = 0; i < parts.length; i++) {
            acc = acc ? `${acc}/${parts[i]}` : parts[i];
            if (!node.children.has(parts[i])) {
                node.children.set(parts[i], {
                    name: parts[i],
                    path: acc,
                    count: 0,
                    ownCount: 0,
                    children: new Map(),
                });
            }
            node = node.children.get(parts[i]);
            if (i === parts.length - 1) node.ownCount = f.count;
        }
    }

    // 聚合每个节点的总数（own + 所有后代）
    function aggregate(node) {
        let sum = node.ownCount;
        for (const child of node.children.values()) {
            sum += aggregate(child);
        }
        node.count = sum;
        return sum;
    }
    aggregate(root);
    return root;
}

const ICON_FOLDER = '<svg class="folder-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
const ICON_FILE = '<svg class="folder-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/></svg>';
const ICON_ALL = '<svg class="folder-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/><path d="M2 10h20"/></svg>';
const ICON_TRASH = '<svg class="folder-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';

function syncSourceSelect(val) {
    // 来源筛选已改胶囊：通过事件让 filters.js 同步胶囊 + 已选条（caller 自己重载列表）
    window.dispatchEvent(new CustomEvent('noctyra-set-filter', { detail: { cat: 'source', value: val, reload: false } }));
}

function renderNode(node, depth) {
    const hasChildren = node.children.size > 0;
    const isExpanded = state.expandedFolders.has(node.path);
    const isActive = state.currentFolder === node.path;

    const chevron = hasChildren
        ? `<span class="folder-chevron${isExpanded ? ' expanded' : ''}"><svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5l10 7-10 7z"/></svg></span>`
        : '<span class="folder-chevron-spacer"></span>';

    let html = `
        <div class="folder-item${isActive ? ' active' : ''}" data-folder="${escapeAttr(node.path)}" style="padding-left:${10 + depth * 14}px" title="${escapeAttr(node.path)}">
            ${chevron}
            ${hasChildren ? ICON_FOLDER : ICON_FILE}
            <span class="folder-name">${escapeHtml(node.name)}</span>
            <span class="folder-count">${node.count}</span>
        </div>
    `;

    if (hasChildren && isExpanded) {
        const sortedChildren = [...node.children.values()].sort((a, b) => a.name.localeCompare(b.name));
        for (const child of sortedChildren) {
            html += renderNode(child, depth + 1);
        }
    }

    return html;
}

function renderFolders() {
    if (!sidebarEl) return;

    const tree = buildTree(state.folders);

    const inDeleted = state.currentSource === 'deleted';

    // "全部模型" 入口（已删除视图下不高亮）
    let html = `
        <div class="folder-item ${state.currentFolder === '' && !inDeleted ? 'active' : ''}" data-folder="">
            <span class="folder-chevron-spacer"></span>
            ${ICON_ALL}
            <span class="folder-name">全部模型</span>
            <span class="folder-count">${tree.count}</span>
        </div>
    `;

    // "已删除 / 留记录" 入口（有软删记录时才显示）—— 文件已删但保留了记录的模型
    if ((state.deletedCount || 0) > 0) {
        html += `
        <div class="folder-item folder-item-deleted ${inDeleted ? 'active' : ''}" data-special="deleted" title="文件已删除但保留了记录的模型，可重新下载">
            <span class="folder-chevron-spacer"></span>
            ${ICON_TRASH}
            <span class="folder-name">删除存档</span>
            <span class="folder-count">${state.deletedCount}</span>
        </div>
        `;
    }

    const topLevels = [...tree.children.values()].sort((a, b) => a.name.localeCompare(b.name));
    for (const node of topLevels) {
        html += renderNode(node, 0);
    }

    sidebarEl.innerHTML = html;
    attachHandlers();
}

function attachHandlers() {
    sidebarEl.querySelectorAll('.folder-item').forEach(el => {
        // "已删除 / 留记录" 特殊入口：切到软删视图（source=deleted），不参与展开/拖拽
        if (el.dataset.special === 'deleted') {
            el.addEventListener('click', () => {
                state.currentSource = 'deleted';
                state.currentFolder = '';
                syncSourceSelect('deleted');
                renderFolders();
                window.dispatchEvent(new Event('noctyra-refresh-tabs'));  // 类型 tab 改算软删
                loadModels();
            });
            return;
        }

        const folder = el.dataset.folder;

        el.addEventListener('click', () => {
            state.currentFolder = folder;
            // 点普通文件夹时离开"已删除"视图
            if (state.currentSource === 'deleted') {
                state.currentSource = '';
                syncSourceSelect('');
                window.dispatchEvent(new Event('noctyra-refresh-tabs'));
            }
            renderFolders();
            loadModels();
        });

        el.addEventListener('dblclick', () => {
            toggleExpand(folder);
        });

        el.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            el.classList.add('drag-over');
        });
        el.addEventListener('dragleave', () => el.classList.remove('drag-over'));
        el.addEventListener('drop', async (e) => {
            e.preventDefault();
            el.classList.remove('drag-over');
            const filePath = e.dataTransfer.getData('text/plain');
            const targetFolder = folder;
            if (!filePath) return;

            const res = await api.moveModel(filePath, targetFolder);
            if (res.success) {
                showToast('模型已移动', 'success');
                loadFolders();
                loadModels();
            } else {
                showOpError(res, '移动失败');
            }
        });
    });
}

function toggleExpand(path) {
    if (state.expandedFolders.has(path)) {
        state.expandedFolders.delete(path);
    } else {
        state.expandedFolders.add(path);
    }
    try {
        sessionStorage.setItem('noctyra_expanded_folders', JSON.stringify([...state.expandedFolders]));
    } catch { /* ignore */ }
    renderFolders();
}
