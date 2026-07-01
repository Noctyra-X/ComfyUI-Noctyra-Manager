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
 * 画布接收器：接收工作流图库详情页"发送到画布"发来的 ComfyUI 工作流，
 * 用 app.loadGraphData 加载到当前画布。
 *
 * 通信走同源（图库页与 ComfyUI 同一个后端端口）：
 *   1) BroadcastChannel('noctyra-canvas') —— 画布已打开时实时收到。
 *   2) localStorage 'noctyra_pending_workflow' —— 图库先发、画布后开/刷新的兜底（60s 内有效）。
 */
import { app } from "../../scripts/app.js";

const PENDING_KEY = "noctyra_pending_workflow";
const PENDING_TTL = 60000;

function loadWorkflowToCanvas(wf, name) {
    if (!wf) return;
    try {
        const graph = typeof wf === "string" ? JSON.parse(wf) : wf;
        // editor 格式的工作流图；loadGraphData 是 ComfyUI 打开工作流用的标准入口
        app.loadGraphData(graph);
        console.log(`%c[Noctyra] 已从图库加载工作流到画布${name ? "：" + name : ""}`, "color:#22c55e;font-weight:600");
        try { app.graph?.setDirtyCanvas(true, true); } catch (_) {}
    } catch (e) {
        console.error("[Noctyra] 加载工作流到画布失败:", e);
    }
}

function consumePending() {
    try {
        const raw = localStorage.getItem(PENDING_KEY);
        if (!raw) return;
        const obj = JSON.parse(raw);
        // 取出即清，避免下次再触发；只认 60s 内的（用户刚点过"发送到画布"）
        localStorage.removeItem(PENDING_KEY);
        if (obj && obj.workflow && (Date.now() - (obj.ts || 0)) < PENDING_TTL) {
            loadWorkflowToCanvas(obj.workflow, obj.name);
        }
    } catch (_) { /* 忽略 */ }
}

app.registerExtension({
    name: "Noctyra.CanvasReceiver",
    setup() {
        console.log("%c[Noctyra] 画布接收器已加载（图库可发送工作流到画布）", "color:#2d7ff9;font-weight:700");

        // 1) 实时接收：画布已打开时，图库点"发送到画布"立即加载
        try {
            const ch = new BroadcastChannel("noctyra-canvas");
            ch.onmessage = (e) => {
                if (e.data && e.data.type === "load-workflow" && e.data.workflow) {
                    // 实时收到即清暂存，避免切回标签页时 consumePending 再加载一次
                    try { localStorage.removeItem(PENDING_KEY); } catch (_) {}
                    loadWorkflowToCanvas(e.data.workflow, e.data.name);
                }
            };
        } catch (_) { /* 浏览器不支持 BroadcastChannel 时忽略，靠 localStorage 兜底 */ }

        // 2) 兜底：画布刚打开/刷新时捞一下暂存的（图库先发的情况）。
        //    延后一拍，等 ComfyUI 自身恢复上次工作流之后再覆盖，避免竞态。
        setTimeout(consumePending, 600);

        // 标签页重新可见时也捞一次（图库在另一个标签发了，用户切回画布）
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden) consumePending();
        });
    },
});
