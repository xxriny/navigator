"""
WebSocket 엔드포인트 (REQ-001)
연결 관리와 메시지 dispatch를 담당하며,
실제 파이프라인 실행은 orchestration 계층에 위임한다.
"""

from __future__ import annotations

import json

from fastapi import WebSocket, WebSocketDisconnect

from auth.database import SessionLocal
from auth.service import decode_token, get_user_by_id
from transport.connection_manager import manager
from observability.logger import get_logger

# pipeline_runner는 langgraph를 포함하므로 첫 WS 연결 시 지연 로드
_run_analysis = None
_run_idea_chat = None

def _ensure_ws_pipeline():
    global _run_analysis, _run_idea_chat
    if _run_analysis is not None:
        return
    from orchestration.pipeline_runner import run_analysis, run_idea_chat
    _run_analysis = run_analysis
    _run_idea_chat = run_idea_chat


async def _safe_send_error(websocket: WebSocket, message: str) -> None:
    """
    소켓이 이미 끊겼을 수도 있으므로 별도 try/except로 감싼 에러 프레임 전송.
    """
    try:
        await manager.send_json(websocket, {
            "type": "error",
            "data": {"message": message},
        })
    except Exception as send_err:  # noqa: BLE001 - 마지막 보호선
        get_logger().warning(f"[WS] failed to send error frame: {send_err}")


def _authenticated_user(payload: dict):
    if not isinstance(payload, dict):
        return None
    raw_token = payload.get("auth_token")
    if not isinstance(raw_token, str) or not raw_token:
        return None
    token = decode_token(raw_token)
    if not token:
        return None
    db = SessionLocal()
    try:
        user = get_user_by_id(db, token.get("sub", ""))
        return user if user and user.team_id else None
    except Exception:
        get_logger().exception("WebSocket user lookup failed")
        return None
    finally:
        db.close()


async def websocket_pipeline(websocket: WebSocket):
    """
    WebSocket 파이프라인 엔드포인트.

    Client → Server:
        {"type": "analyze"|"idea_chat"|"ping", "payload": {...}}

    Server → Client:
        {"type": "status"|"thinking"|"result"|"error"|"pong", "node": "...", "data": {...}}
    """
    await manager.connect(websocket)
    get_logger().info(f"[WS] Client connected. Active: {len(manager.active_connections)}")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_json(websocket, {
                    "type": "error",
                    "data": {"message": "Invalid JSON"},
                })
                continue

            msg_type = msg.get("type", "")
            payload = msg.get("payload", {})

            if msg_type in {"analyze", "idea_chat"}:
                user = _authenticated_user(payload)
                if not user:
                    await _safe_send_error(websocket, "인증이 필요합니다.")
                    continue
            if msg_type == "analyze":
                try:
                    _ensure_ws_pipeline()
                except Exception as e:  # noqa: BLE001 - ImportError 등을 사용자에게 노출
                    get_logger().error(f"[WS] _ensure_ws_pipeline failed (analyze): {e}")
                    await _safe_send_error(websocket, f"Backend init failed: {e}")
                    continue
                await _run_analysis(websocket, payload)
            elif msg_type == "idea_chat":
                try:
                    _ensure_ws_pipeline()
                except Exception as e:  # noqa: BLE001 - ImportError 등을 사용자에게 노출
                    get_logger().error(f"[WS] _ensure_ws_pipeline failed (idea_chat): {e}")
                    await _safe_send_error(websocket, f"Backend init failed: {e}")
                    continue
                await _run_idea_chat(websocket, payload)
            elif msg_type == "ping":
                await manager.send_json(websocket, {"type": "pong"})
            else:
                await manager.send_json(websocket, {
                    "type": "error",
                    "data": {"message": f"Unknown type: {msg_type}"},
                })

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        get_logger().info(f"[WS] Client disconnected. Active: {len(manager.active_connections)}")
    except Exception as e:
        get_logger().error(f"[WS Error] {e}")
        # disconnect 전에 사용자에게 에러 프레임 전송 시도 (S2 영구 해소)
        await _safe_send_error(websocket, f"Internal error: {e}")
        manager.disconnect(websocket)
