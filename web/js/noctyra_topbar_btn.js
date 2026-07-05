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

import { app } from "../../scripts/app.js";

const MANAGER_PATH = "/noctyra-manager";
const BUTTON_TOOLTIP = "Noctyra";
const BUTTON_GROUP_CLASS = "noctyra-manager-top-menu-group";
const MAX_ATTACH_ATTEMPTS = 120;

const openManager = (event) => {
    const url = `${window.location.origin}${MANAGER_PATH}`;
    // 命名窗口 "noctyra-manager"：再次点击复用同一个标签/窗口并聚焦，
    // 不再每次点开攒一个重复标签。actionBarButtons 的 action 回调不一定传 event，安全检查。
    const win = (event && event.shiftKey)
        ? window.open(url, "noctyra-manager", "width=1200,height=800,resizable=yes,scrollbars=yes")
        : window.open(url, "noctyra-manager");
    try { win && win.focus(); } catch (_) { /* 跨源/被拦截时忽略 */ }
};

// 顶部菜单按钮图标：用网站新图标（白底动漫女孩）；?v 防旧缓存
const getIconHtml = () => `<img src="/noctyra_static/images/noctyra-logo.svg?v=13.0" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:6px;display:block">`;

// Legacy 按钮（旧版 ComfyUI）
const attachTopMenuButton = async (attempt = 0) => {
    if (document.querySelector(`.${BUTTON_GROUP_CLASS}`)) return;

    const settingsGroup = app.menu?.settingsGroup;
    if (!settingsGroup?.element?.parentElement) {
        if (attempt >= MAX_ATTACH_ATTEMPTS) {
            console.warn("[Noctyra] unable to locate ComfyUI settings button group.");
            return;
        }
        requestAnimationFrame(() => attachTopMenuButton(attempt + 1));
        return;
    }

    try {
        const { ComfyButton } = await import("../../scripts/ui/components/button.js");
        const { ComfyButtonGroup } = await import("../../scripts/ui/components/buttonGroup.js");

        const button = new ComfyButton({
            icon: "noctyra-manager",
            tooltip: BUTTON_TOOLTIP,
            app,
            enabled: true,
            classList: "comfyui-button comfyui-menu-mobile-collapse",
        });

        button.element.setAttribute("aria-label", BUTTON_TOOLTIP);
        button.element.title = BUTTON_TOOLTIP;
        if (button.iconElement) {
            button.iconElement.innerHTML = getIconHtml();
        }
        button.element.addEventListener("click", openManager);

        const group = new ComfyButtonGroup(button);
        group.element.classList.add(BUTTON_GROUP_CLASS);
        settingsGroup.element.before(group.element);
    } catch (e) {
        console.error("[Noctyra] failed to create button:", e);
    }
};

// 注册扩展
(async () => {
    const ext = {
        name: "Noctyra.ModelManager",
        async setup() {
            // 注入样式
            if (!document.getElementById("noctyra-manager-btn-styles")) {
                const style = document.createElement("style");
                style.id = "noctyra-manager-btn-styles";
                style.textContent = `
                    /* 圆角方形图标 chip：直接显示网站图标，去掉蓝底，hover 才出背景 */
                    button[aria-label="${BUTTON_TOOLTIP}"] {
                        background: transparent !important;
                        border: none !important;
                        box-shadow: none !important;
                        width: 28px !important;
                        height: 28px !important;
                        min-width: 28px !important;
                        padding: 0 !important;
                        border-radius: 8px !important;
                        display: inline-flex !important;
                        align-items: center !important;
                        justify-content: center !important;
                        color: #eaf1fc !important;
                        transition: background-color .15s ease, color .15s ease !important;
                    }
                    /* 容器 mx-2 让左侧多出 8px → 左拉 8px 使左右间隙对称（都 8px） */
                    .${BUTTON_GROUP_CLASS} { margin-left: -8px !important; }
                    button[aria-label="${BUTTON_TOOLTIP}"]:hover {
                        background-color: var(--p-button-text-secondary-hover-background, var(--comfy-input-bg, rgba(255,255,255,0.10))) !important;
                        color: var(--fg-color, #ffffff) !important;
                    }
                    /* 聚焦态去掉旧版按钮可能带的白色实心高亮，避免点一下变白方块 */
                    button[aria-label="${BUTTON_TOOLTIP}"]:focus,
                    button[aria-label="${BUTTON_TOOLTIP}"]:focus-visible {
                        background-color: transparent !important;
                        outline: none !important;
                        box-shadow: none !important;
                    }
                    button[aria-label="${BUTTON_TOOLTIP}"]:active {
                        background-color: var(--p-button-text-secondary-active-background, var(--comfy-input-bg, rgba(255,255,255,0.14))) !important;
                    }
                    button[aria-label="${BUTTON_TOOLTIP}"] .mdi {
                        width: 24px !important;
                        height: 24px !important;
                        margin: 0 !important;
                        font-size: 0 !important;
                        line-height: 0 !important;
                        display: inline-flex !important;
                        align-items: center !important;
                        justify-content: center !important;
                    }
                    /* mdi-noctyra-manager 不是真实 MDI 图标，其字体 ::before 会渲染
                       缺字"豆腐块"白方框盖住我们的 SVG —— 必须抹掉 */
                    button[aria-label="${BUTTON_TOOLTIP}"] .mdi::before {
                        content: none !important;
                        display: none !important;
                    }
                    button[aria-label="${BUTTON_TOOLTIP}"] .mdi svg,
                    button[aria-label="${BUTTON_TOOLTIP}"] .mdi img {
                        width: 24px !important;
                        height: 24px !important;
                        display: block !important;
                        object-fit: cover !important;
                        border-radius: 6px !important;
                    }
                `;
                document.head.appendChild(style);
            }

            // 直接尝试 legacy 按钮
            await attachTopMenuButton();
        },
    };

    app.registerExtension(ext);
})();
