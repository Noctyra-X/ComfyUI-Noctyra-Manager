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
 * Noctyra 画布模型选择器（悬浮按钮版）
 *
 * 不修改任何节点的 widget。在画布上常驻一个悬浮图标：
 *   - 选中含模型 widget 的 loader 节点时，图标「亮起」并显示类型
 *   - 点击亮起的图标弹出带预览图的卡片选择器
 *   - 选项来自 widget.options.values（ComfyUI 扫出的合法值），选中必合法
 * 元数据来自 Noctyra /api/noctyra/picker/match，视觉对齐模型管理器。
 */

import { app } from "../../scripts/app.js";

const API = "/api/noctyra/picker/match";
const API_GALLERY = "/api/noctyra/workflow/gallery";
const API_TO_INPUT = (id) => `/api/noctyra/workflow/image/${id}/to-input`;
// LoadImage / LoadImageMask 等"加载图像"节点的文件 widget 名
const IMAGE_WIDGET_NAMES = new Set(["image"]);
// VHS_LoadVideo / 核心 LoadVideo 等"加载视频"节点的文件 widget 名
const VIDEO_WIDGET_NAMES = new Set(["video", "video_file"]);

// NSFW 预览模糊阈值默认（对齐管理器默认 R 级=4）。只用模型级 nsfw_level 字段做"显示遮挡"，
// 不据此反推/标记模型本身的 nsfw 属性。CivitAI nsfwLevel: 1=PG 2=PG13 4=R 8=X 16=XXX
const NSFW_BLUR_THRESHOLD = 4;

// 实际生效的 NSFW 设置 —— 读自管理器（设置 → 界面 → "模糊 NSFW 预览" 开关 / 阈值），
// 用户在那里关掉即对选择器生效。拉取失败时退回安全默认（开、R 级）。
let nsfwCfg = { blur: true, threshold: NSFW_BLUR_THRESHOLD };
let pickerEnabled = true;  // 管理器设置 → 界面 → 画布 → 模型选择器悬浮按钮
async function refreshNsfwCfg() {
    try {
        const r = await fetch("/api/noctyra/settings");
        const d = await r.json();
        const s = d?.settings || {};
        pickerEnabled = s.canvas_picker_enabled !== false;                 // 默认开
        nsfwCfg = {
            blur: s.blur_nsfw !== false,                                   // 默认开
            threshold: parseInt(s.nsfw_blur_threshold) || NSFW_BLUR_THRESHOLD,
            sfwOnly: !!s.show_only_sfw,                                     // 仅显示 SFW（隐藏 NSFW）
        };
    } catch (_) { /* 用默认值 */ }
}

// ---- "仅显示 SFW"跨页同步：与模型页/工作流页共用一个状态 ----
// 切换时同时写 show_only_sfw + gallery_show_only_sfw（值相同）并广播；收到广播则更新已打开的面板。
const _sfwChan = (() => { try { return new BroadcastChannel("noctyra-sfw"); } catch (_) { return null; } })();
let _activeSfwApply = null;   // 当前打开的选择器面板提供：(value) => 更新按钮 + 重渲染
async function setSfwEverywhere(value) {
    value = !!value;
    nsfwCfg.sfwOnly = value;   // 同步更新本地，后续 render 立即生效
    try {
        await fetch("/api/noctyra/settings", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ show_only_sfw: value, gallery_show_only_sfw: value }),
        });
    } catch (_) { /* 网络失败也广播，至少各页 UI 一致 */ }
    try { if (_sfwChan) _sfwChan.postMessage(value); } catch (_) {}
    try { localStorage.setItem("noctyra_sfw_only", value ? "1" : "0"); } catch (_) {}
}
function _applyExternalSfw(value) {
    value = !!value;
    nsfwCfg.sfwOnly = value;
    if (_activeSfwApply) _activeSfwApply(value);
}
if (_sfwChan) _sfwChan.addEventListener("message", (e) => _applyExternalSfw(e.data));
window.addEventListener("storage", (e) => { if (e.key === "noctyra_sfw_only") _applyExternalSfw(e.newValue === "1"); });

// 各 loader 的模型 widget 名 → 类型标签。显式白名单，避免误伤 sampler_name 等。
const WIDGET_LABELS = {
    ckpt_name: "大模型",
    lora_name: "LoRA",
    vae_name: "VAE",
    control_net_name: "ControlNet",
    unet_name: "UNet",
    clip_name: "CLIP",
    clip_name1: "CLIP",
    clip_name2: "CLIP",
    clip_name3: "CLIP",
    style_model_name: "风格模型",
    upscale_model_name: "放大模型",
    gligen_name: "GLIGEN",
};
// Lora Loader Stack (rgthree) 等"堆叠"节点:lora_01 ~ lora_10 都是 LoRA 槽(标准 combo,带 None)
for (let i = 1; i <= 10; i++) WIDGET_LABELS[`lora_${String(i).padStart(2, "0")}`] = "LoRA";

// 槽位配色(与 CSS 的 .ntp-sc-0..5 一致),给选中卡片光晕用
const SLOT_COLORS = ["#2d7ff9", "#34d399", "#fbbf24", "#ec4899", "#a78bfa", "#f87171"];

const NOCTYRA_LOGO = `<svg viewBox="0 0 24 24" fill="currentColor"><defs><mask id="ntpm"><circle cx="12" cy="12" r="8" fill="#fff"/><circle cx="15" cy="9" r="7.2" fill="#000"/></mask></defs><circle cx="12" cy="12" r="8" mask="url(#ntpm)"/><circle cx="20" cy="4" r="0.9"/><circle cx="4.5" cy="20" r="0.7" opacity="0.7"/></svg>`;
const PH_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.6"/><path d="m21 15-5-5L5 21"/></svg>`;
// 库内指示：两个上扬 chevron（右键看触发词）
const ARROWS_SVG = `<svg viewBox="0 0 22 14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 8 11 3 19 8"/><polyline points="3 12 11 7 19 12"/></svg>`;

const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const baseName = (p) => String(p).replace(/\\/g, "/").split("/").pop();

// ---- 模型卡片右键菜单（管理器详情 / 复制触发词 / 复制文件名）----
const IC_DETAIL = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`;
const IC_COPY = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;

let _cardMenuEl = null;
function closeCardMenu() {
    if (_cardMenuEl) { _cardMenuEl.remove(); _cardMenuEl = null; }
    document.removeEventListener("click", _onDocClickForMenu, true);
    document.removeEventListener("keydown", _onKeyForMenu, true);
}
function _onDocClickForMenu(e) {
    if (_cardMenuEl && !_cardMenuEl.contains(e.target)) {
        e.stopPropagation();   // 这次点击只用于关菜单，不穿透（避免误选卡片/误关面板）
        closeCardMenu();
    }
}
function _onKeyForMenu(e) { if (e.key === "Escape") { e.stopPropagation(); closeCardMenu(); } }

function pickerToast(msg, ok = true) {
    const t = document.createElement("div");
    t.className = "ntp-toast" + (ok ? "" : " ntp-toast-err");
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 1300);
}
async function _copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_) { return false; }
}

// x,y = 右键位置；val = 卡片模型值；meta = 库内元数据（未匹配则 undefined）
function showCardMenu(x, y, val, meta) {
    closeCardMenu();
    const fname = baseName(val);
    const hasFile = !!(meta && meta.file_path);
    const words = (meta && Array.isArray(meta.trained_words)) ? meta.trained_words.filter(Boolean) : [];
    const hasWords = words.length > 0;

    const menu = document.createElement("div");
    menu.className = "ntp-cardmenu";
    menu.innerHTML =
        `<div class="ntp-cmi${hasFile ? "" : " disabled"}" data-act="detail" title="${hasFile ? "" : "该模型不在库中"}">${IC_DETAIL}<span>在管理器中打开详情</span></div>`
      + `<div class="ntp-cmi${hasWords ? "" : " disabled"}" data-act="copytrig" title="${hasWords ? "" : "无触发词"}">${IC_COPY}<span>复制触发词${hasWords ? ` (${words.length})` : ""}</span></div>`
      + `<div class="ntp-cmi-sep"></div>`
      + `<div class="ntp-cmi" data-act="copyname">${IC_COPY}<span>复制文件名</span></div>`;
    document.body.appendChild(menu);
    _cardMenuEl = menu;

    const mw = menu.offsetWidth, mh = menu.offsetHeight, m = 8;
    menu.style.left = Math.max(m, Math.min(x, window.innerWidth - mw - m)) + "px";
    menu.style.top = Math.max(m, Math.min(y, window.innerHeight - mh - m)) + "px";

    menu.addEventListener("click", async (e) => {
        const item = e.target.closest(".ntp-cmi");
        if (!item || item.classList.contains("disabled")) return;
        const act = item.dataset.act;
        if (act === "detail" && hasFile) {
            window.open(`/noctyra-manager?model=${encodeURIComponent(meta.file_path)}`, "_blank", "noopener");
        } else if (act === "copytrig" && hasWords) {
            const ok = await _copyText(words.join(", "));
            pickerToast(ok ? "已复制触发词" : "复制失败", ok);
        } else if (act === "copyname") {
            const ok = await _copyText(fname);
            pickerToast(ok ? "已复制文件名" : "复制失败", ok);
        }
        closeCardMenu();
    });
    setTimeout(() => {
        document.addEventListener("click", _onDocClickForMenu, true);
        document.addEventListener("keydown", _onKeyForMenu, true);
    }, 0);
}

// ---- 样式 ----
function injectStyles() {
    if (document.getElementById("noctyra-picker-styles")) return;
    const style = document.createElement("style");
    style.id = "noctyra-picker-styles";
    style.textContent = `
/* 悬浮按钮 */
.ntp-fab {
    position: fixed; right: 18px; bottom: 110px; z-index: 2147483500;
    width: 50px; height: 50px; border-radius: 15px;
    display: flex; align-items: center; justify-content: center;
    background: #23232b; border: 1px solid #2c2c34; color: #4b4b56;
    box-shadow: 0 6px 20px rgba(0,0,0,0.4); cursor: grab;
    opacity: 0.5; transition: opacity .22s, box-shadow .22s, background-color .22s, border-color .22s;
    font-family: 'HarmonyOS Sans SC', 'HarmonyOS Sans', 'Inter', -apple-system, sans-serif; user-select: none;
}
.ntp-fab svg { width: 26px; height: 26px; }
/* 绝对铺满，避免 flex 下 width:100% 偶发被压小 */
.ntp-fab-icon { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; border-radius: 15px; display: block; }
.ntp-fab:not(.active) .ntp-fab-icon { filter: grayscale(0.85); }
.ntp-fab.active {
    opacity: 1; cursor: pointer; color: #fff; border-color: transparent;
    background: linear-gradient(135deg, #1d6bf0 0%, #2d7ff9 55%, #6ba8fd 100%);
    box-shadow: 0 6px 22px rgba(45,127,249,0.5), 0 0 0 1px rgba(45,127,249,0.3);
    animation: ntp-glow 2s ease-in-out infinite;
}
.ntp-fab.active:hover { transform: translateY(-3px) scale(1.05); box-shadow: 0 12px 30px rgba(45,127,249,0.6); }
@keyframes ntp-glow {
    0%,100% { box-shadow: 0 6px 22px rgba(45,127,249,0.45), 0 0 0 1px rgba(45,127,249,0.3); }
    50% { box-shadow: 0 6px 28px rgba(45,127,249,0.7), 0 0 0 3px rgba(45,127,249,0.18); }
}
.ntp-fab-tag {
    position: absolute; top: -8px; right: -8px; min-width: 16px; height: 17px; padding: 0 5px;
    font-size: 9px; font-weight: 700; border-radius: 10px; display: none;
    align-items: center; justify-content: center; box-shadow: 0 2px 6px rgba(0,0,0,0.4);
}
.ntp-fab.active .ntp-fab-tag { display: flex; background: #16161a; color: #6fb0ff; border: 1px solid #2d7ff9; }
.ntp-fab.dragging { cursor: grabbing !important; transition: none !important; }
/* 库内指示箭头：FAB 上方两个上扬 chevron + 辉光脉冲（右键看触发词） */
.ntp-fab-arrows {
    position: absolute; left: 50%; top: -15px; transform: translateX(-50%);
    width: 22px; height: 14px; color: #6ba8fd; display: none; pointer-events: none;
    filter: drop-shadow(0 0 5px rgba(45,127,249,0.95));
    animation: ntp-arrow-bounce 1.3s ease-in-out infinite;
}
.ntp-fab.has-match .ntp-fab-arrows { display: block; }
.ntp-fab-arrows svg { width: 100%; height: 100%; display: block; }
@keyframes ntp-arrow-bounce {
    0%, 100% { transform: translateX(-50%) translateY(1px); opacity: 0.65; }
    50% { transform: translateX(-50%) translateY(-3px); opacity: 1; }
}
/* 触发词面板 */
.ntp-trig-pop {
    position: fixed; z-index: 2147483560; width: 300px; max-height: 56vh; overflow-y: auto;
    background: #1c1c22; border: 1px solid #2c2c34; border-radius: 12px;
    box-shadow: 0 16px 44px rgba(0,0,0,0.62); padding: 10px;
    font-family: 'HarmonyOS Sans SC', 'HarmonyOS Sans', 'Inter', -apple-system, sans-serif; animation: ntp-fade .12s ease;
    scrollbar-width: thin; scrollbar-color: #33333c transparent;
}
.ntp-trig-pop::-webkit-scrollbar { width: 8px; }
.ntp-trig-pop::-webkit-scrollbar-track { background: transparent; }
.ntp-trig-pop::-webkit-scrollbar-thumb { background: #2c2c34; border-radius: 4px; border: 2px solid #1c1c22; }
.ntp-trig-pop::-webkit-scrollbar-thumb:hover { background: #3a3a44; }
.ntp-trig-more { font-size: 12px; font-weight: 700; padding: 3px 9px; border-radius: 6px; background: #26262e; color: #8a8a94; cursor: help; letter-spacing: 1px; }
.ntp-trig-warn { font-size: 10.5px; color: #fbbf24; margin: 1px 0 6px; line-height: 1.35; }
.ntp-trig-model { display: flex; gap: 10px; padding: 4px 4px 8px; }
.ntp-trig-model + .ntp-trig-model { border-top: 1px solid #26262e; margin-top: 4px; padding-top: 10px; }
.ntp-trig-thumb { width: 48px; height: 64px; flex-shrink: 0; object-fit: cover; border-radius: 6px; background: #16161a; }
.ntp-trig-ph { display: flex; align-items: center; justify-content: center; color: #3f3f4a; }
.ntp-trig-ph svg { width: 22px; height: 22px; }
.ntp-trig-body { flex: 1; min-width: 0; }
.ntp-trig-name { font-size: 12px; font-weight: 600; color: #ececee; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ntp-trig-base { display: inline-block; margin: 4px 0 7px; font-size: 9px; font-weight: 700; padding: 2px 6px; border-radius: 4px; background: rgba(45,127,249,0.15); color: #6fb0ff; letter-spacing: .03em; text-transform: uppercase; }
/* 槽位类型标签:多模型节点的触发词面板里标清每个模型是 Checkpoint / CLIP / LoRA */
.ntp-trig-slot { display: inline-block; margin: 4px 6px 0 0; font-size: 9px; font-weight: 700; padding: 2px 7px; border-radius: 4px; background: rgba(255,255,255,0.08); color: #c7c7d0; letter-spacing: .03em; }
.ntp-trig-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.ntp-trig-chip { font-size: 11px; padding: 3px 8px; border-radius: 6px; background: #26262e; color: #cfcfd6; cursor: pointer; transition: background .12s, color .12s; }
.ntp-trig-chip:hover { background: rgba(45,127,249,0.25); color: #fff; }
.ntp-trig-chip.copied { background: #22c55e; color: #fff; }
.ntp-trig-empty { font-size: 11px; color: #5e5e68; }
.ntp-trig-copyall { margin-top: 8px; font-size: 11px; padding: 4px 11px; border-radius: 6px; cursor: pointer;
    border: 1px solid #2d7ff9; background: rgba(45,127,249,0.14); color: #6fb0ff; transition: background .12s, color .12s; }
.ntp-trig-copyall:hover { background: rgba(45,127,249,0.28); color: #fff; }
.ntp-trig-copyall.copied { background: #22c55e; border-color: #22c55e; color: #fff; }
/* 选择器面板（对齐 manager 设计） */
.ntp-overlay {
    position: fixed; inset: 0; z-index: 2147483600;
    background: rgba(8,8,11,0.66); backdrop-filter: blur(5px); -webkit-backdrop-filter: blur(5px);
    display: flex; align-items: center; justify-content: center;
    font-family: 'HarmonyOS Sans SC', 'HarmonyOS Sans', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    animation: ntp-fade .14s ease;
}
@keyframes ntp-fade { from { opacity: 0 } to { opacity: 1 } }
.ntp-panel {
    width: min(1120px, 92vw); height: min(82vh, 840px);
    background: #18181c; border: 1px solid #2c2c34; border-radius: 18px;
    box-shadow: 0 30px 90px rgba(0,0,0,0.66), 0 0 0 1px rgba(255,255,255,0.02); display: flex; flex-direction: column; overflow: hidden;
}
.ntp-head {
    display: flex; align-items: center; gap: 12px; padding: 15px 18px; flex-shrink: 0;
    border-bottom: 1px solid #1c1c22;
    background: linear-gradient(180deg, #1d1d24 0%, #18181c 100%);
}
.ntp-logo {
    width: 26px; height: 26px; border-radius: 7px; flex-shrink: 0; overflow: hidden;
    display: flex; align-items: center; justify-content: center;
}
.ntp-logo svg { width: 16px; height: 16px; }
.ntp-logo-img { width: 100%; height: 100%; object-fit: cover; display: block; }
.ntp-title { font-size: 15px; font-weight: 700; color: #ececee; letter-spacing: -0.01em; white-space: nowrap; }
.ntp-title b { background: linear-gradient(135deg,#2d7ff9,#6ba8fd); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
.ntp-count { font-size: 12px; color: #5e5e68; font-weight: 500; white-space: nowrap; }
.ntp-search {
    flex: 1; min-width: 90px; padding: 9px 13px; font-size: 13px; background: #16161a;
    border: 1px solid #27272e; border-radius: 10px; color: #ececee; outline: none; font-family: inherit;
    transition: border-color .15s, box-shadow .15s;
}
.ntp-search::placeholder { color: #5e5e68; }
.ntp-search:focus { border-color: #2d7ff9; box-shadow: 0 0 0 3px rgba(45,127,249,0.16); }
.ntp-select {
    padding: 9px 11px; font-size: 12px; background: #16161a; border: 1px solid #27272e; border-radius: 10px;
    color: #a0a0a8; outline: none; font-family: inherit; max-width: 170px; cursor: pointer; transition: border-color .15s;
}
.ntp-select:focus, .ntp-select:hover { border-color: #3a3a44; }
.ntp-sfw {
    padding: 9px 12px; font-size: 12px; font-weight: 600; flex-shrink: 0; cursor: pointer;
    background: #16161a; border: 1px solid #27272e; border-radius: 10px; color: #a0a0a8;
    font-family: inherit; white-space: nowrap; transition: all .15s;
}
.ntp-sfw { letter-spacing: .06em; }
.ntp-sfw:hover:not(.active) { border-color: #3a3a44; color: #ececee; }
.ntp-sfw.active { background: rgba(244,63,94,0.16); border-color: rgba(244,63,94,0.45); color: #f87171; }
.ntp-close {
    width: 34px; height: 34px; flex-shrink: 0; border: 1px solid #27272e; cursor: pointer;
    background: transparent; color: #a0a0a8; border-radius: 10px; font-size: 19px; line-height: 1;
    display: flex; align-items: center; justify-content: center; transition: all .15s;
}
.ntp-close:hover { background: rgba(244,63,94,0.14); color: #f87171; border-color: rgba(244,63,94,0.35); }
/* 多槽:左侧槽位侧边栏 + 右侧候选 */
.ntp-body { flex: 1; display: flex; min-height: 0; }
.ntp-slots {
    flex: 0 0 196px; width: 196px; overflow-y: auto;
    border-right: 1px solid #1c1c22; padding: 10px;
    display: flex; flex-direction: column; gap: 6px;
}
.ntp-slot {
    display: flex; align-items: center; gap: 8px; padding: 9px 10px;
    border-radius: 10px; cursor: pointer; border: 1px solid transparent;
    transition: background .12s, border-color .12s;
}
.ntp-slot:hover { background: #1e1e24; }
.ntp-slot.active { background: rgba(45,127,249,0.14); border-color: rgba(45,127,249,0.4); }
.ntp-slot-main { flex: 1; min-width: 0; }
.ntp-slot-label { font-size: 12px; font-weight: 700; color: #ececee; }
.ntp-slot.active .ntp-slot-label { color: #6fb0ff; }
.ntp-slot-val { font-size: 11px; color: #5e5e68; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
.ntp-slot-x {
    flex-shrink: 0; width: 20px; height: 20px; border: none; background: transparent;
    color: #5e5e68; border-radius: 6px; cursor: pointer; font-size: 15px; line-height: 1;
    display: flex; align-items: center; justify-content: center;
}
.ntp-slot-x:hover { background: rgba(244,63,94,0.16); color: #f87171; }
.ntp-slot-add {
    margin-top: 4px; padding: 10px; border: 1px dashed #3a3a44; background: transparent;
    color: #a0a0a8; border-radius: 10px; cursor: pointer; font-size: 13px; font-weight: 600;
    font-family: inherit; transition: all .12s; flex-shrink: 0;
}
.ntp-slot-add:hover { border-color: #2d7ff9; color: #6fb0ff; background: rgba(45,127,249,0.08); }
.ntp-grid {
    flex: 1; min-width: 0; overflow-y: auto; padding: 16px;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(158px, 1fr)); gap: 14px; align-content: start;
    /* 关键：ComfyUI 的 flex 布局环境里 grid-auto-rows:auto 会把行压成 ~96px，
       导致封面（比例撑高的 thumb）被 overflow:hidden 裁成横条。必须显式 max-content
       强制每行按卡片完整内容撑开，否则竖图塌成横条。实测验证。 */
    grid-auto-rows: max-content;
}
.ntp-grid::-webkit-scrollbar { width: 8px; }
.ntp-grid::-webkit-scrollbar-track { background: transparent; }
.ntp-grid::-webkit-scrollbar-thumb { background: #27272e; border-radius: 4px; }
.ntp-grid::-webkit-scrollbar-thumb:hover { background: #3a3a44; }
.ntp-card {
    background: #23232b; border: 1px solid #2a2a33; border-radius: 16px; overflow: hidden; cursor: pointer; position: relative;
    min-width: 0; /* grid item 默认 min-width:auto，长文件名会撑爆列宽 → 必须归零 */
    transition: transform .22s cubic-bezier(.34,1.2,.64,1), box-shadow .2s; box-shadow: 0 1px 3px rgba(0,0,0,0.25);
}
.ntp-card:hover { transform: translateY(-5px); box-shadow: 0 18px 42px rgba(0,0,0,0.55), 0 0 0 2px #2d7ff9; z-index: 1; }
/* 选中卡片:用当前槽颜色的光晕(--ntp-glow 由 JS 按槽设),环 + 柔外晕 */
.ntp-card.current {
    border-color: transparent;
    box-shadow: 0 0 0 2px var(--ntp-glow, #2d7ff9),
                0 0 24px -3px var(--ntp-glow, #2d7ff9),
                0 8px 24px rgba(0,0,0,0.38);
}
.ntp-card.current:hover { transform: translateY(-5px); }
/* 槽位序号标记:被某槽选中的模型角标该槽序号(①②③…),各槽不同色;侧边栏圆点同色呼应 */
.ntp-sbadges { position: absolute; top: 8px; right: 8px; z-index: 3; display: flex; gap: 4px; flex-wrap: wrap; justify-content: flex-end; max-width: calc(100% - 16px); }
.ntp-sbadge {
    min-width: 22px; height: 22px; padding: 0 5px; border-radius: 11px;
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; font-weight: 800; color: #fff;
    box-shadow: 0 2px 6px rgba(0,0,0,0.45); border: 1.5px solid rgba(0,0,0,0.22);
}
.ntp-sbadge-active { outline: 2px solid #fff; outline-offset: 1px; }
.ntp-slot-dot { flex-shrink: 0; width: 9px; height: 9px; border-radius: 50%; }
/* 槽位配色(循环 6 色) */
.ntp-sc-0 { background: #2d7ff9; }
.ntp-sc-1 { background: #34d399; }
.ntp-sc-2 { background: #fbbf24; color: #1a1a1f; }
.ntp-sc-3 { background: #ec4899; }
.ntp-sc-4 { background: #a78bfa; }
.ntp-sc-5 { background: #f87171; }
.ntp-thumb-wrap { width: 100%; padding-bottom: 108%; overflow: hidden; background: #16161a; position: relative; }
.ntp-thumb { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; transition: transform .4s cubic-bezier(.4,0,.2,1), filter .3s ease; }
.ntp-vid-badge { position: absolute; bottom: 6px; right: 6px; z-index: 2; font-size: 9px; line-height: 1.4; color: #fff; background: rgba(0,0,0,0.55); border-radius: 4px; padding: 1px 5px; pointer-events: none; }
.ntp-thumb-wrap.ntp-nsfw .ntp-thumb { filter: blur(20px); }
.ntp-card:hover .ntp-thumb-wrap.ntp-nsfw .ntp-thumb { filter: blur(0); }
.ntp-nsfw-tag { position: absolute; top: 7px; left: 7px; z-index: 2; font-size: 9px; font-weight: 800; letter-spacing: .04em; padding: 2px 6px; border-radius: 5px; background: rgba(244,63,94,0.92); color: #fff; box-shadow: 0 2px 6px rgba(0,0,0,0.4); pointer-events: none; transition: opacity .2s; }
.ntp-card:hover .ntp-nsfw-tag { opacity: 0.45; }
.ntp-card:hover .ntp-thumb { transform: scale(1.05); }
.ntp-ph { position: absolute; inset: 0; width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg,#26262f 0%,#1a1a20 100%); color: #3f3f4a; }
.ntp-ph svg { width: 38px; height: 38px; opacity: 0.7; }
.ntp-info { padding: 6px 9px 8px; min-width: 0; }
.ntp-name {
    font-size: 12px; font-weight: 600; color: #ececee; line-height: 1.32; letter-spacing: -0.01em;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    overflow-wrap: anywhere; /* 长文件名（无空格）能断行，否则撑宽卡片破坏 grid */
    min-height: calc(1.32em * 2); /* 预留两行高度：1 行 / 2 行名称的卡片下沿对齐，行距不参差（仍 line-clamp:2） */
}
.ntp-meta { margin-top: 6px; display: flex; align-items: center; gap: 5px; flex-wrap: wrap; min-height: 16px; }
.ntp-badge { font-size: 9px; font-weight: 700; padding: 2px 7px; border-radius: 4px; background: rgba(45,127,249,0.15); color: #6fb0ff; letter-spacing: .03em; text-transform: uppercase; }
.ntp-fav { color: #fbbf24; font-size: 12px; line-height: 1; }
.ntp-empty { grid-column: 1/-1; text-align: center; color: #5e5e68; padding: 70px 20px; font-size: 14px; line-height: 1.7; }
.ntp-spinner { width: 30px; height: 30px; margin: 0 auto 14px; border-radius: 50%; border: 3px solid #27272e; border-top-color: #2d7ff9; animation: ntp-spin .7s linear infinite; }
@keyframes ntp-spin { to { transform: rotate(360deg) } }
.ntp-foot { padding: 9px 18px; border-top: 1px solid #1c1c22; color: #5e5e68; font-size: 11px; flex-shrink: 0; }
.ntp-foot b { color: #a0a0a8; font-weight: 600; }
.ntp-foot.err { color: #f87171; }
/* ---- 图像模式（选中 LoadImage 节点）：FAB 换紫色，和模型模式蓝色区分 ---- */
.ntp-fab.img-mode.active {
    background: linear-gradient(135deg, #7c3aed 0%, #a855f7 55%, #c4b5fd 100%);
    box-shadow: 0 6px 22px rgba(168,85,247,0.5), 0 0 0 1px rgba(168,85,247,0.3);
    animation: ntp-glow-v 2s ease-in-out infinite;
}
.ntp-fab.img-mode.active:hover { box-shadow: 0 12px 30px rgba(168,85,247,0.6); }
@keyframes ntp-glow-v {
    0%,100% { box-shadow: 0 6px 22px rgba(168,85,247,0.45), 0 0 0 1px rgba(168,85,247,0.3); }
    50% { box-shadow: 0 6px 28px rgba(168,85,247,0.7), 0 0 0 3px rgba(168,85,247,0.18); }
}
.ntp-fab.img-mode.active .ntp-fab-tag { color: #d8b4fe; border-color: #a855f7; }
/* 选图器头部：收藏过滤药丸 */
.ntp-favp {
    padding: 9px 12px; font-size: 14px; line-height: 1; flex-shrink: 0; cursor: pointer;
    background: #16161a; border: 1px solid #27272e; border-radius: 10px; color: #a0a0a8;
    font-family: inherit; transition: all .15s;
}
.ntp-favp:hover:not(.active) { border-color: #3a3a44; color: #ececee; }
.ntp-favp.active { background: rgba(236,72,153,0.16); border-color: rgba(236,72,153,0.45); color: #ec4899; }
/* 选图卡片"送入 input"中的 loading 态 */
.ntp-card.ntp-loading { opacity: .55; pointer-events: none; }
.ntp-card.ntp-loading::after {
    content: ''; position: absolute; top: 50%; left: 50%; width: 26px; height: 26px; margin: -13px 0 0 -13px;
    border-radius: 50%; border: 3px solid rgba(255,255,255,.22); border-top-color: #a855f7;
    animation: ntp-spin .7s linear infinite; z-index: 3;
}
/* 模型卡片右键菜单 */
.ntp-cardmenu {
    position: fixed; z-index: 2147483640; min-width: 196px;
    background: rgba(24,24,28,0.98); border: 1px solid rgba(255,255,255,0.1);
    border-radius: 10px; padding: 6px; box-shadow: 0 18px 46px rgba(0,0,0,0.62);
    font-size: 13px; color: #ececee; backdrop-filter: blur(10px);
    animation: ntp-menu-in .12s ease;
}
@keyframes ntp-menu-in { from { opacity: 0; transform: scale(.97); } to { opacity: 1; transform: none; } }
.ntp-cmi { display: flex; align-items: center; gap: 9px; padding: 8px 10px; border-radius: 6px; cursor: pointer; white-space: nowrap; }
.ntp-cmi:hover { background: rgba(45,127,249,0.2); }
.ntp-cmi.disabled { opacity: .4; cursor: default; pointer-events: none; }
.ntp-cmi svg { flex-shrink: 0; opacity: .85; }
.ntp-cmi-sep { height: 1px; margin: 5px 6px; background: rgba(255,255,255,0.09); }
/* 复制反馈 toast */
.ntp-toast {
    position: fixed; left: 50%; top: 40px; transform: translateX(-50%);
    z-index: 2147483645; background: rgba(34,197,94,0.96); color: #fff;
    padding: 8px 18px; border-radius: 8px; font-size: 13px; font-weight: 600;
    box-shadow: 0 8px 26px rgba(0,0,0,0.45); animation: ntp-toast-in .2s ease; pointer-events: none;
}
.ntp-toast-err { background: rgba(244,63,94,0.96); }
@keyframes ntp-toast-in { from { opacity: 0; transform: translate(-50%,-8px); } to { opacity: 1; transform: translate(-50%,0); } }
`;
    document.head.appendChild(style);
}

// ---- 选择器面板 ----
// 同类型多槽加序号(DualCLIP 的两个 CLIP → "CLIP 1"/"CLIP 2")
function slotLabels(widgets) {
    const count = {};
    widgets.forEach((w) => { const l = WIDGET_LABELS[w.name] || "模型"; count[l] = (count[l] || 0) + 1; });
    const seen = {};
    return widgets.map((w) => {
        const l = WIDGET_LABELS[w.name] || "模型";
        if (count[l] > 1) { seen[l] = (seen[l] || 0) + 1; return `${l} ${seen[l]}`; }
        return l;
    });
}
// widget 的候选里是否有"无/None"值(combo 不能真空,清除=设成这个;没有则不显示清除键)
function noneValueOf(widget) {
    const vals = widget.options?.values || [];
    return vals.find((v) => v === "None" || v === "none" || v === "" || v === "undefined") ?? null;
}

// ---- Power Lora Loader (rgthree) 特判:它用 addCustomWidget 的自定义 widget,不是标准 combo ----
function getPowerLoraWidgets(node) {
    // 只有 lora 行 widget 才有 setLora 方法(头/分隔/按钮 widget 没有),据此精确挑出
    return (node?.widgets || []).filter((w) => w && typeof w.setLora === "function");
}
function isPowerLoraNode(node) {
    if (!node) return false;
    if ((node.comfyClass || node.type || "").includes("Power Lora Loader")) return true;
    return getPowerLoraWidgets(node).length > 0;
}
// 候选 lora 列表:用 ComfyUI 原生 /object_info/LoraLoader(和 rgthree 同源 folder_paths，命名精确对得上)
let _loraListCache = null;
async function fetchLoraList() {
    if (_loraListCache) return _loraListCache;
    try {
        const r = await fetch("/object_info/LoraLoader");
        const d = await r.json();
        const arr = d?.LoraLoader?.input?.required?.lora_name?.[0] || [];
        _loraListCache = arr.filter((v) => typeof v === "string");
    } catch (e) { _loraListCache = []; }
    return _loraListCache;
}
// 把 rgthree 的自定义 lora widget 包成选择器能用的"伪 widget"(value 读写进 .lora)
function loraPseudoWidget(realW, candidates) {
    return {
        name: "lora_name",              // → WIDGET_LABELS["lora_name"] = "LoRA"
        options: { values: candidates },
        get value() { return realW.value?.lora || ""; },
        set value(v) {
            const lv = v === "None" ? null : v;
            // rgthree 版本差异:正常用 setLora;若该 widget 无此方法(如刚 addNewLoraWidget 的新行),
            // 退回直接改 value.lora,避免静默失效
            if (typeof realW.setLora === "function") realW.setLora(lv);
            else if (realW.value && typeof realW.value === "object") realW.value.lora = lv;
        },
        _real: realW,
    };
}
async function openPowerLoraPicker(node) {
    const list = await fetchLoraList();
    const candidates = list.length ? ["None", ...list] : list;   // None 放最前,用于清空槽
    const pseudos = getPowerLoraWidgets(node).map((w) => loraPseudoWidget(w, candidates));
    const onAddSlot = () => {
        if (typeof node.addNewLoraWidget !== "function") return null;   // rgthree 版本无此 API → 不加槽(按钮静默无效)
        const w = node.addNewLoraWidget();    // rgthree:加一行 lora widget
        if (!w) return null;
        try { node.size[1] = Math.max(node._tempHeight || 15, node.computeSize()[1]); } catch (_) {}
        node.setDirtyCanvas(true, true);
        return loraPseudoWidget(w, candidates);
    };
    openPicker(node, pseudos, pseudos.length ? 0 : -1, { onAddSlot });
}

// 模型值容错比较:画布各来源存值格式不一(原生 combo 存完整名 / rgthree Power Lora 存 basename /
// Windows 反斜杠路径 / 大小写),严格 === 会让"当前已选"漏标(数字角标、光晕都不出现)。
// 顺序:精确 → 归一化(反斜杠→正斜杠 + 去空格 + 小写) → basename 兜底(同一文件不同存法都判同;
// 极少数"同名不同目录"会一起标记,但远好于严格相等整张漏标)。
function sameModelValue(a, b) {
    if (a == null || b == null) return false;
    if (a === b) return true;
    const norm = (s) => String(s).replace(/\\/g, "/").trim().toLowerCase();
    const na = norm(a), nb = norm(b);
    if (na === nb) return true;
    // basename 兜底:同一文件不同存法(完整名/裸名/不同目录前缀)都判同。极少数"同名不同目录"
    // 会一起标记,但比"严格相等漏标整张卡"好得多,且画布上同一槽候选里同名文件本就罕见。
    return na.split("/").pop() === nb.split("/").pop();
}

// 多槽选择器:左侧槽位侧边栏(切槽 + 清除 + 可选「添加槽」),右侧候选卡片。单槽时不显示侧边栏、选完即关。
async function openPicker(node, widgets, activeIdx = 0, opts = {}) {
    injectStyles();
    document.querySelectorAll(".ntp-overlay").forEach((o) => o.remove());
    if (!Array.isArray(widgets)) widgets = [widgets];
    widgets = widgets.filter(Boolean);
    const addable = !!opts.onAddSlot;
    if (!widgets.length && !addable) return;
    // NSFW 设置只在打开面板时拉一次（对齐 openMediaPicker :911）；多槽切换不再各拉一次 /settings。
    await refreshNsfwCfg();
    let labels = slotLabels(widgets);
    const multi = widgets.length > 1 || addable;
    let active = widgets.length ? Math.max(0, Math.min(activeIdx, widgets.length - 1)) : -1;

    const overlay = document.createElement("div");
    overlay.className = "ntp-overlay";
    overlay.innerHTML = `
        <div class="ntp-panel">
            <div class="ntp-head">
                <span class="ntp-logo"><img class="ntp-logo-img" src="/noctyra_static/images/noctyra-logo.svg" alt=""></span>
                <span class="ntp-title"><b>Noctyra</b> · <span class="ntp-title-slot"></span></span>
                <span class="ntp-count"></span>
                <input class="ntp-search" type="text" placeholder="搜索文件名 / 模型名…">
                <select class="ntp-select"><option value="">全部基础模型</option></select>
                <button class="ntp-sfw" type="button" title="点击切换是否显示 NSFW（红色=已隐藏）">NSFW</button>
                <button class="ntp-close" title="关闭 (Esc)">×</button>
            </div>
            <div class="ntp-body">
                ${multi ? '<div class="ntp-slots"></div>' : ""}
                <div class="ntp-grid"></div>
            </div>
            <div class="ntp-foot">点击卡片填入当前槽${multi ? " · 左侧切换槽位 / × 清除" : ""} · <b>Esc</b> 关闭</div>
        </div>`;
    document.body.appendChild(overlay);

    const grid = overlay.querySelector(".ntp-grid");
    const searchEl = overlay.querySelector(".ntp-search");
    const selectEl = overlay.querySelector(".ntp-select");
    const sfwBtn = overlay.querySelector(".ntp-sfw");
    const slotsEl = overlay.querySelector(".ntp-slots");
    const titleSlot = overlay.querySelector(".ntp-title-slot");
    const countEl = overlay.querySelector(".ntp-count");
    let vRO = null;   // 虚拟滚动的尺寸观察器,关闭时断开,防泄漏
    const close = () => { _activeSfwApply = null; if (vRO) vRO.disconnect(); closeCardMenu(); overlay.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
    document.addEventListener("keydown", onKey);
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
    overlay.querySelector(".ntp-close").addEventListener("click", close);

    const slotItems = {};   // 槽下标 -> 元数据 map(切槽缓存,不重复拉)

    function renderSlots() {
        if (!slotsEl) return;
        labels = slotLabels(widgets);   // 槽可能增加,重算序号标签
        slotsEl.innerHTML = "";
        widgets.forEach((w, i) => {
            const row = document.createElement("div");
            row.className = "ntp-slot" + (i === active ? " active" : "");
            const val = w.value;
            const none = noneValueOf(w);
            const showX = none != null && val && val !== none;
            row.innerHTML = `
                <span class="ntp-slot-dot ntp-sc-${i % 6}" title="槽 ${i + 1}"></span>
                <div class="ntp-slot-main">
                    <div class="ntp-slot-label">${esc(labels[i])}</div>
                    <div class="ntp-slot-val" title="${esc(val || "")}">${esc(val ? baseName(val) : "未选择")}</div>
                </div>
                ${showX ? '<button class="ntp-slot-x" title="清除此槽">×</button>' : ""}`;
            row.querySelector(".ntp-slot-main").addEventListener("click", () => loadSlot(i));
            const xb = row.querySelector(".ntp-slot-x");
            if (xb) xb.addEventListener("click", (e) => { e.stopPropagation(); assign(i, none); });
            slotsEl.appendChild(row);
        });
        // 可添加槽(Power Lora Loader):底部「＋ 添加 LoRA」
        if (opts.onAddSlot) {
            const add = document.createElement("button");
            add.className = "ntp-slot-add";
            add.textContent = "＋ 添加 LoRA";
            add.addEventListener("click", () => {
                const nw = opts.onAddSlot();
                if (nw) { widgets.push(nw); renderSlots(); loadSlot(widgets.length - 1); }
            });
            slotsEl.appendChild(add);
        }
    }

    function assign(i, val) {
        const w = widgets[i];
        w.value = val;
        if (w.callback) w.callback(val, app.canvas, node);
        app.graph?.setDirtyCanvas(true, true);
        renderSlots();
        if (i === active) render();   // 刷新当前格子的"已选"高亮
    }

    async function loadSlot(i) {
        if (i < 0 || i >= widgets.length) return;
        active = i;
        renderSlots();
        const w = widgets[i];
        const values = (w.options?.values || []).filter((v) => v != null);
        titleSlot.textContent = `选择${labels[i]}`;
        countEl.textContent = `${values.length} 项`;
        grid.style.display = "";   // 还原 CSS 的 grid 布局,让 spinner 居中(虚拟模式会改成 block)
        grid.innerHTML = `<div class="ntp-empty"><div class="ntp-spinner"></div>正在向 Noctyra 查询元数据…</div>`;
        if (!slotItems[i]) {
            try {
                slotItems[i] = (await fetchMatchItems(values)) || {};   // NSFW 配置已在 openPicker 开头拉过
            } catch (e) {
                console.warn("[Noctyra] picker 元数据获取失败，降级为纯列表:", e);
                slotItems[i] = {};
            }
        }
        if (!overlay.isConnected || active !== i) return;   // 期间关了/又切了别的槽
        const items = slotItems[i];
        const bases = [...new Set(values.map((v) => items[v]?.base_model).filter(Boolean))].sort();
        selectEl.innerHTML = '<option value="">全部基础模型</option>';
        for (const b of bases) {
            const o = document.createElement("option");
            o.value = b; o.textContent = b;
            selectEl.appendChild(o);
        }
        searchEl.value = "";
        render();
        // 定位:把该槽当前选中的模型滚到可视区中部(模型多时不用手找)
        scrollToCurrent();
        if (!vItems.some((it) => it.onSlots.includes(active)) && w.value && w.value !== noneValueOf(w)) {
            // 当前槽明明有值却没标记到任何卡片 → 多半是存值格式与候选项对不上。打印实际值,
            // 把控制台这行发我即可精准修(诊断用,正常匹配时不会出现)。
            console.warn("[Noctyra] 当前槽值未匹配到候选卡片 →", JSON.stringify(w.value),
                "| 候选样例:", values.slice(0, 4).map(String));
        }
        searchEl.focus({ preventScroll: true });
    }

    // ---- 虚拟滚动:候选上千个时,全量渲染会让所有视频缩略图同时自动播放 + 所有 NSFW 模糊滤镜
    //      同时生效,GPU 持续满载发卡。只渲染可视窗口的几十张 → 视频/模糊数量恒定,丝滑。----
    let vItems = [];                // 过滤后的卡片描述 [{ val, onSlots }]（静态片段另存 vFrags，选中态按可视卡现算）
    let vFrags = null;              // 当前槽的静态片段表 Map<val, thumb+info 的 HTML>（会话内不变）
    let vCols = 1, vColW = 158, vRowH = 220;
    const vGap = 14;                // 与 .ntp-grid 的 gap 一致
    let vp = null;                  // 内部 sizer(撑总高 + 绝对定位卡片)
    let vLastRange = "", vRaf = 0;
    const vCards = new Map();        // 下标 -> 已渲染卡片 DOM;滚动时复用(留在窗口里的不重建→视频不重载、不闪不卡)
    const vRows = () => Math.ceil(vItems.length / vCols);

    // ---- 静态片段缓存：每槽一份 Map<val, thumb+info 的 HTML> ----
    // 片段只依赖库内元数据与 NSFW 模糊配置（会话内不变），首次 loadSlot / render 建好即复用；
    // 选中态(sbadges/current/glow)不进片段——那随 assign 变，由 buildCardEl 只对可视卡现算。
    const slotFrags = {};
    function buildFrag(items, val) {
        const meta = items[val];
        const fname = baseName(val);
        const disp = meta?.name || fname;
        const purl = meta?.preview_url ? `/api/noctyra/preview?url=${encodeURIComponent(meta.preview_url)}` : "";
        const isVid = meta?.preview_type === "video";
        const isNsfwLevel = (meta?.nsfw_level || 0) >= nsfwCfg.threshold;
        const doBlur = nsfwCfg.blur && isNsfwLevel;
        const wrapCls = "ntp-thumb-wrap" + (doBlur ? " ntp-nsfw" : "");
        const nsfwTag = isNsfwLevel ? `<span class="ntp-nsfw-tag">18+</span>` : "";
        const media = !purl
            ? `<div class="ntp-ph">${PH_ICON}</div>`
            : (isVid
                ? `<img class="ntp-thumb" loading="lazy" src="${purl}&size=card" alt="" data-vsrc="${purl}"><span class="ntp-vid-badge">&#9654;</span>`
                : `<img class="ntp-thumb" loading="lazy" src="${purl}&size=card" alt="">`);   // 视频用首帧静态图（后端抽帧），hover 才播放
        const thumb = `<div class="${wrapCls}">${media}${nsfwTag}</div>`;
        const badge = meta?.base_model ? `<span class="ntp-badge">${esc(meta.base_model)}</span>` : "";
        const fav = meta?.favorite ? `<span class="ntp-fav">★</span>` : "";
        return `${thumb}<div class="ntp-info"><div class="ntp-name" title="${esc(fname)}">${esc(disp)}</div><div class="ntp-meta">${badge}${fav}</div></div>`;
    }
    function fragsFor(i) {
        if (slotFrags[i]) return slotFrags[i];
        const items = slotItems[i] || {};
        const vals = (widgets[i]?.options?.values || []).filter((v) => v != null);
        const map = new Map();
        for (const val of vals) if (!map.has(val)) map.set(val, buildFrag(items, val));
        slotFrags[i] = map;
        return map;
    }

    function render() {
        vCards.clear();             // 每次重渲染丢弃旧卡引用(旧 DOM 随下面的 innerHTML 替换一并销毁)
        const w = widgets[active];
        if (!w) {
            grid.style.display = "";
            grid.innerHTML = `<div class="ntp-empty">还没有 LoRA 槽<br><span style="font-size:12px">点左侧「＋ 添加 LoRA」加一个，再从这里选模型</span></div>`;
            vItems = []; vp = null; vFrags = null;
            return;
        }
        const values = (w.options?.values || []).filter((v) => v != null);
        const items = slotItems[active] || {};
        vFrags = fragsFor(active);   // 静态片段（会话内建一次）；render 只做过滤 + 拼接，不再逐卡拼 HTML
        const q = searchEl.value.trim().toLowerCase();
        const baseFilter = selectEl.value;
        // 每次 render 先 O(槽数) 预建"归一化 basename → 选中它的槽下标"查找表：sameModelValue 等价于
        // "两值非空且归一化 basename 相等"（精确/归一化相等都蕴含 basename 相等），据此可完全复刻其匹配，
        // 让每张卡的 selectedHere 从 O(槽数) 降为 O(1)（几千候选下这是键盘/筛选卡顿的大头）。
        const normBase = (s) => String(s).replace(/\\/g, "/").trim().toLowerCase().split("/").pop();
        const slotByBase = new Map();
        widgets.forEach((sw, si) => {
            if (sw.value == null) return;
            const k = normBase(sw.value);
            let arr = slotByBase.get(k);
            if (!arr) slotByBase.set(k, (arr = []));
            arr.push(si);   // 按槽序追加 → onSlots 天然升序，与原 forEach 顺序一致
        });
        // 过滤：选中态只存 onSlots 小数组，不在此拼角标 HTML；静态片段留给 buildCardEl 查 vFrags。
        vItems = [];
        for (const val of values) {
            const meta = items[val];
            if (baseFilter && meta?.base_model !== baseFilter) continue;
            // caveat：:728 的 sfwOnly 豁免作用于"全部 N 项"——当前选中的 NSFW 模型不能被整张滤掉，
            // 故 selectedHere 必须在此对全部候选算(O(1) 查表)，不能整体推迟到只跑可视卡的 buildCardEl。
            const onSlots = slotByBase.get(normBase(val)) || [];
            if (nsfwCfg.sfwOnly && meta?.nsfw && !onSlots.length) continue;
            const fname = baseName(val);
            const disp = meta?.name || fname;
            if (q && !fname.toLowerCase().includes(q) && !disp.toLowerCase().includes(q)) continue;
            vItems.push({ val, onSlots });
        }
        if (!vItems.length) {
            grid.style.display = "";
            grid.innerHTML = `<div class="ntp-empty">没有匹配的模型<br><span style="font-size:12px">换个关键词或筛选试试</span></div>`;
            vp = null;
            return;
        }
        const keep = grid.scrollTop;
        grid.style.display = "block";      // 覆盖 CSS 的 display:grid,改用绝对定位虚拟布局
        grid.innerHTML = `<div class="ntp-vp" style="position:relative;width:100%"></div>`;
        vp = grid.querySelector(".ntp-vp");
        measureLayout();                   // 先建 vp 再量:列宽要用 vp 内容宽(grid 有 16px padding)
        const totalH = vRows() * vRowH;
        vp.style.height = totalH + "px";
        grid.scrollTop = Math.min(keep, Math.max(0, totalH - grid.clientHeight));   // 替换 innerHTML 可能清零,恢复
        vLastRange = "";
        paintWindow();
    }

    function measureLayout() {
        const gw = (vp ? vp.clientWidth : grid.clientWidth) || 600;   // vp 内容宽 = grid 宽 - padding
        vCols = Math.max(1, Math.floor((gw + vGap) / (158 + vGap)));
        vColW = Math.floor((gw - (vCols - 1) * vGap) / vCols);
        // 探测一张卡真实高度(缩略图按 colW 的 108% + 信息条;measure 最稳,不受图片是否加载影响)
        const probe = document.createElement("div");
        probe.className = "ntp-card";
        probe.style.cssText = `position:absolute;left:-9999px;top:0;visibility:hidden;box-sizing:border-box;width:${vColW}px`;
        probe.innerHTML = (vFrags && vFrags.get(vItems[0].val)) || "";   // 片段足够测高（选中角标绝对定位，不影响卡高）
        (vp || grid).appendChild(probe);
        vRowH = (probe.offsetHeight || Math.round(vColW * 1.08 + 60)) + vGap;
        (vp || grid).removeChild(probe);
    }

    function buildCardEl(i) {
        const it = vItems[i];
        const r = Math.floor(i / vCols), c = i % vCols;
        // 选中态(isCur/glow/角标)只对可视 ~30 张现算；角标绝对定位，拼在静态片段末尾即可（DOM 次序不影响其位置）
        const isCur = it.onSlots.includes(active);
        const sbadges = it.onSlots.length
            ? `<div class="ntp-sbadges">${it.onSlots.map((si) =>
                `<span class="ntp-sbadge ntp-sc-${si % 6}${si === active ? " ntp-sbadge-active" : ""}" title="${esc(labels[si])}">${si + 1}</span>`
              ).join("")}</div>`
            : "";
        const el = document.createElement("div");
        el.className = "ntp-card" + (isCur ? " current" : "");
        el.dataset.val = it.val;
        el.style.cssText = `position:absolute;box-sizing:border-box;left:${c * (vColW + vGap)}px;top:${r * vRowH}px;width:${vColW}px`
            + (isCur ? `;--ntp-glow:${SLOT_COLORS[active % 6]}` : "");
        el.innerHTML = ((vFrags && vFrags.get(it.val)) || "") + sbadges;
        return el;
    }

    function paintWindow() {
        if (!vp) return;
        const overscan = 2;
        const firstRow = Math.max(0, Math.floor(grid.scrollTop / vRowH) - overscan);
        const lastRow = Math.min(vRows() - 1, Math.floor((grid.scrollTop + grid.clientHeight) / vRowH) + overscan);
        const range = firstRow + ":" + lastRow;
        if (range === vLastRange) return;
        vLastRange = range;
        const startI = firstRow * vCols;
        const endI = Math.min(vItems.length - 1, (lastRow + 1) * vCols - 1);
        // 只删离开窗口的卡(连同其 <video>,停止解码)；不动留在窗口里的，新进入的才建 → 视频不重载、不闪
        for (const [i, el] of vCards) {
            if (i < startI || i > endI) { el.remove(); vCards.delete(i); }
        }
        const frag = document.createDocumentFragment();
        for (let i = startI; i <= endI; i++) {
            if (vCards.has(i)) continue;
            const el = buildCardEl(i);
            vCards.set(i, el);
            frag.appendChild(el);
        }
        if (frag.childNodes.length) vp.appendChild(frag);
    }

    function scrollToCurrent() {
        if (!vItems.length || !vp) return;
        const idx = vItems.findIndex((it) => it.onSlots.includes(active));
        if (idx < 0) return;
        grid.scrollTop = Math.max(0, Math.floor(idx / vCols) * vRowH - (grid.clientHeight - vRowH) / 2);
        vLastRange = "";
        paintWindow();
    }

    // 卡片点击:事件委托(grid 上只挂一个监听;卡片在 vp 内,closest 仍命中)
    grid.addEventListener("click", (e) => {
        const card = e.target.closest(".ntp-card");
        if (!card || card.dataset.val == null) return;
        assign(active, card.dataset.val);
        if (!multi) close();   // 单槽:选完即关；多槽:留着继续填别的槽
    });
    // 右键卡片:菜单(打开管理器详情 / 复制触发词 / 复制文件名)
    grid.addEventListener("contextmenu", (e) => {
        const card = e.target.closest(".ntp-card");
        if (!card || card.dataset.val == null) return;
        e.preventDefault();
        e.stopPropagation();   // 不让 ComfyUI 画布右键菜单插进来
        const val = card.dataset.val;
        showCardMenu(e.clientX, e.clientY, val, (slotItems[active] || {})[val]);
    });
    // 悬停视频卡片:动态创建 <video> 播放原片，移出即移除（默认静态首帧，hover 才解码，省卡顿）
    grid.addEventListener("mouseover", (e) => {
        const card = e.target.closest(".ntp-card");
        if (!card) return;
        const img = card.querySelector("img.ntp-thumb[data-vsrc]");
        if (!img || card.querySelector("video.ntp-thumb")) return;
        const v = document.createElement("video");
        v.src = img.dataset.vsrc;
        v.className = "ntp-thumb";
        v.muted = true; v.loop = true; v.playsInline = true;
        v.setAttribute("disablepictureinpicture", "");
        v.setAttribute("disableremoteplayback", "");
        img.parentElement.appendChild(v);
        v.play().catch(() => {});
    });
    grid.addEventListener("mouseout", (e) => {
        const card = e.target.closest(".ntp-card");
        if (!card || card.contains(e.relatedTarget)) return;   // 卡片内部移动不算移出
        const v = card.querySelector("video.ntp-thumb");
        if (v) v.remove();
    });
    // 滚动:只重画可视窗口(rAF 节流);滚动时关掉悬空的右键菜单
    grid.addEventListener("scroll", () => {
        closeCardMenu();
        if (vRaf) return;
        vRaf = requestAnimationFrame(() => { vRaf = 0; paintWindow(); });
    });
    // 面板尺寸变化:重算列/行高 + 重画
    if (typeof ResizeObserver !== "undefined") {
        vRO = new ResizeObserver(() => {
            if (!vItems.length || !vp) return;
            measureLayout();
            vp.style.height = vRows() * vRowH + "px";
            vLastRange = "";
            paintWindow();
        });
        vRO.observe(grid);
    }

    // 搜索/筛选/切 SFW 后内容变了,回到顶部再渲染(虚拟滚动按 scrollTop 算窗口)
    const renderTop = () => { grid.scrollTop = 0; render(); };
    let t = null;
    searchEl.addEventListener("input", () => { clearTimeout(t); t = setTimeout(renderTop, 120); });
    selectEl.addEventListener("change", renderTop);
    sfwBtn.classList.toggle("active", !!nsfwCfg.sfwOnly);
    sfwBtn.addEventListener("click", () => {
        setSfwEverywhere(!nsfwCfg.sfwOnly);
        sfwBtn.classList.toggle("active", nsfwCfg.sfwOnly);
        renderTop();
    });
    _activeSfwApply = (v) => { sfwBtn.classList.toggle("active", v); renderTop(); };

    renderSlots();
    if (active >= 0) loadSlot(active);
    else { titleSlot.textContent = "添加 LoRA"; countEl.textContent = ""; render(); }
}

// ---- 图库选媒体器（选中 LoadImage / 加载视频 节点时）----
// 选一张图库图片/视频 → 后端复制进 ComfyUI input 目录 → 填回该节点的 widget。
// kind = "image"（只列图片，LoadImage）/ "video"（只列视频，VHS_LoadVideo 等）
async function openMediaPicker(node, widget, kind) {
    injectStyles();
    document.querySelectorAll(".ntp-overlay").forEach((o) => o.remove());
    await refreshNsfwCfg();
    const isVid = kind === "video";
    const noun = isVid ? "视频" : "图像";
    const unit = isVid ? "个" : "张";

    const overlay = document.createElement("div");
    overlay.className = "ntp-overlay";
    overlay.innerHTML = `
        <div class="ntp-panel">
            <div class="ntp-head">
                <span class="ntp-logo"><img class="ntp-logo-img" src="/noctyra_static/images/noctyra-logo.svg" alt=""></span>
                <span class="ntp-title"><b>Noctyra</b> · 选择${noun}</span>
                <span class="ntp-count">图库</span>
                <input class="ntp-search" type="text" placeholder="搜索文件名 / 提示词…">
                <button class="ntp-favp" type="button" title="只看收藏">♥</button>
                <button class="ntp-sfw" type="button" title="点击切换是否显示 NSFW（红色=已隐藏）">NSFW</button>
                <button class="ntp-close" title="关闭 (Esc)">×</button>
            </div>
            <div class="ntp-grid"></div>
            <div class="ntp-foot">点击${noun} → 复制进 ComfyUI <b>input/NoctyraInput</b> 并填入该节点 · <b>Esc</b> 关闭</div>
        </div>`;
    document.body.appendChild(overlay);

    const grid = overlay.querySelector(".ntp-grid");
    const searchEl = overlay.querySelector(".ntp-search");
    const favBtn = overlay.querySelector(".ntp-favp");
    const sfwBtn = overlay.querySelector(".ntp-sfw");
    const countEl = overlay.querySelector(".ntp-count");
    const footEl = overlay.querySelector(".ntp-foot");

    // 视频卡片默认只渲染静态首帧 <img>（后端 size=card 出封面），hover 才动态建 <video>，
    // 移出即移除 —— 无限滚动追加再多卡也不会有一堆 <video> 同时解码拖垮面板（不再需要 IntersectionObserver）。

    const close = () => {
        _activeSfwApply = null;
        overlay.remove();
        document.removeEventListener("keydown", onKey);
    };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
    document.addEventListener("keydown", onKey);
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
    overlay.querySelector(".ntp-close").addEventListener("click", close);
    searchEl.focus();

    let page = 1, totalPages = 1, loading = false, gen = 0;
    let sfwOnly = !!nsfwCfg.sfwOnly, favOnly = false, search = "";
    const seen = new Set();
    sfwBtn.classList.toggle("active", sfwOnly);

    const buildUrl = (p) => {
        const params = new URLSearchParams({ page: p, page_size: 60 });
        if (search) params.set("search", search);
        if (favOnly) params.set("favorite", "1");
        params.set("sfw", sfwOnly ? "1" : "0");
        params.set("media", isVid ? "video" : "image");   // 后端按类型过滤，计数才准
        return `${API_GALLERY}?${params.toString()}`;
    };

    const cardHtml = (img) => {
        const src = `/api/noctyra/workflow/image/${img.id}`;
        const isNsfwLevel = !!img.user_nsfw || (img.nsfw_level || 0) >= nsfwCfg.threshold;
        const doBlur = nsfwCfg.blur && isNsfwLevel;
        const wrapCls = "ntp-thumb-wrap" + (doBlur ? " ntp-nsfw" : "");
        const nsfwTag = isNsfwLevel ? `<span class="ntp-nsfw-tag">18+</span>` : "";
        const name = img.custom_name || img.file_name || "";
        const fav = img.favorite ? `<span class="ntp-fav">★</span>` : "";
        const wf = img.has_workflow ? `<span class="ntp-badge">WF</span>` : "";
        const media = isVid
            ? `<img class="ntp-thumb" loading="lazy" src="${src}?size=card" alt="" data-vsrc="${src}"><span class="ntp-vid-badge">&#9654;</span>`
            : `<img class="ntp-thumb" loading="lazy" src="${src}?size=card" alt="">`;
        return `<div class="ntp-card" data-id="${img.id}">
            <div class="${wrapCls}">${media}${nsfwTag}</div>
            <div class="ntp-info"><div class="ntp-name" title="${esc(name)}">${esc(name)}</div><div class="ntp-meta">${wf}${fav}</div></div>
        </div>`;
    };

    const pick = async (id, cardEl) => {
        if (cardEl.classList.contains("ntp-loading")) return;
        cardEl.classList.add("ntp-loading");
        try {
            const r = await fetch(API_TO_INPUT(id), {
                method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
            });
            const d = await r.json();
            if (!d.success) throw new Error(d.error || "复制失败");
            const fn = d.filename;
            const opts = widget.options || (widget.options = {});
            if (!Array.isArray(opts.values)) opts.values = [];
            if (!opts.values.includes(fn)) opts.values.push(fn);
            widget.value = fn;
            if (widget.callback) widget.callback(fn, app.canvas, node);
            app.graph?.setDirtyCanvas(true, true);
            close();
        } catch (e) {
            cardEl.classList.remove("ntp-loading");
            console.warn("[Noctyra] 送入 input 失败:", e);
            footEl.textContent = "送入 input 失败：" + (e.message || e);
            footEl.classList.add("err");
        }
    };

    grid.addEventListener("click", (e) => {
        const card = e.target.closest(".ntp-card");
        if (card) pick(parseInt(card.dataset.id), card);
    });

    // 悬停视频卡：在静态封面上动态建 <video> 播原片，移出即移除（同时只有 hover 的一个在解码）
    grid.addEventListener("mouseover", (e) => {
        if (!isVid) return;
        const card = e.target.closest(".ntp-card");
        if (!card) return;
        const img = card.querySelector("img.ntp-thumb[data-vsrc]");
        if (!img || card.querySelector("video.ntp-thumb")) return;
        const v = document.createElement("video");
        v.src = img.dataset.vsrc;
        v.className = "ntp-thumb";
        v.muted = true; v.loop = true; v.playsInline = true;
        v.setAttribute("disablepictureinpicture", "");
        v.setAttribute("disableremoteplayback", "");
        img.parentElement.appendChild(v);
        v.play().catch(() => {});
    });
    grid.addEventListener("mouseout", (e) => {
        const card = e.target.closest(".ntp-card");
        if (!card || card.contains(e.relatedTarget)) return;   // 卡片内部移动不算移出
        const v = card.querySelector("video.ntp-thumb");
        if (v) v.remove();
    });

    async function loadPage(first) {
        if (loading) return;
        loading = true;
        const myGen = gen;
        try {
            const r = await fetch(buildUrl(page));
            const d = await r.json();
            if (myGen !== gen || !overlay.isConnected) return;
            if (!d.success) { if (first) grid.innerHTML = `<div class="ntp-empty">加载失败</div>`; return; }
            totalPages = d.total_pages || 1;
            countEl.textContent = `${d.total} ${unit}`;
            // 后端已按 media 类型过滤，这里只做跨页去重
            const items = (d.images || []).filter((im) => !seen.has(im.id));
            items.forEach((im) => seen.add(im.id));
            if (first) grid.innerHTML = "";
            if (first && d.total === 0) {
                grid.innerHTML = `<div class="ntp-empty">图库里没有可用${noun}<br><span style="font-size:12px">先去工作流图库保存些${noun}</span></div>`;
                return;
            }
            grid.insertAdjacentHTML("beforeend", items.map(cardHtml).join(""));
        } catch (e) {
            if (myGen === gen && first) grid.innerHTML = `<div class="ntp-empty">网络错误</div>`;
        } finally {
            loading = false;
        }
    }

    // 滚到底续拉下一页
    grid.addEventListener("scroll", () => {
        if (loading || page >= totalPages) return;
        if (grid.scrollTop + grid.clientHeight >= grid.scrollHeight - 320) { page++; loadPage(false); }
    });

    const reset = () => {
        gen++; page = 1; totalPages = 1; seen.clear();
        footEl.classList.remove("err");
        grid.innerHTML = `<div class="ntp-empty"><div class="ntp-spinner"></div>正在加载图库…</div>`;
        loadPage(true);
    };

    let t = null;
    searchEl.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { search = searchEl.value.trim(); reset(); }, 200); });
    favBtn.addEventListener("click", () => { favOnly = !favOnly; favBtn.classList.toggle("active", favOnly); reset(); });
    sfwBtn.addEventListener("click", () => {
        sfwOnly = !sfwOnly;
        sfwBtn.classList.toggle("active", sfwOnly);
        reset();
        setSfwEverywhere(sfwOnly);   // 写两键 + 广播到模型页/工作流页
    });
    // 别页切换 SFW 时，更新本面板
    _activeSfwApply = (v) => { sfwOnly = v; sfwBtn.classList.toggle("active", v); reset(); };

    reset();
}

// 选中节点里的"加载图像/视频"widget → {node, widget, mediaKind}
function getMediaTarget() {
    const sel = app.canvas?.selected_nodes;
    if (!sel) return null;
    for (const node of Object.values(sel)) {
        const combos = (node.widgets || []).filter((w) => w && w.type === "combo");
        const iw = combos.find((w) => IMAGE_WIDGET_NAMES.has(w.name));
        if (iw) return { node, widget: iw, mediaKind: "image" };
        // 视频：显式 video/video_file，或名为 file 且节点类名/标题含 video
        const nodeStr = ((node.type || "") + " " + (node.comfyClass || "") + " " + (node.title || "")).toLowerCase();
        const vw = combos.find((w) => VIDEO_WIDGET_NAMES.has(w.name) || (w.name === "file" && nodeStr.includes("video")));
        if (vw) return { node, widget: vw, mediaKind: "video" };
    }
    return null;
}

// ---- 当前选中节点里的模型 widget ----
function getActiveTarget() {
    const sel = app.canvas?.selected_nodes;
    if (!sel) return null;
    for (const node of Object.values(sel)) {
        const widgets = (node.widgets || []).filter((w) => w && w.type === "combo" && WIDGET_LABELS[w.name]);
        if (widgets.length) return { node, widgets };
    }
    return null;
}

function getPowerLoraNode() {
    const sel = app.canvas?.selected_nodes;
    if (!sel) return null;
    for (const node of Object.values(sel)) {
        if (isPowerLoraNode(node)) return node;
    }
    return null;
}

// ---- 库内匹配状态 + 触发词面板 ----
let _matchedModels = [];   // 当前选中节点里"在库"的模型（含 trained_words），给箭头 + 触发词面板用

// /picker/match 会话级缓存：同一组值短时间内反复查（来回点节点/重开面板）直接命中，少打后端。
// 60s TTL 兜住"期间在管理器里扫描/下载改了库"的偶发陈旧；选择器自身不改库成员，无需主动失效。
const _matchCache = new Map();   // key -> { ts, items }
const _MATCH_TTL = 60000;
function _matchKey(names) {
    return [...new Set((names || []).filter(Boolean))].sort().join("\n");
}
async function fetchMatchItems(names) {
    const key = _matchKey(names);
    const hit = _matchCache.get(key);
    if (hit && (Date.now() - hit.ts) < _MATCH_TTL) return hit.items;
    const r = await fetch(API, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ names }),
    });
    const d = await r.json();
    const items = (d && d.success && d.items) || {};
    _matchCache.set(key, { ts: Date.now(), items });
    if (_matchCache.size > 40) _matchCache.delete(_matchCache.keys().next().value);
    return items;
}

// 用当前模型值复用 /picker/match 查库；命中则置 has-match。keyAtReq/getKey 防过期结果覆盖。
// 传 widgets(非纯值)以便给每个命中模型附上"槽位类型"标签(_slots),多模型节点的触发词面板
// 才能标清哪个是 Checkpoint / CLIP / LoRA，避免挤在一起分不清(乱显示)。
async function checkMatched(widgets, fab, keyAtReq, getKey) {
    const vals = widgets.map((w) => w.value).filter(Boolean);
    let out = [];
    if (vals.length) {
        try {
            const items = await fetchMatchItems(vals);
            const seen = new Map();   // file_path/name -> 已入 out 的条目(便于多槽指同一文件时合并标签)
            for (const w of widgets) {
                const it = items[w.value];
                if (!it) continue;
                const k = it.file_path || it.name;   // 多个 widget 可能指同一文件
                const label = WIDGET_LABELS[w.name] || "";
                const ex = seen.get(k);
                if (ex) {
                    if (label && !ex._slots.includes(label)) ex._slots.push(label);
                    continue;
                }
                const entry = { ...it, _slots: label ? [label] : [] };
                seen.set(k, entry);
                out.push(entry);
            }
        } catch (_) { /* 网络失败当作未命中 */ }
    }
    if (keyAtReq !== getKey()) return;   // 选区/值已变 → 丢弃这次过期结果
    _matchedModels = out;
    fab.classList.toggle("has-match", out.length > 0);
}

function previewSrc(raw) {
    if (!raw) return "";
    if (raw.startsWith("sidecar://")) return `/api/noctyra/local-preview?id=${encodeURIComponent(raw.slice(10))}&size=card`;
    return `/api/noctyra/preview?url=${encodeURIComponent(raw)}&size=card`;
}

function closeTriggerPop() {
    const p = document.getElementById("noctyra-trig-pop");
    if (p) p.remove();
    document.removeEventListener("mousedown", onDocClickForTrig, true);
}
function onDocClickForTrig(e) {
    const p = document.getElementById("noctyra-trig-pop");
    const fab = document.getElementById("noctyra-fab");
    if (p && !p.contains(e.target) && fab && !fab.contains(e.target)) closeTriggerPop();
}

function toggleTriggerPop(fab) {
    if (document.getElementById("noctyra-trig-pop")) { closeTriggerPop(); return; }
    if (!_matchedModels.length) return;

    const pop = document.createElement("div");
    pop.className = "ntp-trig-pop";
    pop.id = "noctyra-trig-pop";
    pop.innerHTML = _matchedModels.map((m) => {
        const allWords = (m.trained_words || []).filter(Boolean);
        const LIMIT = 20;
        const truncated = allWords.length > LIMIT;
        const words = truncated ? allWords.slice(0, LIMIT - 1) : allWords;   // 满则留末位给 …
        const src = previewSrc(m.preview_url);
        const thumb = src
            ? `<img class="ntp-trig-thumb" src="${src}" alt="" onerror="this.style.visibility='hidden'">`
            : `<div class="ntp-trig-thumb ntp-trig-ph">${PH_ICON}</div>`;
        // 词条异常多 → 多半是 CivitAI 把数据集打标 tag 塞进了 trainedWords，提示别全当触发词
        const warn = allWords.length > 50
            ? `<div class="ntp-trig-warn">⚠ 共 ${allWords.length} 个词条，可能含数据集标签而非纯触发词</div>` : "";
        const chips = allWords.length
            ? words.map((w) => `<span class="ntp-trig-chip" data-w="${esc(w)}">${esc(w)}</span>`).join("")
              + (truncated ? `<span class="ntp-trig-more" title="还有 ${allWords.length - words.length} 个，复制全部可拿完整列表">…</span>` : "")
            : `<span class="ntp-trig-empty">该模型暂无触发词</span>`;
        const copyAll = allWords.length
            ? `<button class="ntp-trig-copyall" data-all="${esc(allWords.join(", "))}">复制全部${truncated ? `（${allWords.length}）` : ""}</button>` : "";
        // 多模型节点:标注该模型来自哪个槽(Checkpoint / CLIP / LoRA…),避免多个挤在一起分不清
        const slotTag = (m._slots && m._slots.length)
            ? `<span class="ntp-trig-slot">${esc(m._slots.join(" / "))}</span>` : "";
        return `<div class="ntp-trig-model">
            ${thumb}
            <div class="ntp-trig-body">
                <div class="ntp-trig-name" title="${esc(m.name || "")}">${esc(m.name || "")}</div>
                ${slotTag}
                ${m.base_model ? `<span class="ntp-trig-base">${esc(m.base_model)}</span>` : ""}
                ${warn}
                <div class="ntp-trig-chips">${chips}</div>
                ${copyAll}
            </div>
        </div>`;
    }).join("");
    document.body.appendChild(pop);

    // 定位：默认 FAB 上方、与 FAB 左对齐；再把整个面板夹进视口内（左/右/上/下都不出界，
    // 修复 FAB 在左边时面板按"右对齐"往左溢出屏幕的问题）。用 left/top 而非 right/bottom。
    const fr = fab.getBoundingClientRect();
    const pw = pop.offsetWidth || 300;
    const ph = pop.offsetHeight || 200;
    const m = 8;
    const left = Math.min(Math.max(m, fr.left), Math.max(m, window.innerWidth - pw - m));
    let top = fr.top - ph - 10;                      // 优先放上方
    if (top < m) top = fr.bottom + 10;               // 上方放不下 → 改到下方
    top = Math.min(Math.max(m, top), Math.max(m, window.innerHeight - ph - m));
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
    pop.style.right = "auto";
    pop.style.bottom = "auto";

    pop.addEventListener("click", async (e) => {
        const chip = e.target.closest(".ntp-trig-chip");
        const all = e.target.closest(".ntp-trig-copyall");
        const text = chip ? chip.dataset.w : (all ? all.dataset.all : null);
        if (text == null) return;
        try {
            await navigator.clipboard.writeText(text);
            const el = chip || all;
            el.classList.add("copied");
            setTimeout(() => el.classList.remove("copied"), 650);
        } catch (_) { /* 剪贴板不可用，忽略 */ }
    });
    setTimeout(() => document.addEventListener("mousedown", onDocClickForTrig, true), 0);
}

// FAB 自由拖动：照搬浏览器扩展那套 —— mousedown 才把 move/up 绑到 document、抬起就解绑，
// 平时 document 零 listener，也不会被 ComfyUI 画布抢事件（这是之前"拖不动"的根因）。
// 自由定位，限制在视口内（≈画布范围），位置存 localStorage。
function makeDraggable(fab) {
    const KEY = "noctyra_fab_pos";
    const margin = 6;
    const applyClamp = (x, y) => {
        const w = fab.offsetWidth || 50, h = fab.offsetHeight || 50;
        const cx = Math.min(Math.max(margin, x), window.innerWidth - w - margin);
        const cy = Math.min(Math.max(margin, y), window.innerHeight - h - margin);
        fab.style.left = cx + "px"; fab.style.top = cy + "px";
        fab.style.right = "auto"; fab.style.bottom = "auto";
    };
    try {
        const s = JSON.parse(localStorage.getItem(KEY) || "null");
        if (s && typeof s.x === "number") requestAnimationFrame(() => applyClamp(s.x, s.y));
    } catch (_) {}

    let sx = 0, sy = 0, baseL = 0, baseT = 0, dragMoved = false;
    const onMove = (e) => {
        const dx = e.clientX - sx, dy = e.clientY - sy;
        if (!dragMoved && Math.hypot(dx, dy) > 5) {   // 阈值，区分点击与拖动
            dragMoved = true; fab.classList.add("dragging"); closeTriggerPop();
        }
        if (dragMoved) applyClamp(baseL + dx, baseT + dy);
    };
    const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        if (!dragMoved) return;   // 没移动 = 普通点击，dragMoved 留给 click 捕获处理器消费
        fab.classList.remove("dragging");
        const r = fab.getBoundingClientRect();
        try { localStorage.setItem(KEY, JSON.stringify({ x: r.left, y: r.top })); } catch (_) {}
    };
    fab.addEventListener("mousedown", (e) => {
        if (e.button !== 0) return;   // 仅左键
        dragMoved = false;
        sx = e.clientX; sy = e.clientY;
        const r = fab.getBoundingClientRect(); baseL = r.left; baseT = r.top;
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
        e.preventDefault();
    });
    // 捕获阶段拦截拖动后的 click（makeDraggable 先注册 → 此处先于业务 click 触发）
    fab.addEventListener("click", (e) => {
        if (dragMoved) { e.stopImmediatePropagation(); e.preventDefault(); dragMoved = false; }
    }, true);
    window.addEventListener("resize", () => {
        if (fab.style.left) { const r = fab.getBoundingClientRect(); applyClamp(r.left, r.top); }
    });
}

// ---- 创建悬浮按钮 + 选中轮询 ----
let _fabPollTimer = null;   // 选中轮询的 interval id,保存以便重建 FAB 时清掉旧的,避免叠加
function setupFab() {
    if (document.getElementById("noctyra-fab")) return;   // 已有 FAB,沿用其轮询
    if (_fabPollTimer) { clearInterval(_fabPollTimer); _fabPollTimer = null; }   // FAB 不在却有残留轮询 → 清掉
    injectStyles();
    const fab = document.createElement("div");
    fab.id = "noctyra-fab";
    fab.className = "ntp-fab";
    fab.innerHTML = `<img class="ntp-fab-icon" src="/noctyra_static/images/noctyra-logo.svg" alt=""><span class="ntp-fab-arrows">${ARROWS_SVG}</span><span class="ntp-fab-tag"></span>`;
    fab.title = "选中模型 / 加载图像节点后点此";
    document.body.appendChild(fab);
    const tag = fab.querySelector(".ntp-fab-tag");

    makeDraggable(fab);

    // 统一目标：模型节点优先，其次"加载图像/视频"节点
    const getTarget = () => {
        const m = getActiveTarget();
        if (m) return { kind: "model", node: m.node, widgets: m.widgets };
        const pl = getPowerLoraNode();   // Power Lora Loader:无标准 combo,单独识别
        if (pl) return { kind: "powerlora", node: pl };
        const md = getMediaTarget();
        if (md) return { kind: "media", node: md.node, widget: md.widget, mediaKind: md.mediaKind };
        return null;
    };

    const openFlow = () => {
        const target = getTarget();
        if (!target) return;
        if (target.kind === "media") {
            openMediaPicker(target.node, target.widget, target.mediaKind);
            return;
        }
        if (target.kind === "powerlora") {
            openPowerLoraPicker(target.node);   // 槽位侧边栏带「＋ 添加 LoRA」
            return;
        }
        // 初始定位到"第一个有值的槽"——用户多半刚在原生下拉改了某个槽,打开就该停在那、
        // 直接看到选中卡被标记。之前写死 0,多槽节点(如 DualCLIP 改第 2 个 CLIP)会停在空的第 0 槽,
        // 看不到刚选的模型被标记。都没值则回退第 0 槽。
        const initIdx = target.widgets.findIndex((w) => {
            const v = w.value;
            return v != null && v !== "" && String(v).toLowerCase() !== "none";
        });
        openPicker(target.node, target.widgets, initIdx >= 0 ? initIdx : 0);   // 单槽/多槽统一；多槽时面板内出侧边栏
    };
    // 单击立即打开选择器（不再为"双击看触发词"预留 240ms 延迟，库内模型点开也不再卡顿）。
    fab.addEventListener("click", () => {
        if (!getTarget()) return;
        openFlow();
    });
    // 看触发词改用右键（contextmenu）：与单击各走各的，天然不抢时序，无需乐观打开/撤销那套。
    fab.addEventListener("contextmenu", (e) => {
        if (!fab.classList.contains("has-match")) return;   // 非库内模型：放行浏览器默认右键
        e.preventDefault();
        toggleTriggerPop(fab);
    });

    // 轮询选中状态：key 含 widget 值，模型被改也能重查库内状态
    let lastKey = "";
    let checkTimer = null;
    _fabPollTimer = setInterval(() => {
        if (document.hidden) return;  // 后台标签页不空转
        const target = getTarget();
        let key = "";
        if (target?.kind === "model") {
            key = "m:" + target.node.id + "|" + target.widgets.map((w) => w.name + "=" + (w.value || "")).join(",");
        } else if (target?.kind === "media") {
            key = "x:" + target.node.id + "|" + target.mediaKind + "=" + (target.widget.value || "");
        } else if (target?.kind === "powerlora") {
            key = "pl:" + target.node.id + "|" + getPowerLoraWidgets(target.node).map((w) => w.value?.lora || "").join(",");
        }
        if (key === lastKey) return;
        lastKey = key;
        closeTriggerPop();
        if (target?.kind === "media") {
            const isVid = target.mediaKind === "video";
            fab.classList.add("active", "img-mode");
            fab.classList.remove("has-match");
            _matchedModels = [];
            fab.title = `选${isVid ? "视频" : "图"}：从 Noctyra 图库挑一${isVid ? "个" : "张"}载入此节点`;
            tag.textContent = isVid ? "视频" : "图像";
        } else if (target?.kind === "powerlora") {
            fab.classList.add("active");
            fab.classList.remove("img-mode", "has-match");
            _matchedModels = [];
            fab.title = "Power Lora Loader：浏览 / 添加 LoRA 槽（带预览）";
            tag.textContent = "LoRA";
        } else if (target) {
            fab.classList.add("active");
            fab.classList.remove("img-mode");
            const labels = [...new Set(target.widgets.map((w) => WIDGET_LABELS[w.name]))];
            fab.title = `浏览${labels.join(" / ")}（库内模型右键看触发词）`;
            tag.textContent = labels.length === 1 ? labels[0] : labels.length;
            clearTimeout(checkTimer);
            checkTimer = setTimeout(() => checkMatched(target.widgets, fab, key, () => lastKey), 180);
        } else {
            fab.classList.remove("active", "has-match", "img-mode");
            _matchedModels = [];
            fab.title = "选中模型 / 加载图像节点后点此";
            closeTriggerPop();
        }
    }, 300);
}

app.registerExtension({
    name: "Noctyra.ModelPicker",
    setup() {
        console.log("%c[Noctyra] 画布选择器 v2.34 已加载（模型 + 图库选图/选视频 / 可拖动 / 右键看触发词 / 右键卡片看详情·复制触发词）", "color:#2d7ff9;font-weight:700");
        // 先取设置：决定是否创建悬浮按钮 + 预热 NSFW 配置（设置里关掉则不显示按钮）
        refreshNsfwCfg().then(() => { if (pickerEnabled) setupFab(); });
    },
});
