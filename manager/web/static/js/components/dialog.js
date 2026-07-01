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
 * 应用内对话框 —— 取代浏览器原生 confirm() / prompt()，风格统一（复用 .delete-confirm-overlay）。
 * Esc / 点遮罩 = 取消，Enter = 确认。
 */
import { escapeHtml } from '../utils.js';

/**
 * 确认弹窗。返回 Promise<boolean>（确定=true，取消/Esc/点遮罩=false）。
 * message 支持多行（\n 自动转换行）。danger=true 时确认按钮为红色。
 */
export function showConfirm({ title = '确认', message = '', okText = '确定', cancelText = '取消', danger = false } = {}) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'delete-confirm-overlay';
        overlay.innerHTML = `
            <div class="delete-confirm">
                <h3>${escapeHtml(title)}</h3>
                ${message ? `<p>${escapeHtml(message).replace(/\n/g, '<br>')}</p>` : ''}
                <div class="delete-confirm-actions">
                    <button class="btn" data-action="cancel">${escapeHtml(cancelText)}</button>
                    <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-action="confirm">${escapeHtml(okText)}</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        const onKey = e => {
            if (e.key === 'Escape') finish(false);
            else if (e.key === 'Enter') finish(true);
        };
        const finish = v => { document.removeEventListener('keydown', onKey); overlay.remove(); resolve(v); };
        overlay.querySelector('[data-action="cancel"]').addEventListener('click', () => finish(false));
        overlay.querySelector('[data-action="confirm"]').addEventListener('click', () => finish(true));
        overlay.addEventListener('click', e => { if (e.target === overlay) finish(false); });
        document.addEventListener('keydown', onKey);
        overlay.querySelector('[data-action="confirm"]').focus();
    });
}

/**
 * 文本输入弹窗。返回 Promise<string|null>（trim 后；取消 / 空 = null）。
 */
export function showPrompt({ title = '', message = '', defaultValue = '', okText = '保存', placeholder = '' } = {}) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'delete-confirm-overlay';
        overlay.innerHTML = `
            <div class="delete-confirm">
                <h3>${escapeHtml(title)}</h3>
                ${message ? `<p>${escapeHtml(message).replace(/\n/g, '<br>')}</p>` : ''}
                <input type="text" class="preset-prompt-input settings-input"
                       value="${escapeHtml(defaultValue)}" placeholder="${escapeHtml(placeholder)}">
                <div class="delete-confirm-actions">
                    <button class="btn" data-action="cancel">取消</button>
                    <button class="btn btn-primary" data-action="confirm">${escapeHtml(okText)}</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        const input = overlay.querySelector('.preset-prompt-input');
        input.focus();
        input.select();
        const finish = v => { overlay.remove(); resolve(v); };
        overlay.querySelector('[data-action="cancel"]').addEventListener('click', () => finish(null));
        overlay.querySelector('[data-action="confirm"]').addEventListener('click', () => finish(input.value.trim() || null));
        overlay.addEventListener('click', e => { if (e.target === overlay) finish(null); });
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter') finish(input.value.trim() || null);
            else if (e.key === 'Escape') finish(null);
        });
    });
}
