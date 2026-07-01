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
 * Toast 通知组件
 */

let container = null;

function ensureContainer() {
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        document.body.appendChild(container);
    }
    return container;
}

/**
 * 把后端 {success:false, error} 的失败统一弹 toast。
 * error === 'busy'（有扫描/匹配/整理等操作进行中被拒）→ 友好的 warning 提示；
 * 其余 → error 提示，附带具体原因。各调用点不必各自判 busy。
 */
export function showOpError(res, fallbackMsg = '操作失败') {
    const err = res && res.error;
    if (err === 'busy') {
        showToast('有操作进行中，请稍后再试', 'warning');
    } else {
        showToast(err ? `${fallbackMsg}: ${err}` : fallbackMsg, 'error');
    }
}

const _ICON = {
    success: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
    error:   '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    warning: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    info:    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
};

export function showToast(message, type = 'info', duration = 3000) {
    const c = ensureContainer();
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    el.innerHTML = `<span class="toast-icon">${_ICON[type] || _ICON.info}</span><span class="toast-msg"></span><button class="toast-close" aria-label="关闭">✕</button>`;
    el.querySelector('.toast-msg').textContent = message;   // textContent：消息文本不走 innerHTML，防注入
    c.appendChild(el);

    requestAnimationFrame(() => el.classList.add('show'));

    let removed = false;
    const cleanup = () => { if (removed) return; removed = true; el.remove(); };
    const dismiss = () => {
        el.classList.remove('show');
        el.addEventListener('transitionend', cleanup, { once: true });
        setTimeout(cleanup, 400);   // 保底：过渡不生效时强制移除
    };
    el.querySelector('.toast-close').addEventListener('click', dismiss);
    setTimeout(dismiss, duration);
}
