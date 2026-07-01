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
 * Shared helper: 向 background service worker 发消息并 await 返回。
 * 被 content script 和 popup 共用。
 * 10s 超时保护，避免 service worker 被浏览器终止时 UI 永久挂起。
 */
const NOCTYRA_SEND_TIMEOUT_MS = 10000;

function send(action, payload, timeoutMs = NOCTYRA_SEND_TIMEOUT_MS) {
    return new Promise((resolve) => {
        if (!chrome?.runtime?.sendMessage) {
            console.debug('[Noctyra] sendMessage unavailable', action);
            resolve({ ok: false, error: 'extension context invalidated' });
            return;
        }
        let settled = false;
        const finish = (res) => {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            resolve(res);
        };
        const timer = setTimeout(() => {
            console.warn('[Noctyra] sendMessage timeout', action, timeoutMs + 'ms');
            finish({ ok: false, error: 'timeout: background 未响应（service worker 可能已休眠）' });
        }, timeoutMs);
        try {
            chrome.runtime.sendMessage({ action, payload }, (res) => {
                if (chrome.runtime.lastError) {
                    console.debug('[Noctyra] sendMessage error', action, chrome.runtime.lastError.message);
                    finish({ ok: false, error: chrome.runtime.lastError.message });
                } else {
                    finish(res || { ok: false });
                }
            });
        } catch (e) {
            console.warn('[Noctyra] sendMessage threw', action, e);
            finish({ ok: false, error: e.message });
        }
    });
}
