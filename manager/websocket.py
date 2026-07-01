# ComfyUI-Noctyra-Manager
# Copyright (C) 2026 Noctyra
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
WebSocket 进度推送

管理 WebSocket 连接，向前端广播扫描/匹配/下载的实时进度。
消息以 JSON 格式发送，包含 event/stage/current/total/progress 字段。
内置 300ms 节流，首条和末条消息必发。
"""

import asyncio
import json
import logging
import time
from typing import Optional

from aiohttp import web, WSMsgType

logger = logging.getLogger("noctyra.websocket")


class ProgressWebSocket:
    """WebSocket 进度管理器"""

    def __init__(self):
        self._clients: set[web.WebSocketResponse] = set()
        self._lock = asyncio.Lock()

    async def handle(self, request) -> web.WebSocketResponse:
        """WebSocket 连接处理函数（注册为路由）"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        async with self._lock:
            self._clients.add(ws)

        logger.debug("[Noctyra-MM] WebSocket 客户端已连接，当前 %d 个", len(self._clients))

        try:
            async for msg in ws:
                if msg.type == WSMsgType.PING:
                    await ws.pong(msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            async with self._lock:
                self._clients.discard(ws)
            logger.debug("[Noctyra-MM] WebSocket 客户端已断开，当前 %d 个", len(self._clients))

        return ws

    async def broadcast(self, event: str, data: dict):
        """向所有连接的客户端广播消息"""
        if not self._clients:
            return

        message = json.dumps({"event": event, **data}, ensure_ascii=False)
        dead = []

        for ws in list(self._clients):
            try:
                await ws.send_str(message)
            except ConnectionResetError:
                dead.append(ws)
            except Exception as e:
                logger.debug("[Noctyra-MM] WebSocket 发送失败: %s", e)
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    def make_progress_callback(self, event_prefix: str):
        """生成 progress_callback 供 manager.scan / match_all 使用

        返回的回调签名: async def callback(stage, current, total, detail)
        """
        last_time = [0.0]

        async def callback(stage: str, current: int, total: int, detail: str = ""):
            now = time.time()
            # 节流：最多每 300ms 发送一次，但首条和末条必发
            if current > 0 and current < total and now - last_time[0] < 0.3:
                return
            last_time[0] = now

            await self.broadcast(event_prefix, {
                "stage": stage,
                "current": current,
                "total": total,
                "detail": detail,
                "progress": round(current / total * 100, 1) if total > 0 else 0,
            })

        return callback

    def make_download_progress_callback(self, download_id: str):
        """生成下载进度回调，包含速度和预计剩余时间

        返回的回调签名: async def callback(downloaded, total)
        """
        last_time = [0.0]
        last_bytes = [0]
        start_time = [0.0]
        speed_samples = []

        async def callback(downloaded: int, total: int):
            now = time.time()
            if start_time[0] == 0.0:
                start_time[0] = now

            elapsed_since_last = now - last_time[0]
            if downloaded > 0 and downloaded < total and elapsed_since_last < 0.5:
                return
            last_time[0] = now

            speed = 0.0
            if elapsed_since_last > 0 and last_bytes[0] > 0:
                speed = (downloaded - last_bytes[0]) / elapsed_since_last
                speed_samples.append(speed)
                if len(speed_samples) > 10:
                    speed_samples.pop(0)

            avg_speed = sum(speed_samples) / len(speed_samples) if speed_samples else 0
            remaining = total - downloaded
            eta = remaining / avg_speed if avg_speed > 0 else 0

            last_bytes[0] = downloaded

            await self.broadcast("download_progress", {
                "download_id": download_id,
                "downloaded": downloaded,
                "total": total,
                "progress": round(downloaded / total * 100, 1) if total > 0 else 0,
                "speed": round(avg_speed),
                "eta": round(eta),
            })

        return callback

    async def send_complete(self, event_prefix: str, result: dict):
        """发送操作完成消息"""
        await self.broadcast(event_prefix, {
            "stage": "complete",
            "current": 0,
            "total": 0,
            "progress": 100,
            **result,
        })

    async def send_error(self, event_prefix: str, error: str):
        """发送错误消息"""
        await self.broadcast(event_prefix, {
            "stage": "error",
            "error": error,
        })


_instance: Optional[ProgressWebSocket] = None


def get_progress_ws() -> ProgressWebSocket:
    global _instance
    if _instance is None:
        _instance = ProgressWebSocket()
    return _instance
