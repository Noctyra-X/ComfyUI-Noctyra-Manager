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
 * 跨页 / 跨标签同步"仅显示 SFW"开关。
 * 模型页、工作流页、画布选择器共用同一状态：在任意一处切换 →
 *   1) 同时写 show_only_sfw + gallery_show_only_sfw（值相同，保持全局与图库一致，
 *      不然工作流图库读的是独立键，永远和模型页/选择器对不上）；
 *   2) BroadcastChannel 广播给同源其它页面/标签，立刻跟随；
 *   3) localStorage 兜底（个别环境无 BroadcastChannel，靠 storage 事件跨标签同步）。
 */

const CHAN_NAME = 'noctyra-sfw';
const LS_KEY = 'noctyra_sfw_only';

let _chan = null;
try { _chan = new BroadcastChannel(CHAN_NAME); } catch (_) { /* 降级到 storage */ }

// 切换并广播。value = 新的"仅显示 SFW"布尔值。
export async function setSfwEverywhere(value) {
    value = !!value;
    try {
        await fetch('/api/noctyra/settings', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ show_only_sfw: value, gallery_show_only_sfw: value }),
        });
    } catch (_) { /* 网络失败也要广播，至少各页 UI 一致 */ }
    try { if (_chan) _chan.postMessage(value); } catch (_) {}
    try { localStorage.setItem(LS_KEY, value ? '1' : '0'); } catch (_) {}
}

// 订阅其它 surface 的变更。回调里只更新 UI + 重载列表，
// 不要再调 setSfwEverywhere（否则广播回环）。
export function onSfwChange(cb) {
    try { if (_chan) _chan.addEventListener('message', (e) => cb(!!e.data)); } catch (_) {}
    window.addEventListener('storage', (e) => {
        if (e.key === LS_KEY) cb(e.newValue === '1');
    });
}
