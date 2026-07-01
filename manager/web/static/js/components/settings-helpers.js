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
 * Settings 共享上下文和通用辅助函数
 */
import * as api from '../api.js';
import { showToast } from './toast.js';
import { state } from '../state.js';

// 共享上下文：各 section 通过 ctx.settings 读写当前设置对象
// 主 settings.js 在 openSettings 中会更新 ctx.settings 引用
export const ctx = {
    settings: null,
};

export function renderToggle(id, checked) {
    return `<label class="toggle-switch">
        <input type="checkbox" id="${id}"${checked ? ' checked' : ''}>
        <span class="toggle-slider"></span>
    </label>`;
}

export function bindToggle(content, id, settingKey, callback) {
    const el = content.querySelector(`#${id}`);
    if (el) {
        el.addEventListener('change', () => {
            saveSetting(settingKey, el.checked);
            if (callback) callback(el.checked);
        });
    }
}

export function bindSelect(content, id, settingKey, callback) {
    const el = content.querySelector(`#${id}`);
    if (el) {
        el.addEventListener('change', () => {
            saveSetting(settingKey, el.value);
            if (callback) callback(el.value);
        });
    }
}

export async function saveSetting(key, value) {
    // 乐观更新：先同步写本地 state，保证绑定的 callback（如 'noctyra-refresh-list'
    // → buildQueryParams 读 state.settings.show_only_sfw）能立刻看到新值。
    // 网络保存是 fire-and-forget，失败才回滚 + 提示。
    const oldCtx = ctx.settings ? ctx.settings[key] : undefined;
    const oldState = state.settings ? state.settings[key] : undefined;
    if (ctx.settings) ctx.settings[key] = value;
    if (state.settings) state.settings[key] = value;
    try {
        const res = await api.saveSettings({ [key]: value });
        if (!res.success) {
            if (ctx.settings) ctx.settings[key] = oldCtx;
            if (state.settings) state.settings[key] = oldState;
            showToast('保存失败', 'error');
        }
    } catch (e) {
        if (ctx.settings) ctx.settings[key] = oldCtx;
        if (state.settings) state.settings[key] = oldState;
        showToast('保存失败: ' + e.message, 'error');
    }
}

export function updateKeyStatus(content, inputSelector) {
    const wrapper = content.querySelector(inputSelector)?.closest('.api-key-input');
    if (!wrapper) return;
    let badge = wrapper.querySelector('.key-status');
    if (!badge) {
        badge = document.createElement('span');
        badge.className = 'key-status set';
        wrapper.appendChild(badge);
    }
    badge.textContent = '已设置';
    badge.classList.add('set');
}
