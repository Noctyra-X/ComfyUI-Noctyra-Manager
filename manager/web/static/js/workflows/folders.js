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
 * 图库文件夹树侧边栏（Billfish 文件夹模型）。
 * 自洽模块：拉取注册文件夹树、渲染、点选过滤、扫描、增删注册文件夹。
 * 通过 init 的回调与 app.js 解耦（onSelect 切换图库过滤、toast 提示）。
 */

import { escapeHtml, escapeAttr } from '../utils.js';
import {
    apiGalleryFolders, apiGalleryScan,
    apiGalleryFolderAdd, apiGalleryFolderRemove,
} from './api.js';
import { showPrompt, showConfirm } from '../components/dialog.js';

let _onSelect = null;        // (path) => void：切换图库到该文件夹
let _toast = () => {};       // (msg, type) => void
let _currentFolder = '';     // 当前选中文件夹路径（''=全部）
const _expanded = new Set(); // 展开的文件夹路径
let _foldersCache = [];      // 最近一次后端返回的文件夹树，展开/折叠纯本地重渲染用（不重拉后端）

export function getCurrentFolder() {
    return _currentFolder;
}

export function initFolderSidebar({ onSelect, toast } = {}) {
    _onSelect = onSelect || (() => {});
    _toast = toast || (() => {});

    document.getElementById('wf-scan-btn')?.addEventListener('click', doScan);
    document.getElementById('wf-folder-add-btn')?.addEventListener('click', doAddFolder);

    const tree = document.getElementById('wf-folder-tree');
    if (tree) tree.addEventListener('click', onTreeClick);

    loadFolders();
}

let _scanPollTimer = null;

export async function loadFolders() {
    const tree = document.getElementById('wf-folder-tree');
    if (!tree) return;
    try {
        const res = await apiGalleryFolders();
        if (!res || !res.success) return;
        _applyFolders(res);
    } catch (e) {
        console.warn('loadFolders failed:', e);
    }
}

// 渲染文件夹树 + 根据后端扫描状态恢复"扫描中"（刷新页面后也能接上正在跑的扫描）
function _applyFolders(res) {
    const tree = document.getElementById('wf-folder-tree');
    if (tree) renderTree(tree, res.folders || []);
    const btn = document.getElementById('wf-scan-btn');
    if (res.scanning) {
        if (btn) { btn.disabled = true; btn.textContent = '扫描中…'; }
        _startScanPoller();
    } else if (btn && btn.textContent === '扫描中…') {
        btn.disabled = false; btn.textContent = '扫描';
    }
}

// 轮询后端扫描状态，完成后复位按钮 + 刷新树 + 重载图库
function _startScanPoller() {
    if (_scanPollTimer) return;
    _scanPollTimer = setInterval(async () => {
        let res;
        try { res = await apiGalleryFolders(); } catch (_) { return; }
        if (!res || !res.success || res.scanning) return;   // 还在扫 → 继续等
        clearInterval(_scanPollTimer); _scanPollTimer = null;
        const tree = document.getElementById('wf-folder-tree');
        if (tree) renderTree(tree, res.folders || []);
        const btn = document.getElementById('wf-scan-btn');
        if (btn) { btn.disabled = false; btn.textContent = '扫描'; }
        const ls = res.last_scan;
        if (ls && !ls.error) _toast(`扫描完成：新增 ${ls.added}，清理 ${ls.pruned}`, 'success');
        else if (ls && ls.error) _toast('扫描失败：' + ls.error, 'error');
        _onSelect(_currentFolder);   // 重载图库
    }, 2000);
}

// 用缓存的文件夹树本地重渲染（展开/折叠时用，不触后端）
function rerenderTree() {
    const tree = document.getElementById('wf-folder-tree');
    if (tree) renderTree(tree, _foldersCache);
}

function renderTree(container, folders) {
    _foldersCache = folders;   // 记下最新数据，供展开/折叠本地重渲染复用
    let html = `<div class="wf-folder-node wf-folder-all${_currentFolder === '' ? ' active' : ''}" data-path="" data-act="select">
        <span class="wf-folder-caret-empty"></span>
        <span class="wf-folder-name">全部</span>
    </div>`;
    html += folders.map(f => folderNodeHtml(f, 0)).join('');
    container.innerHTML = html;
}

function folderNodeHtml(node, depth) {
    const kids = node.children || [];
    const hasKids = kids.length > 0;
    const exp = _expanded.has(node.path);
    const active = _currentFolder === node.path;
    const caret = hasKids
        ? `<span class="wf-folder-caret${exp ? ' open' : ''}" data-act="toggle" data-path="${escapeAttr(node.path)}">▸</span>`
        : '<span class="wf-folder-caret-empty"></span>';
    const rm = node.builtin
        ? ''
        : `<button class="wf-folder-rm" data-act="remove" data-path="${escapeAttr(node.path)}" title="取消注册（磁盘文件不动）">×</button>`;
    let html = `<div class="wf-folder-node${active ? ' active' : ''}" data-path="${escapeAttr(node.path)}" data-act="select" style="padding-left:${depth * 14 + 8}px" title="${escapeAttr(node.path)}">
        ${caret}
        <span class="wf-folder-name">${escapeHtml(node.name)}</span>
        <span class="wf-folder-count">${node.count || 0}</span>
        ${rm}
    </div>`;
    if (hasKids && exp) {
        html += `<div class="wf-folder-children">${kids.map(c => folderNodeHtml(c, depth + 1)).join('')}</div>`;
    }
    return html;
}

function onTreeClick(e) {
    const caret = e.target.closest('[data-act="toggle"]');
    if (caret) {
        e.stopPropagation();
        const p = caret.dataset.path;
        if (_expanded.has(p)) _expanded.delete(p); else _expanded.add(p);
        // 展开/折叠纯 UI：只切本地 _expanded + 用缓存重渲染，不重拉后端/不打全表 SELECT/不 scandir
        rerenderTree();
        return;
    }
    const rm = e.target.closest('[data-act="remove"]');
    if (rm) {
        e.stopPropagation();
        doRemoveFolder(rm.dataset.path);
        return;
    }
    const node = e.target.closest('[data-act="select"]');
    if (node) {
        selectFolder(node.dataset.path || '');
    }
}

function selectFolder(path) {
    if (_currentFolder === path) return;
    _currentFolder = path;
    // 只刷新 active 态，避免整树重渲染丢展开动画
    const tree = document.getElementById('wf-folder-tree');
    tree?.querySelectorAll('.wf-folder-node').forEach(n => {
        n.classList.toggle('active', (n.dataset.path || '') === path);
    });
    _onSelect(path);
}

async function doScan() {
    const btn = document.getElementById('wf-scan-btn');
    try {
        const res = await apiGalleryScan();   // 后台跑，立即返回 started/running
        if (res && res.success) {
            if (btn) { btn.disabled = true; btn.textContent = '扫描中…'; }
            _startScanPoller();               // 轮询到完成
        } else {
            _toast('扫描失败', 'error');
        }
    } catch (e) {
        _toast('扫描失败：' + (e.message || '网络错误'), 'error');
    }
}

async function doAddFolder() {
    const path = await showPrompt({
        title: '注册文件夹',
        message: '输入要注册的文件夹绝对路径\n（如 ComfyUI 的 output 目录、存放下载图的目录等；原地索引，不拷贝）',
        placeholder: 'E:\\ComfyUI\\output',
        okText: '添加',
    });
    if (!path || !path.trim()) return;
    try {
        const res = await apiGalleryFolderAdd(path.trim());
        if (res && res.success) {
            _toast('已添加，开始扫描…', 'success');
            await loadFolders();
            await doScan();   // 加完即扫，立刻看到内容
        } else {
            _toast(res && res.error ? res.error : '添加失败', 'error');
        }
    } catch (e) {
        _toast('添加失败：' + (e.message || '网络错误'), 'error');
    }
}

async function doRemoveFolder(path) {
    if (!path) return;
    if (!await showConfirm({
        title: '取消注册',
        message: '取消注册此文件夹？\n只移除图库索引记录，磁盘上的文件不会被删除。',
        okText: '取消注册',
        danger: true,
    })) return;
    try {
        const res = await apiGalleryFolderRemove(path);
        if (res && res.success) {
            _toast(`已移除（清理 ${res.removed_records} 条记录）`, 'success');
            // 若当前正看着被移除的文件夹，退回「全部」
            if (_currentFolder === path || _currentFolder.startsWith(path)) {
                _currentFolder = '';
                _onSelect('');
            }
            await loadFolders();
        } else {
            _toast(res && res.error ? res.error : '移除失败', 'error');
        }
    } catch (e) {
        _toast('移除失败：' + (e.message || '网络错误'), 'error');
    }
}
