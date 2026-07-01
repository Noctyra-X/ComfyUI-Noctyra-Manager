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
 * 自动整理弹窗 — 预览文件移动 → 确认执行
 */
import * as api from '../api.js';
import { showToast, showOpError } from './toast.js';
import { loadModels } from './card-grid.js';
import { escapeAttr as esc } from '../utils.js';

let pendingMoves = [];

export function initOrganize() {
    const btn = document.getElementById('btn-organize');
    if (btn) btn.addEventListener('click', openOrganize);
}

async function openOrganize() {
    showToast('正在分析文件结构...', 'info');
    const res = await api.organizePreview();
    if (!res.success) {
        showToast('分析失败: ' + (res.error || ''), 'error');
        return;
    }

    if (res.count === 0) {
        showToast('所有模型已在正确位置，无需整理', 'success');
        return;
    }

    pendingMoves = res.moves;
    showOrganizeModal(res.moves);
}

function showOrganizeModal(moves) {
    let overlay = document.getElementById('organize-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'organize-overlay';
        overlay.className = 'modal-overlay';
        document.body.appendChild(overlay);
    }

    // 按原因分组：先显示类型纠正，再按 base_model 分组
    const typeFixes = moves.filter(mv => mv.reason === 'type_mismatch');
    const bmMoves = moves.filter(mv => mv.reason !== 'type_mismatch');
    const groups = {};
    for (const mv of bmMoves) {
        const bm = mv.base_model || 'Unknown';
        if (!groups[bm]) groups[bm] = [];
        groups[bm].push(mv);
    }

    overlay.innerHTML = `
        <div class="modal-content organize-modal">
            <div class="modal-header">
                <h2>自动整理预览</h2>
                <button class="modal-close" id="org-close">&times;</button>
            </div>
            <div class="organize-summary">
                将按 Base Model 整理 <strong>${moves.length}</strong> 个文件到对应子文件夹
            </div>
            <div class="organize-body">
                ${typeFixes.length > 0 ? renderGroup('目录纠正（类型与所在目录不匹配）', typeFixes, true) : ''}
                ${Object.entries(groups).map(([bm, items]) => renderGroup(bm, items)).join('')}
            </div>
            <div class="organize-footer">
                <button class="btn" id="org-cancel">取消</button>
                <button class="btn btn-primary" id="org-execute">确认整理 (${moves.length} 个文件)</button>
            </div>
        </div>
    `;

    overlay.querySelector('#org-close').addEventListener('click', () => overlay.classList.remove('show'));
    overlay.querySelector('#org-cancel').addEventListener('click', () => overlay.classList.remove('show'));
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.classList.remove('show'); });

    overlay.querySelector('#org-execute').addEventListener('click', async () => {
        const btn = overlay.querySelector('#org-execute');
        btn.disabled = true;
        btn.textContent = '整理中...';

        try {
            const res = await api.organizeExecute(pendingMoves);
            if (res.success) {
                const msg = `整理完成: 移动 ${res.moved} 个文件` + (res.failed ? `, ${res.failed} 个失败` : '');
                showToast(msg, res.failed ? 'warning' : 'success');
                overlay.classList.remove('show');
                loadModels();
                window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
                window.dispatchEvent(new Event('noctyra-refresh-tabs'));
            } else {
                showOpError(res, '整理失败');
                btn.disabled = false;
                btn.textContent = `确认整理 (${pendingMoves.length} 个文件)`;
            }
        } catch (e) {
            showToast('整理出错: ' + e.message, 'error');
            btn.disabled = false;
            btn.textContent = `确认整理 (${pendingMoves.length} 个文件)`;
        }
    });

    overlay.classList.add('show');
}

function renderGroup(title, items, isWarning = false) {
    const titleClass = isWarning ? 'org-group-title org-group-title-warn' : 'org-group-title';
    return `
        <div class="org-group">
            <div class="org-group-header">
                <span class="${titleClass}">${esc(title)}</span>
                <span class="org-group-count">${items.length} 个文件</span>
            </div>
            <div class="org-items">
                ${items.map(mv => `
                    <div class="org-item">
                        <span class="org-item-name" title="${esc(mv.file_path)}">${esc(mv.file_name)}</span>
                        <span class="org-item-arrow">\u2192</span>
                        <span class="org-item-target" title="${esc(mv.target_path)}">${esc(mv.target_folder)}/</span>
                    </div>
                `).join('')}
            </div>
        </div>
    `;
}

