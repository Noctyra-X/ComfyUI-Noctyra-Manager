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

// 一次性守卫：启动时只在"首个 configureGraph（= ComfyUI 恢复上次工作流）之后"捞一次暂存，
// 避免固定 setTimeout 猜恢复完成。afterConfigureGraph / 长兜底定时器都经它，防重入。
let _consumedInitial = false;

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

// 首个 configureGraph 后捞暂存：ComfyUI 恢复上次工作流的最终 load 也会触发此钩子，
// 它确定性地发生在恢复完成之后 → 我们的 loadGraphData 稳稳后到、覆盖恢复的图，不再靠猜时延。
// 一次性守卫保证只在启动那一次生效；之后用户自己开工作流的 configureGraph 不再重复捞。
function consumeInitialOnce() {
    if (_consumedInitial) return;
    _consumedInitial = true;
    consumePending();
}

app.registerExtension({
    name: "Noctyra.CanvasReceiver",
    afterConfigureGraph() {
        consumeInitialOnce();
    },
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

        // 2) 长兜底：万一 afterConfigureGraph 没触发（旧版 ComfyUI / 异常），仍在较晚时刻捞一次。
        //    留足够长（8s），确保正常情况下钩子先到、守卫已置位，此定时器只在钩子缺席时兜底。
        setTimeout(consumeInitialOnce, 8000);

        // 标签页重新可见时也捞一次（图库在另一个标签发了，用户切回画布）。
        // 走独立路径（不经一次性守卫），因为这是启动之后新发来的暂存。
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden) consumePending();
        });
    },
});
