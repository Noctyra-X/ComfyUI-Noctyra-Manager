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
 * 任务中心 —— 右下角浮窗，轮询 /status 显示扫描 / 匹配 / 预缓存的实时进度。
 * 预缓存：已下/总数/失败 + 速度/预计剩余 + 失败列表(点击跳转到该模型)。
 * 匹配/预缓存可「停止」；预缓存完成/已停后保留面板(带×关闭),失败列表仍可查可跳。
 */
import * as api from '../api.js';
import { escapeHtml, escapeAttr, formatSize } from '../utils.js';
import { openDetailModal } from './modal.js';
import { getActiveDownloads, runDownloadAction } from './download.js';
import { showToast } from './toast.js';

let el = null;
let expanded = false;
let lastData = null;
let pwDismissed = false;   // 用户关掉了"已完成的预缓存"卡片 → 隐藏，直到下一轮预缓存

// 下载任务操作图标（feather 风格，跨平台渲染稳定，胜过 unicode 符号）
const TC_ICONS = {
    pause:  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="9" y1="5" x2="9" y2="19"/><line x1="15" y1="5" x2="15" y2="19"/></svg>',
    resume: '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" stroke="none"><polygon points="6 4 20 12 6 20"/></svg>',
    retry:  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/></svg>',
    cancel: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    remove: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
};
const DL_LABELS = {
    queued: '排队中', downloading: '下载中', complete: '已完成',
    error: '失败', cancelled: '已取消', paused: '已暂停', interrupted: '已中断',
};

export function initTaskCenter() {
    if (el) return;
    el = document.createElement('div');
    el.id = 'noctyra-task-center';
    el.className = 'task-center hidden';
    document.body.appendChild(el);
    el.addEventListener('click', onClick);
    // 下载进度走 WS 推送(实时);收到就重渲染,任务中心读 getActiveDownloads 展示
    api.onWsEvent('download_progress', () => { if (el) render(lastData); });
    // 下载操作的乐观更新不走 WS,靠该事件通知任务中心刷新
    window.addEventListener('noctyra-downloads-changed', () => { if (el) render(lastData); });
    poll();
}

async function onClick(e) {
    // 失败项 → 跳转到该模型详情
    const fail = e.target.closest('.tc-fail-item[data-path]');
    if (fail) { if (fail.dataset.path) openDetailModal(fail.dataset.path); return; }
    // 下载任务操作:暂停/继续/重试/取消/移除
    const act = e.target.closest('.tc-act-btn[data-dl-act]');
    if (act) {
        e.stopPropagation();
        runDownloadAction(act.dataset.dlAct, act.dataset.dlId, act);
        return;
    }
    // 停止按钮(匹配/预缓存,下载已走上面的 tc-act-btn)
    const cancel = e.target.closest('.tc-cancel-btn');
    if (cancel) {
        e.stopPropagation();
        cancel.disabled = true;
        if (cancel.dataset.cancel === 'match') await api.cancelMatch();
        else if (cancel.dataset.cancel === 'prewarm') await api.cancelPrewarm();
        return;
    }
    // 关闭已完成的预缓存卡
    if (e.target.closest('.tc-dismiss-btn')) { e.stopPropagation(); pwDismissed = true; render(lastData); return; }
    // 标题行 → 展开/收起
    if (e.target.closest('.tc-head')) { expanded = !expanded; render(lastData); }
}

function baseName(u) {
    try {
        const s = String(u).replace(/&#x2F;/gi, '/').replace(/&#47;/g, '/').replace(/&amp;/g, '&');
        return decodeURIComponent(s.split('?')[0].split('/').pop()) || s;
    } catch (_) { return u; }
}

const pct = (cur, total) => total > 0 ? Math.min(100, Math.round(cur / total * 100)) : 0;
const bar = (cur, total) => `<div class="tc-bar"><div class="tc-bar-fill" style="width:${pct(cur, total)}%"></div></div>`;

function fmtEta(sec) {
    if (!isFinite(sec) || sec <= 0) return '';
    if (sec < 90) return `约 ${Math.round(sec)} 秒`;
    return `约 ${Math.round(sec / 60)} 分`;
}

async function poll() {
    let active = false;
    try {
        const st = await api.fetchStatus();
        lastData = st;
        const pw = st && st.prewarm;
        active = !!(st && (st.is_scanning || st.is_matching || (pw && pw.active)));
        render(st);
    } catch (_) { /* 网络抖动忽略，下次再拉 */ }
    // 活跃 2s 一刷；空闲 6s 探测新任务；后台标签页放慢到 15s
    const delay = document.hidden ? 15000 : (active ? 2000 : 6000);
    setTimeout(poll, delay);
}

function render(st) {
    if (!el) return;
    st = st || {};

    const tasks = [];
    const p = st.progress || {};
    if (st.is_scanning) tasks.push({ key: 'scan', label: '扫描', cur: p.current || 0, total: p.total || 0, detail: p.detail || '' });
    if (st.is_matching) tasks.push({ key: 'match', label: '在线匹配', cur: p.current || 0, total: p.total || 0, detail: p.detail || '', cancel: 'match' });
    const pw = st.prewarm;
    if (pw && pw.active) {
        pwDismissed = false;   // 新一轮活跃 → 重置"已关闭"
        tasks.push({ key: 'prewarm', label: '预缓存图片', cur: pw.done, total: pw.total, failed: pw.failed,
                     failures: pw.recent_failures || [], cancel: 'prewarm', elapsed: pw.elapsed || 0 });
    } else if (pw && pw.total > 0 && !pwDismissed) {
        // 完成 / 已停 → 保留展示(带×),失败列表仍可查可跳转
        tasks.push({ key: 'prewarm', label: '预缓存图片', cur: pw.done, total: pw.total, failed: pw.failed,
                     failures: pw.recent_failures || [], done: true, cancelled: !!pw.cancelled });
    }
    // 下载任务(WS 实时驱动,统一在此展示;完成后由 download.js 自动移除)
    for (const d of getActiveDownloads()) {
        tasks.push({ key: 'download', label: d.file_name || '下载', cur: d.downloaded || 0, total: d.total || 0,
                     barPct: d.progress || 0, dlStatus: d.status, speed: d.speed || 0, eta: d.eta || 0,
                     dlId: d.id, dlActive: d.status === 'downloading' || d.status === 'queued' });
    }

    if (!tasks.length) { el.className = 'task-center hidden'; el.innerHTML = ''; return; }
    const anyActive = tasks.some(t => t.key === 'download' ? t.dlActive : !t.done);
    // 有暂停/失败/中断的下载 → 头部用中性提示点(非"全部完成"绿勾)
    const anyHalted = tasks.some(t => t.key === 'download'
        && ['paused', 'error', 'interrupted', 'cancelled'].includes(t.dlStatus));
    el.className = 'task-center' + (expanded ? ' expanded' : '');

    let head;
    if (tasks.length === 1) {
        const t = tasks[0];
        head = t.key === 'download'
            ? (t.dlActive
                ? `下载 ${Math.round(t.barPct)}%`
                : `${DL_LABELS[t.dlStatus] || t.dlStatus}${t.dlStatus === 'paused' ? ` ${Math.round(t.barPct)}%` : ''}`)
            : t.done
                ? `${t.cancelled ? '已停' : '完成'} ${t.label}${t.failed ? ` · ${t.failed} 失败` : ''}`
                : `${t.label} ${t.total ? pct(t.cur, t.total) + '%' : ''}`;
    } else {
        head = `${tasks.length} 个任务`;
    }
    const headIcon = anyActive ? '<span class="tc-spin"></span>'
        : anyHalted ? '<span class="tc-warn-dot"></span>'
        : '<span class="tc-done-dot">✓</span>';
    let html = `<div class="tc-head" title="点击展开/收起">
        ${headIcon}
        <span class="tc-title">${escapeHtml(head)}</span>
        <span class="tc-toggle">${expanded ? '▾' : '▸'}</span>
    </div>`;
    // 折叠时也能看到进度:头部下方一条细进度条(取首个活跃任务)
    if (!expanded && anyActive) {
        const lead = tasks.find(t => t.key === 'download' ? t.dlActive : !t.done) || tasks[0];
        const lp = lead.key === 'download' ? Math.round(lead.barPct || 0) : pct(lead.cur, lead.total);
        html += `<div class="tc-mini"><div class="tc-mini-fill" style="width:${lp}%"></div></div>`;
    }

    if (expanded) {
        html += '<div class="tc-body">';
        for (const t of tasks) {
            if (t.key === 'download') { html += renderDownloadTask(t); continue; }
            const failTag = t.failed ? ` · <span class="tc-fail">${t.failed} 失败</span>` : '';
            const btns = (t.cancel ? `<button class="tc-cancel-btn" data-cancel="${t.cancel}" title="停止">停止</button>` : '')
                + (t.done ? '<button class="tc-dismiss-btn" title="关闭">×</button>' : '');
            html += `<div class="tc-task">
                <div class="tc-task-line">
                    <span>${escapeHtml(t.label)}${t.done ? (t.cancelled ? '（已停）' : '（完成）') : ''}</span>
                    <span class="tc-task-right">${t.cur}/${t.total || '?'}${failTag}${btns}</span>
                </div>
                ${bar(t.cur, t.total)}`;
            // 速度 / 预计剩余(仅活跃预缓存)
            if (t.key === 'prewarm' && !t.done && t.elapsed > 0 && t.cur > 0) {
                const rate = t.cur / t.elapsed;
                const eta = rate > 0 ? Math.max(0, t.total - t.cur - t.failed) / rate : 0;
                const speed = rate >= 1 ? `${rate.toFixed(1)} 张/秒` : `${Math.round(rate * 60)} 张/分`;
                html += `<div class="tc-detail">${speed}${eta ? ` · 还需${fmtEta(eta)}` : ''}</div>`;
            } else if (t.detail) {
                html += `<div class="tc-detail" title="${escapeAttr(String(t.detail))}">${escapeHtml(String(t.detail))}</div>`;
            }
            if (t.key === 'prewarm' && t.failures && t.failures.length) {
                html += `<div class="tc-failures">${t.failures.slice(-12).reverse().map(f => {
                    const path = f.model_path || '';
                    const name = f.model || baseName(f.url || '');
                    const tip = (f.model ? f.model + '\n' : '') + (f.url || '') + (path ? '\n点击跳转到该模型' : '');
                    return `<div class="tc-fail-item${path ? ' tc-clickable' : ''}"${path ? ` data-path="${escapeAttr(path)}"` : ''} title="${escapeAttr(tip)}">
                        <span class="tc-fail-name">${escapeHtml(name)}</span>
                        <span class="tc-fail-reason">${escapeHtml(f.reason || '')}</span>
                    </div>`;
                }).join('')}</div>`;
            }
            html += '</div>';
        }
        html += '</div>';
    }
    el.innerHTML = html;
}

// 按下载状态给出可用操作（与后端状态机对齐：pause 只接受进行中、resume 只接受 paused、
// retry 只接受 error/cancelled/interrupted），complete 由 download.js 自动移除故无按钮
function dlActionButtons(t) {
    const id = escapeAttr(t.dlId);
    const mk = (act, label) =>
        `<button class="tc-act-btn tc-act-${act}" data-dl-act="${act}" data-dl-id="${id}" title="${label}" aria-label="${label}">${TC_ICONS[act]}</button>`;
    const s = t.dlStatus;
    if (s === 'downloading' || s === 'queued') return mk('pause', '暂停') + mk('cancel', '取消');
    if (s === 'paused') return mk('resume', '继续') + mk('cancel', '取消');
    if (s === 'interrupted' || s === 'error' || s === 'cancelled') return mk('retry', '重试') + mk('remove', '移除');
    return '';
}

function renderDownloadTask(t) {
    const s = t.dlStatus;
    const statusLabel = DL_LABELS[s] || s;
    const statusCls = s === 'complete' ? 'tc-st-ok'
        : s === 'paused' ? 'tc-st-paused'
        : (s === 'error' || s === 'cancelled' || s === 'interrupted') ? 'tc-st-err' : '';
    const size = t.total > 0 ? `${formatSize(t.cur)} / ${formatSize(t.total)}` : '';
    const speed = t.speed > 0 ? `${formatSize(t.speed)}/s` : '';
    const eta = t.eta > 0 ? fmtEta(t.eta) : '';
    const detail = [size, speed, eta].filter(Boolean).join(' · ');
    const barCls = s === 'paused' ? ' tc-bar-paused'
        : (s === 'error' || s === 'cancelled' || s === 'interrupted') ? ' tc-bar-err' : '';
    const pctVal = Math.min(100, Math.max(0, Math.round(t.barPct)));
    return `<div class="tc-task">
        <div class="tc-task-line">
            <span class="tc-dl-name" title="${escapeAttr(t.label)}">⬇ ${escapeHtml(t.label)}</span>
            <span class="tc-task-right"><span class="tc-st ${statusCls}">${escapeHtml(statusLabel)}</span>${dlActionButtons(t)}</span>
        </div>
        <div class="tc-bar${barCls}"><div class="tc-bar-fill" style="width:${pctVal}%"></div></div>
        ${detail ? `<div class="tc-detail">${escapeHtml(detail)}</div>` : ''}
    </div>`;
}
