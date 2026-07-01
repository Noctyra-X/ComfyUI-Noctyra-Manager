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
 * Content script for civitai.com
 *
 * Scans the current page for CivitAI model version references and:
 *   1. Queries Noctyra to see which versions are already downloaded
 *   2. Injects a "Send to Noctyra" button + "已下载" badge on the model detail page
 */


// `send()` 由 noctyra-send.js 提供（manifest content_scripts 先加载）

// ---- Extract current model/version from URL + DOM ----

function parseModelUrl() {
    // Matches /models/<id> and /models/<id>?modelVersionId=<vid>
    const m = location.pathname.match(/^\/models\/(\d+)/);
    if (!m) return null;
    const modelId = parseInt(m[1], 10);
    const url = new URL(location.href);
    let versionId = parseInt(url.searchParams.get('modelVersionId') || '0', 10) || null;
    // Fallback：只信任真实链接 href 里的 modelVersionId，而非整页任意文本
    // （评论/广告/注入文本可塞假 "modelVersionId: 999" 污染选版）
    if (!versionId) {
        const link = document.querySelector('a[href*="modelVersionId="]');
        if (link) {
            try {
                const lv = parseInt(new URL(link.href, location.origin).searchParams.get('modelVersionId') || '0', 10);
                if (lv > 0) versionId = lv;
            } catch (_) { /* 非法 href 忽略 */ }
        }
    }
    return { modelId, versionId };
}

// ---- UI injection on model detail page ----

// 追踪当前的注入会话：SPA 快速切页时取消上一页的重试循环和请求回调
let injectSession = 0;
let injectRetryTimer = null;

async function injectPanelIfModelPage() {
    const info = parseModelUrl();
    if (!info) {
        // 离开 model 页 → 移除浮动按钮 + 停掉上一页的版本描边重试 interval（否则空转 12 秒）
        removeFloatingButton();
        if (injectRetryTimer) { clearInterval(injectRetryTimer); injectRetryTimer = null; }
        injectSession++;   // 让上一页遗留的回调因 session 不符而早退
        return;
    }

    const mySession = ++injectSession;
    if (injectRetryTimer) { clearInterval(injectRetryTimer); injectRetryTimer = null; }

    const status = { versionId: info.versionId, modelId: info.modelId, downloaded: null, recordOnly: null, otherVersions: [] };

    const payload = {
        version_ids: info.versionId ? [info.versionId] : [],
        model_ids: info.modelId ? [info.modelId] : [],
    };
    const res = await send('checkVersions', payload);
    if (mySession !== injectSession) return;
    reportConnection(res);

    if (res.success) {
        if (info.versionId && res.downloaded?.[String(info.versionId)]) {
            status.downloaded = res.downloaded[String(info.versionId)];
        }
        // 软删（文件已删但留了记录）→ 显示"已有记录"，仍可点下载
        if (info.versionId && res.record_only?.[String(info.versionId)]) {
            status.recordOnly = res.record_only[String(info.versionId)];
        }
        if (info.modelId && res.by_model?.[String(info.modelId)]) {
            status.otherVersions = res.by_model[String(info.modelId)];
        }
    }

    // 浮动按钮不依赖 CivitAI DOM 加载，立即渲染
    await renderFloatingButton(status);

    // 版本选择按钮的绿色描边（独立功能，仍需要等 DOM 加载）
    markDownloadedVersionButtons(status.otherVersions || []);
    let tries = 0;
    injectRetryTimer = setInterval(() => {
        if (mySession !== injectSession) { clearInterval(injectRetryTimer); injectRetryTimer = null; return; }
        tries++;
        markDownloadedVersionButtons(status.otherVersions || []);
        if (tries >= 24) { clearInterval(injectRetryTimer); injectRetryTimer = null; }
    }, 500);
}

function markDownloadedVersionButtons(versions) {
    if (!versions || versions.length === 0) return;
    const dlNames = versions
        .map(v => (v.version_name || '').trim())
        .filter(Boolean);
    if (dlNames.length === 0) return;

    // 模型详情页的版本选择区内的 compact-sm 按钮
    // Why: 全局 compact-sm 会命中分页/排序按钮（比如 "1" "2"），
    // 限定在版本容器里避免误伤。
    const scopes = document.querySelectorAll(
        'main [class*="Version"], main [class*="version"]'
    );
    const roots = scopes.length ? Array.from(scopes) : [document];
    for (const root of roots) {
        const btns = root.querySelectorAll('button[data-size="compact-sm"]');
        btns.forEach(b => {
            const t = (b.textContent || '').trim();
            if (!t) return;
            if (dlNames.includes(t)) {
                b.classList.add('noctyra-version-downloaded');
                b.title = `Noctyra: 此版本已下载`;
            }
        });
    }
}

// ---- Floating action button (draggable, edge-snap) ----
// 浮动按钮替代了原来的 anchor-based 注入。优点：完全不依赖 CivitAI DOM 结构，
// 在任何 model 页（包括禁用 generation 的 edit lora 等没有 Create 按钮的页面）
// 都能稳定显示；缺点：脱离原生操作栏，但通过用户可拖拽 + 边缘吸附弥补。

// 模型下载浮按钮与图片保存浮按钮共用同一个位置存储（两者永不同时出现，统一落点）
const FLOAT_STORAGE_KEY = 'noctyra_float_pos';
const DRAG_THRESHOLD_PX = 5;  // mouse 累计位移阈值，区分点击和拖拽

let floatBtn = null;

/**
 * 给一个浮动按钮绑定拖拽 + 边缘吸附 + 位置持久化。
 * storageKey 不同 → 两个按钮位置互相独立保存（model 和 image 页用不同的 key）。
 */
async function makeFloatDraggable(btn, storageKey, defaultPos) {
    let pos;
    try {
        const stored = (await chrome.storage.sync.get([storageKey]))[storageKey];
        if (stored && (stored.side === 'left' || stored.side === 'right') && typeof stored.yPct === 'number') {
            pos = stored;
        }
    } catch (e) { /* storage 失败回退默认值 */ }
    pos = pos || defaultPos;

    const margin = 12;

    function apply() {
        const btnH = btn.offsetHeight || 40;
        const top = Math.max(margin, Math.min(window.innerHeight - btnH - margin, pos.yPct * window.innerHeight));
        btn.style.top = `${top}px`;
        if (pos.side === 'left') {
            btn.style.left = `${margin}px`;
            btn.style.right = 'auto';
        } else {
            btn.style.right = `${margin}px`;
            btn.style.left = 'auto';
        }
    }

    function save() {
        try { chrome.storage.sync.set({ [storageKey]: pos }).catch(() => {}); } catch (e) {}
    }

    let startX = 0, startY = 0;
    let btnStartLeft = 0, btnStartTop = 0;
    let dragMoved = false;

    // mousemove/mouseup 绑在 document（拖拽时鼠标会移出按钮）；
    // 关键：按下才绑、松手就解绑 → 平时 document 零 listener，SPA 反复切页不会累积僵尸 listener
    function onMove(e) {
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        if (!dragMoved && Math.hypot(dx, dy) > DRAG_THRESHOLD_PX) {
            dragMoved = true;
            btn.classList.add('dragging');
        }
        if (dragMoved) {
            btn.style.left = `${btnStartLeft + dx}px`;
            btn.style.top = `${btnStartTop + dy}px`;
            btn.style.right = 'auto';
        }
    }

    function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        if (!dragMoved) return;  // 没移动 = 普通点击，dragMoved 留给 click handler 消费
        btn.classList.remove('dragging');
        // 吸附到最近的左/右边
        const rect = btn.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;
        const side = centerX < window.innerWidth / 2 ? 'left' : 'right';
        const yPct = Math.max(0.05, Math.min(0.95, rect.top / window.innerHeight));
        pos = { side, yPct };
        apply();
        save();
    }

    btn.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;  // 仅响应左键
        dragMoved = false;
        startX = e.clientX;
        startY = e.clientY;
        const rect = btn.getBoundingClientRect();
        btnStartLeft = rect.left;
        btnStartTop = rect.top;
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        e.preventDefault();
    });

    // 拖拽后阻止 click 触发（capture 阶段拦截）
    btn.addEventListener('click', (e) => {
        if (dragMoved) {
            e.stopPropagation();
            e.preventDefault();
            dragMoved = false;
        }
    }, true);

    window.addEventListener('resize', apply);

    // 移除按钮时调用：解绑常驻的 resize + 兜底解绑拖拽中途的 move/up
    // （mousedown/click 绑在 btn 自身，随 DOM 一起 GC，无需手动解绑）
    btn._cleanupDrag = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        window.removeEventListener('resize', apply);
    };

    requestAnimationFrame(apply);
}

function updateFloatingButtonState(btn, status) {
    btn._status = status;
    btn.classList.remove('submitted');  // SPA 切模型时清掉旧"已加入下载"状态
    btn.disabled = false;

    const downloaded = !!status.downloaded;
    const recordOnly = !downloaded && !!status.recordOnly;  // 有记录但文件已删，仍可下载
    const noVersion = !status.versionId;

    btn.classList.toggle('downloaded', downloaded);
    btn.classList.toggle('record-only', recordOnly);
    btn.classList.toggle('no-version', noVersion && !downloaded && !recordOnly);
    // 不用 disabled：disabled 的 <button> 收不到 mousedown，会导致已下载状态下拖不动。
    // 点击动作由 click 处理器按 status 把关（已下载/无版本不触发下载）。
    btn.disabled = false;

    const iconSvg = downloaded
        ? `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 8.5 L6.3 11.5 L13 5"/></svg>`
        : `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2.5 V10.5"/><path d="M4.5 7.5 L8 11 L11.5 7.5"/><path d="M3 13.5 H13"/></svg>`;

    let label, title;
    if (downloaded) {
        label = '已在本地';
        title = `已下载: ${status.downloaded.file_name || ''}`;
    } else if (recordOnly) {
        label = '已有记录';
        title = `本地有记录但文件已删，点击重新下载: ${status.recordOnly.file_name || ''}`;
    } else if (noVersion) {
        label = '请选版本';
        title = '请先在 CivitAI 选择具体版本';
    } else {
        label = '下载';
        title = '下载到 Noctyra 本地模型库（按住拖动可移动）';
    }
    btn.innerHTML = `${iconSvg}<span>${label}</span>`;
    btn.title = title;
}

async function renderFloatingButton(status) {
    if (floatBtn) {
        // 同一按钮 instance 更新状态（避免 SPA 切模型时重复创建）
        updateFloatingButtonState(floatBtn, status);
        return;
    }

    const btn = document.createElement('button');
    btn.className = 'noctyra-inline-btn noctyra-float';
    btn.type = 'button';
    document.body.appendChild(btn);
    floatBtn = btn;

    btn.addEventListener('click', async () => {
        const s = btn._status;
        if (!s || btn.disabled) return;
        if (s.downloaded) return;
        if (!s.versionId) {
            showToast('Noctyra: 请先在 CivitAI 选择具体版本');
            return;
        }
        btn.disabled = true;
        const span = btn.querySelector('span');
        if (span) span.textContent = '提交中...';
        const res = await send('extensionDownload', { model_id: s.modelId, version_id: s.versionId });
        if (res?.success) {
            btn.classList.add('submitted');
            if (span) span.textContent = '已加入下载';
            btn.title = `保存目录: ${res.save_dir}`;
        } else {
            btn.disabled = false;
            if (span) span.textContent = '下载';
            showToast(`Noctyra: ${res?.error || '失败'}`);
        }
    });

    updateFloatingButtonState(btn, status);
    // helper 内部负责加载位置、apply、监听 resize 自适应
    await makeFloatDraggable(btn, FLOAT_STORAGE_KEY, { side: 'right', yPct: 0.85 });
}

function removeFloatingButton() {
    if (floatBtn) {
        if (floatBtn._cleanupDrag) floatBtn._cleanupDrag();
        floatBtn.remove();
        floatBtn = null;
    }
}

// ---- Batch badging on list / related cards ----

const CARD_MARKED_ATTR = 'data-noctyra-card';
const KNOWN_MODELS_MAX = 2000;
const KNOWN_MODELS_TTL_MS = 5 * 60 * 1000; // 5 分钟过期，避免用户在别处下载后本页状态 stale
const knownModels = new Map(); // modelId -> { rows, total, t }
function rememberModel(id, rows, total) {
    if (knownModels.size >= KNOWN_MODELS_MAX) {
        // 简单 LRU：删掉最早插入的 1/4，保留最近访问
        const trim = Math.floor(KNOWN_MODELS_MAX / 4);
        const it = knownModels.keys();
        for (let i = 0; i < trim; i++) knownModels.delete(it.next().value);
    }
    knownModels.set(id, { rows, total: total || 0, t: Date.now() });
}
function getKnownModel(id) {
    const entry = knownModels.get(id);
    if (!entry) return null;
    if (Date.now() - entry.t > KNOWN_MODELS_TTL_MS) {
        knownModels.delete(id);
        return null;
    }
    return entry.rows;
}
// 该模型在 CivitAI 上的总版本数（0=未知）；判"本地是否下全"
function getKnownTotal(id) {
    const entry = knownModels.get(id);
    return entry ? (entry.total || 0) : 0;
}
function hasKnownModel(id) {
    return getKnownModel(id) !== null;
}

// 当前详情页的 modelId —— 用于跳过页面内部的导航链接（Reviews / Gallery 等
// 共享同一 modelVersionId 查询参数的 tab 链接），避免给它们打版本描边
function currentPageModelId() {
    const m = (location.pathname || '').match(/\/models\/(\d+)/);
    return m ? parseInt(m[1], 10) : 0;
}

// 详情页子路由（tab 链接）黑名单，永远不当作卡片处理
const SUBPAGE_RE = /\/models\/\d+\/(reviews|gallery|images|discussion|edit)(?:$|[/?#])/i;

function isImagePage() {
    return /^\/images\/\d+/.test(location.pathname);
}

// 从 /images/<id> 提取 civitai image id
function parseImageIdFromUrl() {
    const m = location.pathname.match(/^\/images\/(\d+)/);
    return m ? parseInt(m[1], 10) : null;
}

// ---- Image page: "保存到 Noctyra" 按钮 ----
//
// CivitAI 图片详情页 DOM 变化频繁，用视口右下角浮动按钮最稳。
// 点击后后端串联 fetch + save（/api/noctyra/extension/save-image），
// 图片原图 + 生成参数 + workflow 一并入图库。

const IMG_SAVE_BTN_ID = 'noctyra-image-save-btn';

function removeImageSaveButton() {
    const el = document.getElementById(IMG_SAVE_BTN_ID);
    if (el) {
        if (el._cleanupDrag) el._cleanupDrag();
        el.remove();
    }
}

function resetImageSaveBtn(btn) {
    btn.disabled = false;
    btn.classList.remove('submitted', 'already-exists');
    const label = btn.querySelector('.noctyra-image-save-label');
    if (label) label.textContent = '保存图片';
}

function injectImageSaveButton() {
    if (!isImagePage()) {
        removeImageSaveButton();
        return;
    }
    const currentImageId = parseImageIdFromUrl();
    let btn = document.getElementById(IMG_SAVE_BTN_ID);
    if (btn) {
        // SPA 切图（CivitAI image viewer 切下一张）→ 重置 stale 状态，
        // 否则会显示前一张图的"已保存 ✓"误导用户以为本图也已保存
        if (btn._lastImageId !== currentImageId) {
            resetImageSaveBtn(btn);
            btn._lastImageId = currentImageId;
        }
        return;
    }

    btn = document.createElement('button');
    btn.id = IMG_SAVE_BTN_ID;
    btn.type = 'button';
    btn.className = 'noctyra-image-save-btn';
    btn.innerHTML = `
        <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"
                  transform="scale(0.67) translate(0, 0)"/>
            <path d="M8 2.5 V10.5"/><path d="M4.5 7.5 L8 11 L11.5 7.5"/><path d="M3 13.5 H13"/>
        </svg>
        <span class="noctyra-image-save-label">保存图片</span>
    `;
    btn.title = '把本图连同 prompt / 参数 / workflow 一并保存到 Noctyra 工作流图库（按住拖动可移动）';
    btn._lastImageId = currentImageId;
    btn.addEventListener('click', handleImageSaveClick);
    document.body.appendChild(btn);
    // 与模型下载浮按钮"共用同一个位置"：两者永不同时出现（模型页 vs 图片页），
    // 共用存储 key + 默认位置后，用户拖一次，在两类页面都落在同一处，体验上就是同一个悬浮按钮。
    makeFloatDraggable(btn, FLOAT_STORAGE_KEY, { side: 'right', yPct: 0.85 }).catch(() => {});
}

async function handleImageSaveClick() {
    const btn = document.getElementById(IMG_SAVE_BTN_ID);
    if (!btn || btn.disabled) return;
    const label = btn.querySelector('.noctyra-image-save-label');

    const imageId = parseImageIdFromUrl();
    if (!imageId) {
        showToast('Noctyra: 无法识别图片 ID');
        return;
    }

    btn.disabled = true;
    label.textContent = '保存中...';

    let res;
    try {
        // 超时给 65s：后端图片下载+落盘+入库最长 60s，默认 10s 会先超时误报"保存失败"
        res = await send('extensionSaveImage', { image_id: imageId, url: location.href }, 65000);
    } catch (e) {
        label.textContent = '保存图片';
        btn.disabled = false;
        showToast(`Noctyra: ${String(e.message || e)}`);
        return;
    }

    if (!res?.success) {
        label.textContent = '保存图片';
        btn.disabled = false;
        showToast(`Noctyra: ${res?.error || '保存失败'}`);
        return;
    }

    if (res.already_exists) {
        label.textContent = '已在图库';
        btn.classList.add('already-exists');
        showToast('Noctyra: 本图已在图库中');
    } else {
        label.textContent = '已保存';
        btn.classList.add('submitted');
        showToast('Noctyra: 已保存到工作流图库');
    }
    setTimeout(() => {
        label.textContent = '保存图片';
        btn.classList.remove('submitted', 'already-exists');
        btn.disabled = false;
    }, 3000);
}

function findCardLinks(root = document) {
    // 图片详情页不做卡片徽章扫描（资源标记由 markImagePageResources 处理）
    if (isImagePage()) return new Map();

    const anchors = root.querySelectorAll('a[href*="/models/"]');
    const byId = new Map();
    const pageId = currentPageModelId();
    anchors.forEach(a => {
        if (a.getAttribute(CARD_MARKED_ATTR)) return;
        const href = a.getAttribute('href') || '';
        if (SUBPAGE_RE.test(href)) return;
        const m = href.match(/\/models\/(\d+)(?:\/|\?|$)/);
        if (!m) return;
        const id = parseInt(m[1], 10);
        if (!id) return;
        // 详情页内部指向自己的链接（tab / 锚点）跳过
        if (pageId && id === pageId) return;
        if (!byId.has(id)) byId.set(id, []);
        byId.get(id).push(a);
    });
    return byId;
}

function applyBadgeToAnchor(a, versions, total) {
    a.setAttribute(CARD_MARKED_ATTR, '1');
    // 卡片层只画徽章（不加 noctyra-version-downloaded 绿框 —— 那个 class 是给详情页
    // 小版本选择按钮用的，套在整张卡片上会把卡片整体描成绿色，太重）
    // 通用卡片徽章：必须有真正的卡片容器（带 aspect-ratio / Card / article / li）。
    // 找不到卡片容器 = 详情页里的内联模型文字链接（如 type 栏、相关资源、说明里的链接），
    // 这种不画徽章，否则会把图标直接塞进文字链接，在 type 栏等位置冒出来。
    const host = a.closest('div[style*="aspect-ratio"], [class*="AspectRatioImageCard"], article, li, [class*="Card"], [class*="card"]');
    if (!host) return;
    host.classList.add('noctyra-has-badge');
    if (host.querySelector('.noctyra-card-badge')) return;

    // 按 civitai_version_id 去重（后端可能对同一版本返回多条记录）
    // 统一用 Number() 规整，防止 string vs int 被当成两个版本
    const seen = new Set();
    let count = 0;
    for (const v of versions) {
        const vid = v.civitai_version_id != null ? Number(v.civitai_version_id) : null;
        const key = (Number.isFinite(vid) ? `v:${vid}` : null) || v.file_path || v.file_name;
        if (!key || seen.has(key)) continue;
        seen.add(key);
        count++;
    }

    // 徽章固定在卡片左上角，位置完全交给 CSS（top/left 常量）——
    // 不再 rAF + getBoundingClientRect 锚定三点菜单：那套每张卡每帧重算坐标，
    // 在 CivitAI(Next.js) 频繁重渲染下会抖/错位/卡，还吃 CPU。固定角标稳定且零计算。
    if (getComputedStyle(host).position === 'static') {
        host.style.position = 'relative';
    }
    // 设计：版本下全了只显示 ✓；没下全显示 ✓+已有数(提示该模型共 total 个版本你只有 count 个)。
    // total 未知(0,拿不到 modelVersions)时退回显示数字。
    const complete = total > 0 && count >= total;
    const badge = document.createElement('div');
    badge.className = 'noctyra-card-badge' + (complete ? ' noctyra-card-badge-complete' : '');
    badge.title = total > 0
        ? `Noctyra: 本地已有 ${count}/${total} 个版本${complete ? '（已下全）' : '（未下全）'}`
        : `Noctyra: 本地已有 ${count} 个版本`;
    badge.innerHTML = `
        <svg class="noctyra-card-badge-icon" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
            <path d="M3.5 8.5 L6.8 11.5 L12.5 5" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>${complete ? '' : `<span class="noctyra-card-badge-num">${count}</span>`}`;
    host.appendChild(badge);
}

// ---- Image page resource marking ----

const RESOURCE_MARKED_ATTR = 'data-noctyra-resource';
const RESOURCE_TYPE_KEYWORDS = ['CHECKPOINT', 'LORA', 'LYCORIS', 'EMBEDDING', 'VAE', 'CONTROLNET', 'UPSCALER'];
let resourceScanTimer = null;

function collectResourceAnchors() {
    if (!isImagePage()) return { byId: new Map(), allAnchors: new Map() };
    const anchors = document.querySelectorAll('a[href*="/models/"]');
    const byId = new Map();
    const allAnchors = new Map();
    anchors.forEach(a => {
        const href = a.getAttribute('href') || '';
        const m = href.match(/\/models\/(\d+)(?:\/|\?|$)/);
        if (!m) return;
        const id = parseInt(m[1], 10);
        if (!id) return;
        if (!allAnchors.has(id)) allAnchors.set(id, []);
        allAnchors.get(id).push(a);
        if (a.getAttribute(RESOURCE_MARKED_ATTR)) return;
        if (!byId.has(id)) byId.set(id, a);
    });
    return { byId, allAnchors };
}

function findResourceRow(anchor) {
    let el = anchor;
    for (let i = 0; i < 8 && el.parentElement; i++) {
        el = el.parentElement;
        if (el.offsetWidth > 200) {
            const cs = getComputedStyle(el);
            if (cs.display.includes('flex') && cs.flexDirection === 'row') return el;
        }
    }
    return null;
}

function findTypeBadge(row) {
    const candidates = row.querySelectorAll('span, div');
    for (const el of candidates) {
        if (el.children.length > 2) continue;
        const t = (el.textContent || '').trim().toUpperCase();
        if (RESOURCE_TYPE_KEYWORDS.includes(t)) return el;
    }
    return null;
}

function applyResourceMark(anchor, allLinks, modelId, versions) {
    allLinks.forEach(a => a.setAttribute(RESOURCE_MARKED_ATTR, '1'));

    const row = findResourceRow(anchor);
    if (!row || row.querySelector('.noctyra-resource-mark')) return;

    const href = anchor.getAttribute('href') || '';
    const versionMatch = href.match(/modelVersionId=(\d+)/);
    const versionId = versionMatch ? parseInt(versionMatch[1], 10) : null;

    // 版本精确匹配：资源 anchor 带了 modelVersionId 时，只有本地存在同一版本才算已下载；
    // 否则 CivitAI 侧边栏引用的是 p2_v4 但本地只有 p3_v4 会被误标为已下载。
    let matchedVersion = null;
    if (versionId) {
        matchedVersion = versions.find(v =>
            v.civitai_version_id != null && Number(v.civitai_version_id) === versionId
        ) || null;
    }
    // anchor 没带 modelVersionId（纯 /models/<id>）时退回到旧的"有即已下载"逻辑
    const downloaded = versionId ? !!matchedVersion : versions.length > 0;
    const hasOtherVersions = !downloaded && versions.length > 0;

    // 图片详情页侧边栏：只显示"本地已有 ✓"，不放下载按钮（用户要求只看已有，不在图片页发起下载）。
    // 模型页等其它位置仍保留可点下载标记。
    if (isImagePage() && !downloaded) return;

    const mark = document.createElement('span');
    mark.className = 'noctyra-resource-mark' + (downloaded ? ' downloaded' : ' not-local');
    if (downloaded) {
        const fn = matchedVersion?.file_name
            || versions.map(v => v.file_name || '').filter(Boolean).join(', ');
        mark.title = `Noctyra: 本地已有 (${fn})`;
    } else if (hasOtherVersions) {
        const others = versions.map(v => v.version_name || v.file_name || '').filter(Boolean).join(', ');
        mark.title = `Noctyra: 本地仅有其他版本 (${others})，点击下载当前版本`;
    } else {
        mark.title = '点击下载到 Noctyra';
    }
    if (downloaded) {
        mark.innerHTML = `<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5 L6.5 11 L12.5 5"/></svg>`;
    } else {
        mark.innerHTML = `<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3 V10"/><path d="M5 7.5 L8 10.5 L11 7.5"/><path d="M4 13 H12"/></svg>`;
        mark.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (mark.classList.contains('sending')) return;
            mark.classList.add('sending');
            mark.title = '下载中...';
            const res = await send('extensionDownload', { model_id: modelId, version_id: versionId });
            if (res?.success) {
                mark.classList.remove('not-local', 'sending');
                mark.classList.add('submitted');
                mark.title = '已加入下载队列';
                mark.innerHTML = `<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5 L6.5 11 L12.5 5"/></svg>`;
            } else {
                mark.classList.remove('sending');
                mark.title = `下载失败: ${res?.error || '未知错误'}，点击重试`;
                showToast(`Noctyra: ${res?.error || '下载失败'}`);
            }
        });
    }

    const badge = findTypeBadge(row);
    if (badge) {
        badge.insertAdjacentElement('beforebegin', mark);
    } else {
        row.appendChild(mark);
    }
}

let resourceScanInFlight = false;
let resourceScanPending = false;

async function flushResourceScan() {
    resourceScanTimer = null;
    if (resourceScanInFlight) {
        // 已有一个在跑，标记需要后续再扫一次（合并）
        resourceScanPending = true;
        return;
    }
    resourceScanInFlight = true;
    try {
        const { byId, allAnchors } = collectResourceAnchors();
        if (byId.size === 0) return;

        const toQuery = [];
        for (const id of byId.keys()) {
            if (!hasKnownModel(id)) toQuery.push(id);
        }
        if (toQuery.length > 0) {
            console.debug('[Noctyra] resource-scan 查询', toQuery.length, '个 model_ids');
            const res = await send('checkVersions', { version_ids: [], model_ids: toQuery });
            reportConnection(res);
            if (res?.success) {
                for (const id of toQuery) {
                    rememberModel(id, res.by_model?.[String(id)] || []);
                }
            } else {
                console.warn('[Noctyra] resource-scan 查询失败:', res?.error);
            }
        }
        for (const [id, anchor] of byId.entries()) {
            const versions = getKnownModel(id) || [];
            applyResourceMark(anchor, allAnchors.get(id) || [], id, versions);
        }
    } catch (e) {
        console.debug('[Noctyra] flushResourceScan error', e);
    } finally {
        resourceScanInFlight = false;
        if (resourceScanPending) {
            resourceScanPending = false;
            scheduleResourceScan();
        }
    }
}

function scheduleResourceScan() {
    if (resourceScanTimer) return;
    if (resourceScanInFlight) {
        resourceScanPending = true;
        return;
    }
    resourceScanTimer = setTimeout(flushResourceScan, 300);
}

// ---- 视口驱动卡片扫描（IntersectionObserver）----
// 只查询/标记进入可视区(含 400px 预取)的卡片。旧实现每次轮询都对全页每个模型链接
// 查询/重标，是"图多时滚到才慢慢出"的延迟 + 长时间浏览列表烧 CPU 的根源。
let scanInFlight = false;
let scanPending = false;
let _ioFlushTimer = null;
let _observeTimer = null;
const _pendingCards = new Map();   // modelId -> Set<anchor>，已进视口、待查/标记

const cardIO = ('IntersectionObserver' in window) ? new IntersectionObserver((entries) => {
    let any = false;
    for (const e of entries) {
        if (!e.isIntersecting) continue;
        const a = e.target;
        cardIO.unobserve(a);               // 一次性：标记后不再观察；React 重渲染出新节点会被重新发现
        const id = a._noctyraMid;
        if (!id) continue;
        if (!_pendingCards.has(id)) _pendingCards.set(id, new Set());
        _pendingCards.get(id).add(a);
        any = true;
    }
    if (any) scheduleCardFlush();
}, { rootMargin: '400px 0px' }) : null;

// 发现新的模型卡片链接，交给 IntersectionObserver 观察（进视口才查询）
function observeCards(root = document) {
    if (isImagePage()) return;
    if (!cardIO) { flushCardScan(true); return; }   // 老浏览器兜底：直接全页扫
    const pageId = currentPageModelId();
    root.querySelectorAll('a[href*="/models/"]').forEach(a => {
        if (a.getAttribute(CARD_MARKED_ATTR) || a.hasAttribute('data-noctyra-io')) return;
        const href = a.getAttribute('href') || '';
        if (SUBPAGE_RE.test(href)) return;
        const m = href.match(/\/models\/(\d+)(?:\/|\?|$)/);
        if (!m) return;
        const id = parseInt(m[1], 10);
        if (!id || (pageId && id === pageId)) return;
        a._noctyraMid = id;
        a.setAttribute('data-noctyra-io', '1');
        cardIO.observe(a);
    });
}

function scheduleCardFlush() {
    if (_ioFlushTimer) return;
    _ioFlushTimer = setTimeout(() => flushCardScan(false), 150);
}

async function flushCardScan(fallbackFull) {
    _ioFlushTimer = null;
    if (scanInFlight) { scanPending = true; return; }
    scanInFlight = true;
    try {
        // 主路径：只处理进视口的卡片；兜底/补扫路径：findCardLinks 扫全页未标记链接
        let batch;
        if (fallbackFull || !cardIO) {
            batch = findCardLinks();           // 只返回没有 CARD_MARKED_ATTR 的 anchor
        } else {
            if (_pendingCards.size === 0) return;   // 空就别 clear（修 #1）
            batch = new Map(_pendingCards);
            _pendingCards.clear();
        }
        if (batch.size === 0) return;

        const toQuery = [];
        for (const id of batch.keys()) {
            if (!hasKnownModel(id)) toQuery.push(id);
        }
        for (let i = 0; i < toQuery.length; i += 50) {
            const chunk = toQuery.slice(i, i + 50);
            const res = await send('checkVersions', { version_ids: [], model_ids: chunk });
            reportConnection(res);
            if (!res?.success) continue;   // 网络失败：不写缓存；这些 id 因无 CARD_MARKED_ATTR
                                           // 会被低频补扫(_safetyRescan)重新拾起，不会永久漏标
            for (const id of chunk) rememberModel(id, res.by_model?.[String(id)] || [], res.model_total?.[String(id)] || 0);
        }
        for (const [id, anchors] of batch.entries()) {
            const rows = getKnownModel(id);
            // 查询失败、无缓存 → 不标记(不设 CARD_MARKED_ATTR)，留给补扫重试。
            // 关键修复：之前这些 anchor 已被 unobserve + 带 data-noctyra-io，会被 observeCards
            // 永久跳过。改为同时清掉 io 标记，让其重新可被发现/观察。
            if (!rows) {
                anchors.forEach(a => a.removeAttribute('data-noctyra-io'));
                continue;
            }
            anchors.forEach(a => {
                if (rows.length === 0) a.setAttribute(CARD_MARKED_ATTR, '1');
                else applyBadgeToAnchor(a, rows, getKnownTotal(id));
            });
        }
    } catch (e) {
        console.debug('[Noctyra] flushCardScan error', e);
    } finally {
        scanInFlight = false;
        if (scanPending) { scanPending = false; scheduleScan(); }   // 用 debounce，避免 finally 直调全页扫描
    }
}

// 低频全量补扫兜底：findCardLinks 只看 CARD_MARKED_ATTR，故能重新拾起"网络抖动时
// 漏标"的卡片(后端恢复后即补上徽章)。比旧的 1s 轮询省，又堵住 IO 一次性 unobserve 的漏标。
let _safetyRescan = setInterval(() => { if (document.hidden) return; if (!isImagePage()) flushCardScan(true); }, 8000);

// 兼容旧调用名：现在统一走"发现新卡片→观察→进视口才查"。debounce 避免突变风暴里反复 querySelectorAll
function scheduleScan() {
    if (_observeTimer) return;
    _observeTimer = setTimeout(() => { _observeTimer = null; observeCards(); }, 200);
}

// SPA 路由检测：CivitAI 用 Next.js，可能缓存 history.pushState 绕过我们的 hook，
// 也可能 DOM 不变就完成"局部导航"。三重保险：MutationObserver + 轮询 + popstate
let lastUrl = location.href;
function onUrlMaybeChanged() {
    if (location.href === lastUrl) return;
    lastUrl = location.href;
    injectPanelIfModelPage();
    injectImageSaveButton();
    scheduleScan();
    scheduleResourceScan();
}

// 只有"非扩展自有"的元素增删才算真实变更。否则自激：applyBadge/悬浮按钮/toast 往 DOM
// 写节点 → observer 触发 → scheduleScan 又写节点 …… 防抖压着不死循环但 observer 永不空闲。
function _isRelevantNode(n) {
    if (!n || n.nodeType !== 1) return false;   // 文本/注释节点忽略
    if (typeof n.className === 'string' && n.className.includes('noctyra')) return false;
    if (n.closest && n.closest('[class*="noctyra"]')) return false;
    return true;
}
new MutationObserver((records) => {
    let relevant = false;
    for (const rec of records) {
        for (const n of rec.addedNodes)   { if (_isRelevantNode(n)) { relevant = true; break; } }
        if (relevant) break;
        for (const n of rec.removedNodes) { if (_isRelevantNode(n)) { relevant = true; break; } }
        if (relevant) break;
    }
    if (!relevant) return;
    scheduleScan();
    scheduleResourceScan();
    onUrlMaybeChanged();
}).observe(document.body, { childList: true, subtree: true });

// 1000ms 轮询兜底（MutationObserver + popstate 已覆盖大多数 SPA 路由变更；
// 原来 500ms 过于激进，长时间浏览 civitai 列表页会烧 CPU）
setInterval(() => { if (document.hidden) return; onUrlMaybeChanged(); }, 1000);
window.addEventListener('popstate', onUrlMaybeChanged);

// ---- Ctrl+Shift+H: 隐藏/显示本地已下载的卡片 ----
(async () => {
    const { noctyra_hide_downloaded } = await chrome.storage.sync.get(['noctyra_hide_downloaded']);
    if (noctyra_hide_downloaded) document.documentElement.classList.add('noctyra-hide-downloaded');
})();

window.addEventListener('keydown', async (e) => {
    if (e.ctrlKey && e.shiftKey && (e.key === 'H' || e.key === 'h')) {
        // 在输入框/可编辑区打字时不拦截，避免 preventDefault 吞掉字符
        const t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
        e.preventDefault();
        const on = document.documentElement.classList.toggle('noctyra-hide-downloaded');
        chrome.storage.sync.set({ noctyra_hide_downloaded: on });
        showToast(on ? 'Noctyra: 已隐藏本地已下载' : 'Noctyra: 显示全部');
    }
}, true);

function showToast(msg) {
    const t = document.createElement('div');
    t.className = 'noctyra-toast';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.classList.add('show'), 10);
    setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, 1800);
}

// ---- 连接状态提示（治"装了像没反应"：后端没连上就静默无徽章，用户以为坏了） ----
// 成功的 checkVersions 返回 {success:true}；后端不可达时 background 返回 {ok:false,error:网络/超时}。
let _connState = null;     // null=未知 / true=已连 / false=断开
let _connBanner = null;

function _looksDisconnected(res) {
    if (!res) return true;
    if (res.success === true || res.ok === true) return false;  // 后端正常响应
    if (res.ok === false) return true;                          // background fetch 失败/超时
    if (res.success === false && res.error) {
        return /超时|timeout|fetch|HTTP|未响应|context|invalid/i.test(String(res.error));
    }
    return false;
}

function reportConnection(res) {
    if (_looksDisconnected(res)) {
        if (_connState !== false) { _connState = false; showConnBanner(); }
    } else {
        if (_connState !== true) { _connState = true; hideConnBanner(); }
    }
}

function showConnBanner() {
    if (_connBanner) return;
    try { if (sessionStorage.getItem('noctyra_conn_dismissed') === '1') return; } catch (_) {}
    const el = document.createElement('div');
    el.className = 'noctyra-conn-banner';
    el.innerHTML = `
        <span class="noctyra-conn-dot"></span>
        <span class="noctyra-conn-text">Noctyra 未连接 · 确认 ComfyUI 已运行,端口在扩展图标里设置</span>
        <button class="noctyra-conn-x" title="本次会话不再提示" aria-label="关闭">×</button>`;
    el.querySelector('.noctyra-conn-x').addEventListener('click', () => {
        try { sessionStorage.setItem('noctyra_conn_dismissed', '1'); } catch (_) {}
        hideConnBanner();
    });
    document.body.appendChild(el);
    _connBanner = el;
    requestAnimationFrame(() => el.classList.add('show'));
}

function hideConnBanner() {
    if (!_connBanner) return;
    const el = _connBanner;
    _connBanner = null;
    el.classList.remove('show');
    setTimeout(() => el.remove(), 250);
}

// Initial run + 兜底重扫（Next.js hydration 慢时首次扫描可能为空）
injectPanelIfModelPage();
injectImageSaveButton();
scheduleScan();
scheduleResourceScan();
setTimeout(scheduleScan, 1500);
setTimeout(scheduleResourceScan, 1500);
setTimeout(scheduleScan, 4000);
setTimeout(scheduleResourceScan, 4000);
