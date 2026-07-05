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

const hostEl = document.getElementById('host');
const portEl = document.getElementById('port');
const statusEl = document.getElementById('status');
const infoEl = document.getElementById('info');
const saveBtn = document.getElementById('save-btn');
const openBtn = document.getElementById('open-manager');
const listEl = document.getElementById('download-list');
const clearBtn = document.getElementById('btn-clear');

let currentFilter = 'active';
let pollTimer = null;
let _pollInterval = 0;   // 当前轮询间隔（0=未启动）
let _lastListSig = '';   // 列表内容签名：只有变了才重建 DOM，避免每秒整表 innerHTML

// `send()` 由 noctyra-send.js 提供（popup.html 先加载）

async function loadConfig() {
    const { noctyra_host, noctyra_port } = await chrome.storage.sync.get(['noctyra_host', 'noctyra_port']);
    hostEl.value = noctyra_host || '127.0.0.1';
    portEl.value = noctyra_port || 8188;
}

function isAllowedHost(host) {
    if (!host) return false;
    host = host.trim().toLowerCase();
    // 只允许本机：manifest host_permissions 也只授了 127.0.0.1/localhost。私网/.local 即便
    // 校验通过，浏览器也会因未授权而静默拦截 fetch → 误报"未连接"。如需访问局域网另一台
    // 机器的 ComfyUI，得改用 optional_host_permissions 在用户手势里动态申请权限。
    return host === 'localhost' || host === '127.0.0.1' || host === '::1';
}

async function saveConfig() {
    const host = hostEl.value.trim() || '127.0.0.1';
    if (!isAllowedHost(host)) {
        showConfigError('仅支持本机：localhost / 127.0.0.1');
        throw new Error('host rejected');
    }
    const port = parseInt(portEl.value, 10) || 8188;
    if (port < 1 || port > 65535) {
        showConfigError('端口必须在 1-65535 之间');
        throw new Error('port invalid');
    }
    await chrome.storage.sync.set({
        noctyra_host: host,
        noctyra_port: port,
    });
}

function showConfigError(msg) {
    statusEl.textContent = '✗ ' + msg;
    statusEl.className = 'status error';
}

async function ping() {
    statusEl.textContent = '检查连接...';
    statusEl.className = 'status';
    const res = await send('ping');
    if (res.ok) {
        statusEl.textContent = '已连接';
        statusEl.className = 'status healthy';
        infoEl.textContent = `版本 ${res.version || '-'} · 本地 ${res.total || 0} · 已匹配 ${res.matched || 0}`;
    } else {
        statusEl.textContent = '未连接';
        statusEl.className = 'status error';
        infoEl.textContent = res.error || 'Noctyra 未运行或地址/端口不正确';
    }
}

function fmtSize(b) {
    if (!b) return '0';
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return b.toFixed(1) + u[i];
}

function fmtSpeed(bps) {
    return fmtSize(bps) + '/s';
}

function fmtEta(sec) {
    if (!sec || sec === Infinity) return '-';
    if (sec < 60) return `${Math.round(sec)}s`;
    if (sec < 3600) return `${Math.round(sec / 60)}m`;
    return `${(sec / 3600).toFixed(1)}h`;
}

function managerBase() {
    const host = (hostEl.value || '127.0.0.1').trim();
    const port = parseInt(portEl.value, 10) || 8188;
    return `http://${host}:${port}`;
}
// 缩略图走本地后端预览代理（480px webp + 缓存），避免扩展直接拉 CivitAI 原图裂图/过大
function previewSrc(url) {
    if (!url || url.startsWith('sidecar://')) return '';
    return `${managerBase()}/api/noctyra/preview?url=${encodeURIComponent(url)}&size=card`;
}

function statusBucket(s) {
    // 只有真正有活跃任务的（下载中/排队/暂停）才进"进行中"，显示 暂停/取消/继续；
    // interrupted（重启恢复、无 task）归到失败组，显示 重试/移除（重试从 .tmp 续传）。
    if (s === 'downloading' || s === 'queued' || s === 'paused') return 'active';
    if (s === 'error' || s === 'cancelled' || s === 'interrupted') return 'failed';
    if (s === 'complete') return 'complete';
    return 'active';
}

function renderList(downloads) {
    const counts = { active: 0, failed: 0, complete: 0 };
    downloads.forEach(d => counts[statusBucket(d.status)]++);
    document.getElementById('count-active').textContent = counts.active;
    document.getElementById('count-failed').textContent = counts.failed;
    document.getElementById('count-complete').textContent = counts.complete;

    const filtered = downloads.filter(d => statusBucket(d.status) === currentFilter);
    if (filtered.length === 0) {
        if (_lastListSig !== 'empty') { listEl.innerHTML = '<div class="empty">无任务</div>'; _lastListSig = 'empty'; }
        return;
    }

    // 内容签名：id/状态/进度/大小/速度都没变就整段跳过重建 —— 避免每秒 innerHTML 重建
    // 导致长列表滚动被弹回顶端、缩略图重新 decode 闪烁、点按钮时元素被换掉点空。
    const sig = filtered.map(d =>
        `${d.id}:${d.status}:${(d.progress || 0).toFixed(1)}:${d.downloaded || 0}:${d.speed || 0}`
    ).join('|');
    if (sig === _lastListSig) return;
    _lastListSig = sig;
    const prevScroll = listEl.scrollTop;
    listEl.innerHTML = filtered.map(d => renderItem(d)).join('');
    listEl.scrollTop = prevScroll;   // 重建后恢复滚动位置
}

// 事件委托：在父容器监听一次，避免每次 renderList 给新节点重复绑定
listEl.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.getAttribute('data-action');
    if (action === 'open') {
        const url = btn.getAttribute('data-url');
        if (url) send('openUrl', { url });   // 打开原网页（background 校验域名）
        return;
    }
    const id = btn.getAttribute('data-id');
    if (action === 'cancel') {
        const res = await send('cancelDownload', { download_id: id });
        if (!res?.success) console.warn('[Noctyra] cancel 无效:', res, '(后端可能未重启 / 该任务无活跃下载)');
    } else if (action === 'remove') {
        await send('removeDownload', { download_id: id });
    } else if (action === 'pause') {
        const res = await send('pauseDownload', { download_id: id });
        if (!res?.success) console.warn('[Noctyra] pause 无效:', res, '(后端未重启则 /download/pause 为 404)');
    } else if (action === 'resume') {
        const res = await send('resumeDownload', { download_id: id });
        if (!res?.success && res?.error) {
            console.warn('[Noctyra] resume failed:', res.error);  // 多半是上次任务收尾未完，稍后再点
        }
    } else if (action === 'redownload') {
        const res = await send('redownloadDownload', { download_id: id });
        if (!res?.success) console.warn('[Noctyra] redownload 无效:', res);
    } else if (action === 'retry') {
        const res = await send('retryDownload', { download_id: id });
        if (!res?.success && res?.error) {
            // 调试日志：知道为啥失败（多半是已经在跑了）
            console.warn('[Noctyra] retry failed:', res.error);
        }
    }
    await refresh();
});

const ICON = {
    pause: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>',
    play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>',
    retry: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>',
    download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
};
function actBtn(action, id, label, variant, icon) {
    return `<button class="dl-act${variant ? ' ' + variant : ''}" data-action="${action}" data-id="${id}" title="${label}" aria-label="${label}">${icon}</button>`;
}

function renderItem(d) {
    const pct = (d.progress || 0).toFixed(1);
    const bucket = statusBucket(d.status);
    const paused = d.status === 'paused';

    let actions, metaLine;
    if (paused) {
        actions = actBtn('resume', d.id, '继续', 'primary', ICON.play)
                + actBtn('remove', d.id, '移除', '', ICON.trash);
        metaLine = `已暂停 · ${pct}% · ${fmtSize(d.downloaded)}/${fmtSize(d.total)}`;
    } else if (bucket === 'active') {
        actions = actBtn('pause', d.id, '暂停', '', ICON.pause)
                + actBtn('cancel', d.id, '取消', 'danger', ICON.x);
        metaLine = `${pct}% · ${fmtSize(d.downloaded)}/${fmtSize(d.total)} · ${fmtSpeed(d.speed)} · ETA ${fmtEta(d.eta)}`;
    } else if (bucket === 'failed') {
        actions = actBtn('retry', d.id, d.status === 'interrupted' ? '继续' : '重试', 'primary', ICON.retry)
                + actBtn('remove', d.id, '移除', '', ICON.trash);
        const failLabel = d.status === 'cancelled' ? '已取消' : (d.status === 'interrupted' ? '已中断（可继续）' : '失败');
        metaLine = `<span class="err">${escapeHtml(d.error || failLabel)}</span>`;
    } else {
        actions = actBtn('redownload', d.id, '重新下载', 'primary', ICON.download)
                + actBtn('remove', d.id, '移除', '', ICON.trash);
        metaLine = `✓ ${fmtSize(d.total || d.downloaded)} · ${escapeHtml(d.save_dir || '')}`;
    }

    const thumb = d.preview_url
        ? `<img class="dl-thumb" src="${escapeHtml(previewSrc(d.preview_url))}" alt="" loading="lazy">`
        : `<div class="dl-thumb dl-thumb-placeholder"></div>`;
    const nameHtml = d.source_url
        ? `<span class="dl-name dl-name-link" data-action="open" data-url="${escapeHtml(d.source_url)}" title="点击打开原网页：${escapeHtml(d.file_name)}">${escapeHtml(d.file_name)}</span>`
        : `<span class="dl-name" title="${escapeHtml(d.file_name)}">${escapeHtml(d.file_name)}</span>`;
    const showBar = bucket === 'active';   // 下载中 / 排队 / 暂停 都显示进度条
    return `
        <div class="dl-item ${bucket}${paused ? ' paused' : ''}">
            ${thumb}
            <div class="dl-body">
                <div class="dl-head">
                    ${nameHtml}
                    <div class="dl-actions">${actions}</div>
                </div>
                ${showBar ? `<div class="dl-bar"><div class="dl-fill" style="width:${pct}%"></div></div>` : ''}
                <div class="dl-meta">${metaLine}</div>
            </div>
        </div>
    `;
}

function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

async function refresh() {
    const res = await send('getDownloads');
    if (res?.success) {
        const dls = res.downloads || [];
        renderList(dls);
        // 有进行中任务才 1s 快刷；否则 4s 慢刷（仍能发现新任务/重连，但不再每秒轮询+重建）
        schedulePoll(dls.some(d => statusBucket(d.status) === 'active'));
    } else {
        // 后端不可达：明确告知"未连接"，而不是误导的"无任务"
        ['active', 'failed', 'complete'].forEach(k => {
            document.getElementById('count-' + k).textContent = '0';
        });
        if (_lastListSig !== 'disconnected') {
            listEl.innerHTML = `
            <div class="empty empty-disconnected">
                <div class="empty-title">未连接到 Noctyra</div>
                <div class="empty-sub">确认 ComfyUI 已运行，并在「设置」里填对地址 / 端口</div>
                <button class="empty-act" id="empty-goto-settings">前往设置</button>
            </div>`;
            _lastListSig = 'disconnected';
            const goto = document.getElementById('empty-goto-settings');
            if (goto) goto.addEventListener('click', () => {
                document.querySelector('.tab[data-tab="settings"]')?.click();
            });
        }
        schedulePoll(false);   // 断连时慢刷重连（静态 UI，无重建 churn）
    }
}

// 按是否有进行中任务动态调整轮询间隔：1s（活跃）/ 4s（空闲）
function schedulePoll(hasActive) {
    const want = hasActive ? 1000 : 4000;
    if (pollTimer && _pollInterval === want) return;
    if (pollTimer) clearInterval(pollTimer);
    _pollInterval = want;
    pollTimer = setInterval(refresh, want);
}

// ---- Tab switching ----
document.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
        t.classList.add('active');
        document.getElementById('panel-' + t.dataset.tab).classList.add('active');
    });
});

function updateClearBtnState() {
    // 进行中的任务不该批量清（应该用各项取消）；只在终态 tab 启用
    if (currentFilter === 'active') {
        clearBtn.disabled = true;
        clearBtn.title = '进行中的任务请用各项的取消按钮';
    } else {
        clearBtn.disabled = false;
        clearBtn.title = '清空当前 tab 的记录';
    }
}

document.querySelectorAll('.sub-tab').forEach(t => {
    t.addEventListener('click', () => {
        document.querySelectorAll('.sub-tab').forEach(x => x.classList.remove('active'));
        t.classList.add('active');
        currentFilter = t.dataset.filter;
        updateClearBtnState();
        refresh();
    });
});

// 站内确认弹窗（替代浏览器原生 confirm，风格对齐 popup 主题）
function popupConfirm({ message = '', okText = '确定', cancelText = '取消', danger = false } = {}) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'popup-confirm-overlay';
        overlay.innerHTML = `
            <div class="popup-confirm" role="dialog" aria-modal="true">
                <p class="popup-confirm-msg"></p>
                <div class="popup-confirm-actions">
                    <button class="pc-btn" data-act="cancel"></button>
                    <button class="pc-btn ${danger ? 'pc-danger' : 'pc-primary'}" data-act="ok"></button>
                </div>
            </div>`;
        overlay.querySelector('.popup-confirm-msg').textContent = message;   // textContent 防注入
        overlay.querySelector('[data-act="cancel"]').textContent = cancelText;
        overlay.querySelector('[data-act="ok"]').textContent = okText;
        document.body.appendChild(overlay);
        const done = (v) => { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
        const onKey = (e) => {
            if (e.key === 'Escape') done(false);
            else if (e.key === 'Enter') done(true);
        };
        document.addEventListener('keydown', onKey);
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) { done(false); return; }   // 点遮罩 = 取消
            const act = e.target.closest('[data-act]');
            if (act) done(act.dataset.act === 'ok');
        });
        overlay.querySelector('[data-act="ok"]').focus();
    });
}

clearBtn.addEventListener('click', async () => {
    // 按当前 tab 决定清哪部分：进行中不可清；失败/完成只清对应一组
    if (currentFilter === 'active') return;  // disabled 状态下不应触发，保险拦一下
    const label = currentFilter === 'failed' ? '失败/取消' : '已完成';
    if (!await popupConfirm({ message: `清空"${label}"列表的下载记录？`, okText: '清空', danger: true })) return;
    await send('clearDownloads', { status: currentFilter });
    await refresh();
});

saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    const orig = saveBtn.textContent;
    saveBtn.textContent = '测试中...';
    try {
        await saveConfig();
        await ping();
    } catch (e) {
        // saveConfig 在校验失败时 throw，错误已通过 showConfigError 展示
        console.debug('[Noctyra] saveConfig rejected:', e.message);
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = orig;
    }
});

openBtn.addEventListener('click', () => {
    send('openManager');
    window.close();
});

(async () => {
    updateClearBtnState();  // 初始 currentFilter='active' → 清空按钮 disabled
    await loadConfig();
    await ping();
    await refresh();   // refresh() 内按是否有活跃任务启动动态轮询
})();

window.addEventListener('unload', () => {
    if (pollTimer) clearInterval(pollTimer);
});
