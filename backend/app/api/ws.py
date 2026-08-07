"""WebSocket manager for real-time task progress updates."""

import asyncio
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import JwtAuthenticationError, authenticate_authorization_header
from ..database import get_db

ws_router = APIRouter(tags=["websocket"])


class ConnectionManager:
    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, task_id: str, ws: WebSocket):
        await ws.accept(subprotocol="doctrans-v1")
        async with self._lock:
            if task_id not in self._connections:
                self._connections[task_id] = set()
            self._connections[task_id].add(ws)

    async def disconnect(self, task_id: str, ws: WebSocket):
        async with self._lock:
            if task_id in self._connections:
                self._connections[task_id].discard(ws)
                if not self._connections[task_id]:
                    del self._connections[task_id]

    async def broadcast(self, task_id: str, data: dict):
        async with self._lock:
            connections = list(self._connections.get(task_id, []))
        for ws in connections:
            try:
                await ws.send_json(data)
            except Exception:
                pass


manager = ConnectionManager()


async def _send_current_task_state(websocket: WebSocket, task_id: str):
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, status, progress, total_paragraphs, translated_paragraphs,
                  error_message, started_at, completed_at
           FROM translation_tasks WHERE id = ?""",
        (task_id,),
    )
    row = await cursor.fetchone()
    await db.close()
    if not row:
        return

    await websocket.send_json({
        "task_id": row["id"],
        "status": row["status"],
        "progress": row["progress"],
        "total_paragraphs": row["total_paragraphs"],
        "translated_paragraphs": row["translated_paragraphs"],
        "error_message": row["error_message"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    })


def _get_websocket_token(websocket: WebSocket) -> str | None:
    requested_protocols = websocket.headers.get("sec-websocket-protocol", "")
    for protocol in requested_protocols.split(","):
        protocol = protocol.strip()
        if protocol.startswith("jwt."):
            return protocol[len("jwt."):]
    return None


async def _task_belongs_to_user(task_id: str, workid: str) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT 1 FROM translation_tasks WHERE id = ? AND owner_workid = ?",
            (task_id, workid),
        )
        return await cursor.fetchone() is not None
    finally:
        await db.close()


@ws_router.websocket("/ws/tasks/{task_id}")
async def websocket_task_progress(websocket: WebSocket, task_id: str):
    token = _get_websocket_token(websocket)
    try:
        user = authenticate_authorization_header(f"Bearer {token}" if token else None)
    except JwtAuthenticationError:
        await websocket.close(code=1008)
        return

    if not await _task_belongs_to_user(task_id, user.workid):
        await websocket.close(code=1008)
        return

    await manager.connect(task_id, websocket)
    await _send_current_task_state(websocket, task_id)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(task_id, websocket)
