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
 * 重复模型检测弹窗 —— 精简版。
 *
 * 核心操作只有一个：「保留一个，删掉其余多余文件」。重复 = 硬盘上 SHA256 相同的多个真实
 * 文件，数据库只是索引——删文件才能真正去重腾空间，删记录文件还在、扫描后又回来，故不再
 * 提供「移除记录」。每组保留排在最前的一个（get_duplicates 按 file_name 排序，结果稳定）。
 */
import * as api from '../api.js';
const { previewUrl } = api;
import { showToast, showOpError } from './toast.js';
import { showConfirm } from './dialog.js';
import { loadModels } from './card-grid.js';
import { escapeHtml as escHtml, escapeAttr as escAttr, formatSize } from '../utils.js';

export function initDuplicates() {
    const btn = document.getElementById('btn-duplicates');
    if (btn) btn.addEventListener('click', openDuplicates);
}

async function openDuplicates() {
    showToast('正在查找重复模型...', 'info');
    const res = await api.fetchDuplicates();
    if (!res.success) {
        showToast('查找失败', 'error');
        return;
    }
    if (res.count === 0) {
        showToast('没有发现重复模型', 'success');
        return;
    }
    renderModal(res.groups);
}

function ensureOverlay() {
    let overlay = document.getElementById('duplicates-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'duplicates-overlay';
        overlay.className = 'modal-overlay';
        document.body.appendChild(overlay);
    }
    return overlay;
}

// 每组保留第一个，其余为"多余"（可删）
const extrasOf = (group) => group.slice(1);

function renderModal(groups) {
    const overlay = ensureOverlay();
    const totalFiles = groups.reduce((s, g) => s + g.length, 0);
    const extras = groups.flatMap(extrasOf);
    const extraBytes = extras.reduce((s, m) => s + (m.file_size || 0), 0);

    overlay.innerHTML = `
        <div class="modal-content duplicates-modal">
            <div class="modal-header">
                <h2>重复模型 (${groups.length} 组 / ${totalFiles} 个文件)</h2>
                <button class="modal-close" id="dup-close">&times;</button>
            </div>
            <div class="dup-toolbar">
                <span class="dup-toolbar-info">每组保留 1 个，可删除其余 <b>${extras.length}</b> 个多余文件，腾出约 <b>${formatSize(extraBytes)}</b></span>
                <button class="btn btn-danger dup-keepall-btn"${extras.length ? '' : ' disabled'}>全部各保留一个（删除多余文件）</button>
            </div>
            <div class="duplicates-body">
                ${groups.map((g, gi) => renderGroup(g, gi)).join('')}
            </div>
        </div>
    `;

    const close = () => overlay.classList.remove('show');
    overlay.querySelector('#dup-close').addEventListener('click', close);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });

    // 全局：所有组各保留一个、删除其余文件
    overlay.querySelector('.dup-keepall-btn')?.addEventListener('click', async () => {
        const paths = extras.map(m => m.file_path);
        if (!paths.length) return;
        if (!await showConfirm({
            title: '删除重复文件', danger: true, okText: '删除',
            message: `将处理 ${groups.length} 组重复：每组保留 1 个，永久删除其余 ${paths.length} 个文件，` +
                `腾出约 ${formatSize(extraBytes)}。\n\n文件会从硬盘删除、不可恢复。确定继续？`
        })) return;
        await runDelete(paths);
    });

    // 每组：仅保留一个（删除该组其余文件）
    overlay.querySelectorAll('.dup-keep-one-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const group = groups[parseInt(btn.dataset.groupIndex)];
            if (!group || group.length <= 1) return;
            const ex = extrasOf(group);
            const keeper = group[0];
            const bytes = ex.reduce((s, m) => s + (m.file_size || 0), 0);
            if (!await showConfirm({
                title: '删除重复文件', danger: true, okText: '删除',
                message: `保留：${keeper.file_name}（${keeper.folder || '根目录'}）\n` +
                    `删除其余 ${ex.length} 个重复文件，腾出约 ${formatSize(bytes)}。\n\n` +
                    `文件会从硬盘删除、不可恢复。确定？`
            })) return;
            await runDelete(ex.map(m => m.file_path));
        });
    });

    // 每个文件：手动删除此文件（自己挑哪个留时用）
    overlay.querySelectorAll('.dup-del-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const fp = btn.dataset.filePath;
            if (!await showConfirm({ title: '永久删除文件', danger: true, okText: '删除', message: `从硬盘永久删除这个文件、不可恢复？\n\n${fp}` })) return;
            await runDelete([fp]);
        });
    });

    overlay.classList.add('show');
}

// 统一删除入口（始终删文件 = 真正去重），删完重查刷新弹窗
async function runDelete(paths) {
    const res = await api.batchDelete(paths, true);
    if (!res.success) {
        showOpError(res, '删除失败');
        return;
    }
    showToast(`已删除 ${res.deleted} 个重复文件${res.failed ? `，${res.failed} 个失败` : ''}`, 'success');
    loadModels();
    window.dispatchEvent(new Event('noctyra-refresh-sidebar'));

    const fresh = await api.fetchDuplicates();
    if (fresh.success && fresh.count > 0) {
        renderModal(fresh.groups);
    } else {
        const tb = document.querySelector('#duplicates-overlay .dup-toolbar');
        if (tb) tb.remove();
        const body = document.querySelector('#duplicates-overlay .duplicates-body');
        if (body) body.innerHTML = '<div class="dup-empty">✅ 没有重复模型了</div>';
    }
}

function renderGroup(group, groupIndex) {
    const hash = group[0]?.sha256 || '';
    const preview = group[0]?.preview_url;
    return `
        <div class="dup-group">
            <div class="dup-group-header">
                <div class="dup-group-info">
                    ${preview ? `<img class="dup-preview" src="${escAttr(previewUrl(preview))}" alt="" onerror="this.style.visibility='hidden'">` : ''}
                    <div>
                        <span class="dup-group-title">${group.length} 个相同文件</span>
                        <span class="dup-hash" title="${escAttr(hash)}">SHA256: ${hash.substring(0, 16)}…</span>
                    </div>
                </div>
                <button class="btn btn-sm dup-keep-one-btn" data-group-index="${groupIndex}" title="保留第一个，删除其余文件">仅保留一个</button>
            </div>
            <div class="dup-items">
                ${group.map((m, i) => renderDupItem(m, i === 0)).join('')}
            </div>
        </div>
    `;
}

function renderDupItem(model, isKeeper) {
    const name = model.model_name || model.file_name;
    const size = formatSize(model.file_size);
    const source = model.source ? `<span class="badge badge-${model.source === 'civitai' ? 'civitai' : 'hf'}">${escHtml(model.source)}</span>` : '';
    return `
        <div class="dup-item${isKeeper ? ' dup-item-keep' : ''}">
            <div class="dup-item-info">
                <div class="dup-item-name" title="${escAttr(model.file_path)}">
                    ${isKeeper ? '<span class="dup-keep-tag">保留</span>' : ''}${escHtml(name)}
                </div>
                <div class="dup-item-meta">
                    <span>${escHtml(model.folder)}</span>
                    <span>${size}</span>
                    ${model.base_model ? `<span>${escHtml(model.base_model)}</span>` : ''}
                    ${source}
                </div>
                <div class="dup-item-path">${escHtml(model.file_path)}</div>
            </div>
            <div class="dup-item-actions">
                <button class="btn btn-sm btn-danger dup-del-btn" data-file-path="${escAttr(model.file_path)}" title="从硬盘删除此文件">删除</button>
            </div>
        </div>
    `;
}
