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
 * Noctyra Companion — background service worker
 *
 * Runs in the extension's own origin, so fetch calls to http://127.0.0.1 succeed
 * without CORS. Content scripts send messages here to proxy requests.
 */

const DEFAULT_PORT = 8188;
const DEFAULT_HOST = '127.0.0.1';

function isAllowedHost(host) {
    if (!host) return false;
    host = String(host).trim().toLowerCase();
    if (host === 'localhost' || host === '127.0.0.1' || host === '::1') return true;
    const m = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
    if (m) {
        const oct = m.slice(1).map(Number);
        if (oct.some(n => n > 255)) return false;   // 非法 IP 段（如 10.999.x.x）直接拒
        const [a, b] = oct;
        if (a === 10) return true;
        if (a === 172 && b >= 16 && b <= 31) return true;
        if (a === 192 && b === 168) return true;
        return false;
    }
    if (host.endsWith('.local')) return true;
    return false;
}

async function getConfig() {
    const { noctyra_host, noctyra_port } = await chrome.storage.sync.get(['noctyra_host', 'noctyra_port']);
    let host = noctyra_host || DEFAULT_HOST;
    const port = noctyra_port || DEFAULT_PORT;
    // 白名单失败时静默 fallback 到默认值，避免扩展访问互联网主机
    if (!isAllowedHost(host)) {
        console.warn('[Noctyra] host 不在白名单，fallback 到', DEFAULT_HOST, '原值:', host);
        host = DEFAULT_HOST;
    }
    return { host, port };
}

function baseUrl(host, port) {
    return `http://${host}:${port}`;
}

async function noctyraFetch(path, options = {}) {
    const { host, port } = await getConfig();
    const url = `${baseUrl(host, port)}${path}`;
    // 超时：Noctyra 不在线时 fetch 会挂住 service worker，必须加上限
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), options.timeoutMs || 12000);
    try {
        const res = await fetch(url, {
            ...options,
            signal: ctrl.signal,
            headers: {
                'Content-Type': 'application/json',
                ...(options.headers || {}),
            },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    } catch (e) {
        if (e.name === 'AbortError') throw new Error('请求超时');
        throw e;
    } finally {
        clearTimeout(timeout);
    }
}

// ---- Message handlers ----

const handlers = {
    async ping() {
        try {
            const data = await noctyraFetch('/api/noctyra/extension/ping');
            return { ok: true, ...data };
        } catch (e) {
            return { ok: false, error: String(e.message || e) };
        }
    },

    async checkVersions({ version_ids, model_ids }) {
        return noctyraFetch('/api/noctyra/extension/check', {
            method: 'POST',
            body: JSON.stringify({ version_ids: version_ids || [], model_ids: model_ids || [] }),
        });
    },

    async fetchVersions({ url }) {
        return noctyraFetch('/api/noctyra/civitai-versions', {
            method: 'POST',
            body: JSON.stringify({ url }),
        });
    },

    async extensionDownload({ model_id, version_id }) {
        // 校验为正整数：ID 来自页面 DOM，防注入非法值触发后端异常下载
        const mid = Number(model_id);
        if (!Number.isInteger(mid) || mid <= 0) {
            return { ok: false, error: 'invalid model_id' };
        }
        const payload = { model_id: mid };
        const vid = Number(version_id);
        if (Number.isInteger(vid) && vid > 0) payload.version_id = vid;  // 否则后端取最新版
        return noctyraFetch('/api/noctyra/extension/download', {
            method: 'POST',
            body: JSON.stringify(payload),
        });
    },

    async extensionSaveImage({ image_id, url }) {
        // 图片 fetch + 落盘 + 元数据入库可能走多个 API，给长一点超时
        return noctyraFetch('/api/noctyra/extension/save-image', {
            method: 'POST',
            body: JSON.stringify({ image_id, url }),
            timeoutMs: 60000,
        });
    },

    async getDownloads() {
        return noctyraFetch('/api/noctyra/downloads');
    },

    async cancelDownload({ download_id }) {
        return noctyraFetch('/api/noctyra/download/cancel', {
            method: 'POST',
            body: JSON.stringify({ download_id }),
        });
    },

    async removeDownload({ download_id }) {
        return noctyraFetch('/api/noctyra/download/remove', {
            method: 'POST',
            body: JSON.stringify({ download_id }),
        });
    },

    async clearDownloads({ status } = {}) {
        // status: 'failed' | 'complete' | 'all'（缺省 = all）
        const qs = status && status !== 'all' ? `?status=${encodeURIComponent(status)}` : '';
        return noctyraFetch(`/api/noctyra/download/clear${qs}`, {
            method: 'POST',
            body: '{}',
        });
    },

    async retryDownload({ download_id }) {
        return noctyraFetch('/api/noctyra/download/retry', {
            method: 'POST',
            body: JSON.stringify({ download_id }),
        });
    },

    async pauseDownload({ download_id }) {
        return noctyraFetch('/api/noctyra/download/pause', {
            method: 'POST',
            body: JSON.stringify({ download_id }),
        });
    },

    async resumeDownload({ download_id }) {
        return noctyraFetch('/api/noctyra/download/resume', {
            method: 'POST',
            body: JSON.stringify({ download_id }),
        });
    },

    async redownloadDownload({ download_id }) {
        return noctyraFetch('/api/noctyra/download/redownload', {
            method: 'POST',
            body: JSON.stringify({ download_id }),
        });
    },

    async getSettings() {
        return noctyraFetch('/api/noctyra/settings');
    },

    async openManager() {
        const { host, port } = await getConfig();
        await chrome.tabs.create({ url: `${baseUrl(host, port)}/noctyra-manager` });
        return { ok: true };
    },

    async openUrl({ url }) {
        // 只允许 https + civitai/huggingface 域名，防止被诱导打开任意页面
        try {
            const u = new URL(url);
            const host = u.hostname.toLowerCase();
            // 与 manifest 的 content_scripts / host_permissions 域名保持一致
            const ALLOWED = ['civitai.com', 'civitai.red', 'civitai.green',
                             'civarchive.com', 'civitaiarchive.com', 'huggingface.co'];
            const ok = u.protocol === 'https:' &&
                ALLOWED.some(d => host === d || host.endsWith('.' + d));
            if (!ok) return { ok: false, success: false, error: 'rejected url' };
            await chrome.tabs.create({ url });
            return { ok: true, success: true };
        } catch (e) {
            return { ok: false, success: false, error: String(e.message || e) };
        }
    },
};

// ---- 周期性刷新图标徽章（进行中数量） ----

async function refreshBadge() {
    try {
        const data = await noctyraFetch('/api/noctyra/downloads');
        const active = (data?.downloads || []).filter(d => d.status === 'downloading' || d.status === 'queued').length;
        const failed = (data?.downloads || []).filter(d => d.status === 'error' || d.status === 'cancelled').length;
        if (active > 0) {
            await chrome.action.setBadgeText({ text: String(active) });
            await chrome.action.setBadgeBackgroundColor({ color: '#2d7ff9' });
        } else if (failed > 0) {
            await chrome.action.setBadgeText({ text: '!' });
            await chrome.action.setBadgeBackgroundColor({ color: '#f87171' });
        } else {
            await chrome.action.setBadgeText({ text: '' });
        }
    } catch (e) {
        await chrome.action.setBadgeText({ text: '' });
    }
}

// 1 分钟周期刷新图标角标（chrome.alarms 的下限；popup 打开时由 popup.js 的 1s setInterval 实时刷新）。
// 另外下载状态一变更（见 onMessage）就立刻刷一次，不必干等这个轮询，解决"下载了角标半天不出数字"。
chrome.alarms.create('noctyra-badge', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((a) => {
    if (a.name === 'noctyra-badge') refreshBadge();
});
refreshBadge();

// 这些动作会改变下载任务状态 → 处理完立刻刷角标，别干等 1 分钟轮询
const DOWNLOAD_MUTATING = new Set([
    'extensionDownload', 'cancelDownload', 'removeDownload', 'clearDownloads',
    'retryDownload', 'pauseDownload', 'resumeDownload', 'redownloadDownload',
]);

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    // 只接受本扩展自己的 content script / popup 的消息，拒绝其它来源触发下载等副作用
    if (!sender || sender.id !== chrome.runtime.id) {
        sendResponse({ ok: false, success: false, error: 'rejected sender' });
        return;
    }
    const handler = msg && handlers[msg.action];
    if (!handler) {
        sendResponse({ ok: false, success: false, error: `Unknown action: ${msg && msg.action}` });
        return;
    }
    (async () => {
        try {
            const result = await handler(msg.payload || {});
            sendResponse(result);
            // 下载状态变更 → 立刻刷新角标（fire-and-forget），让"下载数量"秒出
            if (DOWNLOAD_MUTATING.has(msg.action)) refreshBadge();
        } catch (e) {
            // 同时带 ok / success：content/popup 有的查 res.ok 有的查 res.success，统一兜住错误
            sendResponse({ ok: false, success: false, error: String(e.message || e) });
        }
    })();
    return true; // keep channel open for async sendResponse
});
