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
 * Noctyra 统计页 —— 用本地 vendored 的 Chart.js（全局 window.Chart）画图，零依赖零构建。
 * 数据来自 /api/noctyra/statistics（后端聚合，复用 models 表既有字段）。
 */

import { initThemeToggle } from './theme.js';

const API = '/api/noctyra/statistics';
const PALETTE = ['#2d7ff9', '#6ba8fd', '#22c55e', '#f59e0b', '#ef4444',
                 '#a855f7', '#ec4899', '#14b8a6', '#eab308', '#64748b',
                 '#0ea5e9', '#f97316', '#84cc16', '#d946ef', '#06b6d4'];

const charts = {};  // id -> Chart 实例，刷新时先销毁

const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function fmtNum(n) {
    n = n || 0;
    return n.toLocaleString('en-US');
}

function fmtBytes(b) {
    b = b || 0;
    if (b < 1024) return b + ' B';
    const u = ['KB', 'MB', 'GB', 'TB'];
    let i = -1;
    do { b /= 1024; i++; } while (b >= 1024 && i < u.length - 1);
    return b.toFixed(b < 10 ? 1 : 0) + ' ' + u[i];
}

function themeColors() {
    const css = getComputedStyle(document.documentElement);
    const pick = (v, fb) => (css.getPropertyValue(v).trim() || fb);
    return {
        text: pick('--text-muted', '#8b8b94'),
        grid: pick('--border', 'rgba(127,127,127,0.16)'),
    };
}

function applyChartDefaults() {
    if (typeof Chart === 'undefined') return false;
    const c = themeColors();
    Chart.defaults.color = c.text;
    Chart.defaults.borderColor = c.grid;
    Chart.defaults.font.family = "'HarmonyOS Sans SC', 'HarmonyOS Sans', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
    Chart.defaults.plugins.legend.labels.padding = 10;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.responsive = true;
    return true;
}

function destroyCharts() {
    for (const k of Object.keys(charts)) {
        try { charts[k].destroy(); } catch (_) {}
        delete charts[k];
    }
}

// 无数据(空 / 全 0)→ 显示"暂无数据"覆盖层而非空白 canvas。覆盖层方式保留 canvas，
// 刷新后有数据时能正常重渲染。
function _isEmptyData(data) {
    return !data || data.length === 0 || data.every(v => !v);
}
function _toggleChartEmpty(id, empty) {
    const el = document.getElementById(id);
    if (!el) return;
    const wrap = el.closest('.stat-canvas-wrap');
    if (!wrap) return;
    let ov = wrap.querySelector('.stat-empty');
    if (empty) {
        el.style.display = 'none';
        if (!ov) {
            ov = document.createElement('div');
            ov.className = 'stat-empty';
            ov.textContent = '暂无数据';
            wrap.appendChild(ov);
        }
    } else {
        el.style.display = '';
        if (ov) ov.remove();
    }
}

function doughnut(id, labels, data, colors) {
    if (_isEmptyData(data)) { _toggleChartEmpty(id, true); return; }
    _toggleChartEmpty(id, false);
    const el = document.getElementById(id);
    if (!el) return;
    charts[id] = new Chart(el, {
        type: 'doughnut',
        data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 0 }] },
        options: {
            cutout: '60%',
            // 环形：旋转扫描 + 从中心放大，和柱状图区分开
            animation: { duration: 900, easing: 'easeOutQuart', animateRotate: true, animateScale: true },
            plugins: { legend: { position: 'bottom' } },
        },
    });
}

function bar(id, labels, data, opts = {}) {
    if (_isEmptyData(data)) { _toggleChartEmpty(id, true); return; }
    _toggleChartEmpty(id, false);
    const el = document.getElementById(id);
    if (!el) return;
    const horizontal = !!opts.horizontal;
    const color = opts.color || PALETTE[0];
    const fmt = opts.fmt;
    const valAxis = horizontal ? 'x' : 'y';
    charts[id] = new Chart(el, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: opts.multicolor ? labels.map((_, i) => PALETTE[i % PALETTE.length]) : color,
                borderRadius: 4,
                maxBarThickness: 34,
            }],
        },
        options: {
            indexAxis: horizontal ? 'y' : 'x',
            // 柱状：从坐标轴基线逐条错峰升起（沿坐标增长），而不是整体放大
            animation: {
                duration: 850,
                easing: 'easeOutQuart',
                delay: (ctx) => (ctx.type === 'data' && ctx.mode === 'default') ? ctx.dataIndex * 45 : 0,
            },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: fmt ? { label: (c) => ' ' + fmt(c.parsed[valAxis]) } : {} },
            },
            scales: {
                x: { grid: { display: horizontal }, ticks: horizontal && fmt ? { callback: fmt } : {} },
                y: { grid: { display: !horizontal }, ticks: !horizontal && fmt ? { callback: fmt } : {} },
            },
        },
    });
}

function renderCards(ov) {
    const rate = ov.total > 0 ? Math.round(ov.matched / ov.total * 100) : 0;
    const nsfwPct = ov.total > 0 ? Math.round(ov.nsfw / ov.total * 100) : 0;
    const cards = [
        { label: '模型总数', value: fmtNum(ov.total), hint: `${fmtNum(ov.favorites)} 个收藏` },
        { label: '已匹配', value: fmtNum(ov.matched), hint: `匹配率 ${rate}%`, accent: true },
        { label: '未匹配', value: fmtNum(ov.unmatched), hint: '可在线匹配补全' },
        { label: '总存储', value: fmtBytes(ov.total_bytes), hint: '当前库占用' },
        { label: 'NSFW 占比', value: nsfwPct + '%', hint: `${fmtNum(ov.nsfw)} 个 NSFW` },
        { label: '删除存档', value: fmtNum(ov.deleted), hint: '删文件留记录' },
    ];
    document.getElementById('stat-cards').innerHTML = cards.map(c => `
        <div class="stat-card${c.accent ? ' stat-card-accent' : ''}">
            <div class="stat-card-value">${esc(c.value)}</div>
            <div class="stat-card-label">${esc(c.label)}</div>
            <div class="stat-card-hint">${esc(c.hint)}</div>
        </div>`).join('');
}

// 与管理器 api.js previewUrl 同逻辑：sidecar 走本地预览路由，其余走代理；统一取 480px 卡片缩略图
function placeholder() {
    return document.documentElement.dataset.theme === 'light'
        ? '/noctyra_static/images/placeholder-light.svg'
        : '/noctyra_static/images/placeholder-dark.svg';
}

function previewUrl(raw) {
    if (!raw) return placeholder();
    if (raw.startsWith('sidecar://')) {
        return `/api/noctyra/local-preview?id=${encodeURIComponent(raw.slice(10))}&size=card`;
    }
    return `/api/noctyra/preview?url=${encodeURIComponent(raw)}&size=card`;
}

function renderTopUsed(list) {
    const wrap = document.getElementById('stat-top-used');
    if (!wrap) return;
    if (!list || !list.length) {
        wrap.innerHTML = '<span class="stat-empty">还没有使用记录</span>';
        return;
    }
    const max = Math.max(...list.map(m => m.usage_count));
    const ph = placeholder();
    wrap.innerHTML = list.map((m, i) => {
        const pct = max > 0 ? Math.round(m.usage_count / max * 100) : 0;
        return `<div class="stat-model-item">
            <span class="stat-model-rank">${i + 1}</span>
            <img class="stat-model-thumb" src="${esc(previewUrl(m.preview_url))}" alt="" loading="lazy"
                 onerror="this.onerror=null;this.src='${ph}'">
            <div class="stat-model-info">
                <div class="stat-model-name" title="${esc(m.name)}">${esc(m.name)}</div>
                <div class="stat-model-bar"><div class="stat-model-bar-fill" style="width:${pct}%"></div></div>
            </div>
            <span class="stat-model-count" title="使用 ${fmtNum(m.usage_count)} 次">${fmtNum(m.usage_count)}</span>
        </div>`;
    }).join('');
}

function renderTags(tags) {
    const wrap = document.getElementById('stat-tags');
    if (!tags || !tags.length) {
        wrap.innerHTML = '<span class="stat-empty">暂无标签</span>';
        return;
    }
    const max = Math.max(...tags.map(t => t.count));
    wrap.innerHTML = tags.map(t => {
        const scale = 0.85 + 0.6 * (t.count / max);   // 字号随热度
        return `<span class="stat-tag" style="font-size:${scale.toFixed(2)}em">${esc(t.name)}<b>${fmtNum(t.count)}</b></span>`;
    }).join('');
}

function renderCharts(s) {
    const ov = s.overview;
    const SRC = s.by_source || {};

    doughnut('chart-matched', ['已匹配', '未匹配'], [ov.matched, ov.unmatched], ['#2d7ff9', '#3a3a44']);
    doughnut('chart-source',
        ['CivitAI', 'HuggingFace', '未匹配'],
        [SRC.civitai || 0, SRC.huggingface || 0, (SRC.unmatched || 0) + (SRC[''] || 0)],
        ['#2d7ff9', '#f59e0b', '#3a3a44']);
    doughnut('chart-nsfw', ['SFW', 'NSFW'], [ov.sfw, ov.nsfw], ['#22c55e', '#ef4444']);
    doughnut('chart-usage', ['用过', '未用过'], [s.usage.used, s.usage.unused], ['#a855f7', '#3a3a44']);

    const types = s.by_type || [];
    bar('chart-type', types.map(t => t.type), types.map(t => t.count), { multicolor: true });

    const bases = s.by_base_model || [];
    bar('chart-base', bases.map(b => b.name), bases.map(b => b.count), { horizontal: true, color: '#6ba8fd' });

    bar('chart-storage', types.map(t => t.type), types.map(t => t.bytes),
        { color: '#14b8a6', fmt: fmtBytes });

    renderTopUsed(s.usage.top_used);
    renderTags(s.top_tags);
}

async function load() {
    const loading = document.getElementById('stat-loading');
    const content = document.getElementById('stat-content');
    const errEl = document.getElementById('stat-error');
    if (!loading || !content || !errEl) return;  // 容器缺失（理论上不会）→ 安全退出，避免 null.style 崩
    errEl.style.display = 'none';
    if (typeof Chart === 'undefined') {
        loading.style.display = 'none';
        errEl.textContent = 'Chart.js 未加载，无法绘制图表。';
        errEl.style.display = 'block';
        return;
    }
    applyChartDefaults();
    try {
        const res = await fetch(API);
        const data = await res.json();
        if (!data || !data.success) throw new Error((data && data.error) || '加载失败');
        const s = data.stats;
        destroyCharts();
        // 先显示容器再建图表：否则图表在 display:none(0 尺寸)下创建，显示时整体从 0 缩放，
        // 看着就是"统一放大"，盖过了各自的数据动画
        loading.style.display = 'none';
        content.style.display = '';
        renderCards(s.overview);
        renderCharts(s);
    } catch (e) {
        loading.style.display = 'none';
        errEl.textContent = '加载统计失败：' + (e.message || e);
        errEl.style.display = 'block';
    }
}

document.getElementById('stat-refresh')?.addEventListener('click', () => {
    document.getElementById('stat-content').style.display = 'none';
    document.getElementById('stat-loading').style.display = '';
    load();
});

initThemeToggle('stat-theme-toggle');
load();
