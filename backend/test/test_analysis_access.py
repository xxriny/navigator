"""Any authenticated team member may start an analysis; outsiders may not."""

from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.database import get_shared_db
from auth.deps import get_current_user
from transport import rest_handler, ws_handler


def test_team_member_can_analyze_rest():
    app = FastAPI()
    app.include_router(rest_handler.rest_router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="member", team_id="team", role="software_engineer", github_id=None,
    )
    app.dependency_overrides[get_shared_db] = lambda: None
    with patch.object(rest_handler, "_ensure_pipeline"), \
         patch.object(rest_handler, "normalize_action_type", return_value="CREATE", create=True), \
         patch.object(rest_handler, "validate_analysis_inputs", return_value=None, create=True), \
         patch.object(rest_handler, "get_analysis_pipeline", return_value=object(), create=True), \
         patch.object(rest_handler, "analysis_pipeline_type", return_value="analysis", create=True), \
         patch.object(rest_handler, "execute_pipeline", return_value=SimpleNamespace(success=True, data={"ok": True}), create=True):
        with TestClient(app) as client:
            response = client.post("/api/analyze", json={"idea": "draft", "team_id": "team"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_team_member_can_analyze_websocket():
    app = FastAPI()
    app.add_api_websocket_route("/ws/pipeline", ws_handler.websocket_pipeline)

    async def result(websocket, payload):
        await websocket.send_json({"type": "result"})

    with patch.object(ws_handler, "_authenticated_user", return_value=SimpleNamespace(
        id="member", team_id="team", role="software_engineer", github_id=None,
    )), patch.object(ws_handler, "check_model_call", return_value="allowed"), \
         patch.object(ws_handler, "_ensure_ws_pipeline"), \
         patch.object(ws_handler, "_run_analysis", result):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/pipeline") as websocket:
                websocket.send_json({"type": "analyze", "payload": {"auth_token": "valid"}})
                assert websocket.receive_json()["type"] == "result"


def test_user_without_team_cannot_analyze_rest():
    app = FastAPI()
    app.include_router(rest_handler.rest_router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="member", team_id=None, role="software_engineer",
    )
    with patch.object(rest_handler, "_ensure_pipeline", side_effect=AssertionError("pipeline loaded")):
        with TestClient(app) as client:
            assert client.post("/api/analyze", json={"idea": "draft"}).status_code == 403
