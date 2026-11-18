"""Model entry points must reject callers before loading a pipeline."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.deps import get_current_user, get_current_user_optional
from transport import rest_handler, ws_handler


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/analyze", {"idea": "draft"}),
        ("/api/idea-chat", {"message": "hello"}),
        ("/api/extract-final-idea", {"chat_history": []}),
        ("/api/agile/impact", {"change_description": "change", "sa_data": {}}),
        ("/api/dev-tracking/pr", {"repository": {}, "pull_request": {}}),
        ("/api/doc-sync", {"result_data": {}, "github_token": "token", "owner": "o", "repo": "r"}),
    ],
)
def test_model_rest_routes_reject_anonymous_before_execution(path, body, monkeypatch):
    app = FastAPI()
    app.include_router(rest_handler.rest_router)
    app.dependency_overrides[get_current_user_optional] = lambda: None
    monkeypatch.setattr(rest_handler, "_ensure_pipeline", lambda: None)
    monkeypatch.setattr(
        rest_handler, "execute_pipeline", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pipeline ran")),
        raising=False,
    )
    with patch("pipeline.domain.chat.idea_extractor.extract_final_idea", return_value={"status": "ran"}), \
         patch("pipeline.domain.agile.nodes.impact.run_impact_analyzer", return_value=SimpleNamespace(model_dump=lambda: {})), \
         patch("pipeline.domain.dev_tracking.run_dev_tracking_analysis", return_value={}), \
         patch("pipeline.domain.agile.nodes.doc_sync.sync_docs", return_value={}):
        with TestClient(app) as client:
            assert client.post(path, json=body).status_code == 401


@pytest.mark.parametrize("kind", ["analyze", "idea_chat"])
@pytest.mark.parametrize("token", ["", "invalid.token"])
def test_model_websocket_messages_reject_missing_or_invalid_token(kind, token, monkeypatch):
    app = FastAPI()
    app.add_api_websocket_route("/ws/pipeline", ws_handler.websocket_pipeline)

    async def ran(websocket, payload):
        await websocket.send_json({"type": "result"})

    monkeypatch.setattr(ws_handler, "_ensure_ws_pipeline", lambda: None)
    monkeypatch.setattr(ws_handler, "_run_analysis", ran)
    monkeypatch.setattr(ws_handler, "_run_idea_chat", ran)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/pipeline") as websocket:
            websocket.send_json({"type": kind, "payload": {"auth_token": token}})
            assert websocket.receive_json()["type"] == "error"


def test_authenticated_chat_runs_for_current_team(monkeypatch):
    app = FastAPI()
    app.include_router(rest_handler.rest_router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u", team_id="team", role="pm")
    monkeypatch.setattr(rest_handler, "_ensure_pipeline", lambda: None)
    monkeypatch.setattr(rest_handler, "get_idea_pipeline", lambda: object(), raising=False)
    monkeypatch.setattr(
        rest_handler, "execute_pipeline",
        lambda *args, **kwargs: SimpleNamespace(success=True, data={"reply": "ok"}),
        raising=False,
    )
    with TestClient(app) as client:
        assert client.post("/api/idea-chat", json={"message": "hello"}).json() == {
            "status": "ok", "data": {"reply": "ok"},
        }


def test_authenticated_user_without_team_cannot_run_chat(monkeypatch):
    app = FastAPI()
    app.include_router(rest_handler.rest_router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u", team_id=None, role="pm")
    monkeypatch.setattr(rest_handler, "_ensure_pipeline", lambda: (_ for _ in ()).throw(AssertionError("pipeline loaded")))
    with TestClient(app) as client:
        assert client.post("/api/idea-chat", json={"message": "hello"}).status_code == 403


@pytest.mark.parametrize(("path", "body"), [
    ("/api/analyze", {"idea": "draft", "team_id": "other"}),
    ("/api/dev-tracking/pr", {"repository": {}, "pull_request": {}, "team_id": "other"}),
])
def test_model_routes_reject_foreign_team_before_execution(path, body, monkeypatch):
    app = FastAPI()
    app.include_router(rest_handler.rest_router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u", team_id="mine", role="pm")
    monkeypatch.setattr(rest_handler, "_ensure_pipeline", lambda: (_ for _ in ()).throw(AssertionError("pipeline loaded")))
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 403


def test_extract_uses_verified_header_token_instead_of_body_token():
    app = FastAPI()
    app.include_router(rest_handler.rest_router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u", team_id="mine", role="pm")
    with patch("pipeline.domain.chat.idea_extractor.extract_final_idea", return_value={"status": "ok"}) as extract:
        with TestClient(app) as client:
            response = client.post(
                "/api/extract-final-idea",
                json={"chat_history": [], "auth_token": "untrusted-body-token"},
                headers={"Authorization": "Bearer verified-header-token"},
            )
            assert response.status_code == 200
            assert extract.call_args.kwargs["auth_token"] == "verified-header-token"


@pytest.mark.parametrize(("kind", "role", "expected"), [
    ("analyze", "pm", "result"),
    ("idea_chat", "software_engineer", "result"),
    ("analyze", "software_engineer", "result"),
])
def test_websocket_uses_db_user_role(kind, role, expected, monkeypatch):
    from auth.service import create_access_token

    app = FastAPI()
    app.add_api_websocket_route("/ws/pipeline", ws_handler.websocket_pipeline)

    class DB:
        def close(self):
            pass

    async def ran(websocket, payload):
        await websocket.send_json({"type": "result"})

    monkeypatch.setattr(ws_handler, "SessionLocal", DB)
    monkeypatch.setattr(ws_handler, "get_user_by_id", lambda db, user_id: SimpleNamespace(id=user_id, team_id="team", role=role))
    monkeypatch.setattr(ws_handler, "check_model_call", lambda token: "allowed")
    monkeypatch.setattr(ws_handler, "_ensure_ws_pipeline", lambda: None)
    monkeypatch.setattr(ws_handler, "_run_analysis", ran)
    monkeypatch.setattr(ws_handler, "_run_idea_chat", ran)
    token = create_access_token("u", "u@example.com", "pm")
    with TestClient(app) as client:
        with client.websocket_connect("/ws/pipeline") as websocket:
            websocket.send_json({"type": kind, "payload": {"auth_token": token}})
            assert websocket.receive_json()["type"] == expected


def test_websocket_stops_model_call_at_email_limit(monkeypatch):
    app = FastAPI()
    app.add_api_websocket_route("/ws/pipeline", ws_handler.websocket_pipeline)
    monkeypatch.setattr(ws_handler, "_authenticated_user", lambda payload: SimpleNamespace(id="u", team_id="t", role="pm"))
    monkeypatch.setattr(ws_handler, "check_model_call", lambda token: "limited")
    monkeypatch.setattr(ws_handler, "_ensure_ws_pipeline", lambda: (_ for _ in ()).throw(AssertionError("pipeline loaded")))
    with TestClient(app) as client:
        with client.websocket_connect("/ws/pipeline") as websocket:
            websocket.send_json({"type": "analyze", "payload": {"auth_token": "valid"}})
            assert "10회" in websocket.receive_json()["data"]["message"]


def test_websocket_rejects_non_string_token_without_running(monkeypatch):
    app = FastAPI()
    app.add_api_websocket_route("/ws/pipeline", ws_handler.websocket_pipeline)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/pipeline") as websocket:
            websocket.send_json({"type": "idea_chat", "payload": {"auth_token": {"sub": "u"}}})
            assert websocket.receive_json()["data"]["message"] == "인증이 필요합니다."


def test_websocket_rejects_deleted_user_token(monkeypatch):
    from auth.service import create_access_token

    app = FastAPI()
    app.add_api_websocket_route("/ws/pipeline", ws_handler.websocket_pipeline)

    class DB:
        def close(self):
            pass

    monkeypatch.setattr(ws_handler, "SessionLocal", DB)
    monkeypatch.setattr(ws_handler, "get_user_by_id", lambda db, user_id: None)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/pipeline") as websocket:
            websocket.send_json({
                "type": "idea_chat",
                "payload": {"auth_token": create_access_token("deleted", "u@example.com", "pm")},
            })
            assert websocket.receive_json()["data"]["message"] == "인증이 필요합니다."
