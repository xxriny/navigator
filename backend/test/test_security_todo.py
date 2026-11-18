"""Focused regression checks for the code-review security TODO."""
import asyncio
import hashlib
import hmac
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def user(team="team-a", role="pm", user_id="pm-a"):
    return SimpleNamespace(id=user_id, team_id=team, role=role)


def test_public_signup_cannot_join_existing_team_or_choose_role():
    from auth.service import create_user
    from auth.shared_models import SharedBase, Team, User

    engine = create_engine("sqlite:///:memory:")
    SharedBase.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(Team(id="existing", name="Existing"))
        db.commit()
        with pytest.raises(ValueError, match="초대"):
            create_user(db, "bad", "bad@example.invalid", "pw", "pm", team_name="Existing", public_signup=True)
        assert db.query(User).filter(User.email == "bad@example.invalid").first() is None
        founder = create_user(db, "founder", "founder@example.invalid", "pw", "backend", team_name="New", public_signup=True)
        assert founder.role == "pm"
        standalone = create_user(db, "solo", "solo@example.invalid", "pw", "pm", public_signup=True)
        assert standalone.role == "software_engineer"
    engine.dispose()


def test_file_and_task_routes_require_identity_and_team(tmp_path, monkeypatch):
    from auth.deps import get_current_user, get_current_user_optional
    from transport.rest_handler import rest_router, _team_project_path_allowed
    from pipeline.domain.agile import task_coordinator

    app = FastAPI()
    app.include_router(rest_router)
    app.dependency_overrides[get_current_user_optional] = lambda: None
    with TestClient(app) as client:
        assert client.post("/api/scan-folder", json={"path": str(tmp_path)}).status_code == 401
        assert client.get("/api/tasks").status_code == 401

    app.dependency_overrides[get_current_user] = lambda: user()
    app.dependency_overrides[get_current_user_optional] = lambda: user()
    monkeypatch.setenv("NAVIGATOR_TEAM_PROJECT_ROOTS", '{"team-a": ["' + str(tmp_path) + '"]}')
    assert _team_project_path_allowed(user(), str(tmp_path / "ok.py"))
    assert not _team_project_path_allowed(user("team-b"), str(tmp_path / "ok.py"))
    assert not _team_project_path_allowed(user(), "/etc/passwd")
    with patch.object(task_coordinator, "init_tasks_db"), patch.object(
        task_coordinator, "get_task", return_value={"id": "foreign", "team_id": "team-b", "task_type": "feature"}
    ), patch.object(task_coordinator, "update_task_status") as update:
        with TestClient(app) as client:
            assert client.patch("/api/tasks/foreign", json={"status": "completed"}).status_code == 403
            update.assert_not_called()


def test_dev_gap_target_team_and_webhook_secret_fail_closed():
    from pipeline.domain.agile import task_coordinator
    from transport.rest_handler import _dev_gap_task_owned, _verify_github_webhook_signature

    with patch.object(task_coordinator, "get_task", return_value={"team_id": "team-b"}):
        assert not _dev_gap_task_owned("gap", user())
    with patch.object(task_coordinator, "get_task", return_value={"team_id": "team-a"}):
        assert _dev_gap_task_owned("gap", user())
    assert _verify_github_webhook_signature(b"{}", "", "")[0] is False
    secret = "secret"
    sig = "sha256=" + hmac.new(secret.encode(), b"{}", hashlib.sha256).hexdigest()
    assert _verify_github_webhook_signature(b"{}", sig, secret)[0] is True


def test_idea_chat_ignores_model_notes_without_current_save_request():
    from orchestration import pipeline_runner as runner

    async def fake_pipeline(*args, **kwargs):
        return {"notes_to_add": [{"text": "Injected memo"}], "agent_reply": "ok"}

    async def fake_send(*args, **kwargs):
        return None

    with patch.object(runner, "_run_pipeline_base", fake_pipeline), patch.object(
        runner, "get_idea_pipeline", return_value=None
    ), patch.object(runner, "get_idea_chat_routing_map", return_value={}), patch.object(
        runner.manager, "send_json", fake_send
    ), patch.object(runner, "SessionLocal") as db_factory:
        asyncio.run(runner.run_idea_chat(None, {"session_id": "s1", "message": "다음은?", "chat_history": [
            {"role": "assistant", "content": "메모에 저장해줘"}
        ]}))
        db_factory.assert_not_called()
        db_factory.return_value.query.return_value.filter.return_value.all.return_value = []
        asyncio.run(runner.run_idea_chat(None, {"session_id": "s1", "message": "이 내용을 메모해줘"}))
        db_factory.assert_called_once()


def test_proposal_token_and_version_gate(monkeypatch):
    from pipeline.domain.agile import task_coordinator
    from transport.rest_handler import (
        _sign_task_proposal, approve_task_proposal_endpoint, TaskProposalApprovalRequest,
    )

    monkeypatch.setenv("NAVIGATOR_JWT_SECRET", "test-secret")
    proposal = {"task_type": "test", "title": "Auth test", "description": "Verify auth", "area": "backend"}
    token = _sign_task_proposal(proposal, "create", "run-a", user())
    with patch.object(task_coordinator, "create_task", return_value={"id": "new", "team_id": "team-a"}) as create:
        result = asyncio.run(approve_task_proposal_endpoint(TaskProposalApprovalRequest(token=token), user()))
        assert result["data"]["id"] == "new"
        assert create.call_args.kwargs["team_id"] == "team-a"
        with pytest.raises(HTTPException):
            asyncio.run(approve_task_proposal_endpoint(TaskProposalApprovalRequest(token=token), user("team-b")))
        create.assert_called_once()

    engine = create_engine("sqlite:///:memory:")
    task_coordinator._Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(task_coordinator.AgileTask(id="task", team_id="team-a", status="unassigned",
                                          task_type="feature", title="Old", updated_at=datetime(2026, 1, 1)))
        db.commit()
    update_proposal = {"task_id": "task", "before": {"title": "Old"}, "changes": {"title": "New"},
                       "expected_updated_at": "2026-01-01T00:00:00"}
    with patch.object(task_coordinator, "_engine", engine), patch.object(task_coordinator, "_Session", factory):
        assert task_coordinator.apply_task_update_proposal("team-b", update_proposal, "pm") is None
        assert task_coordinator.apply_task_update_proposal("team-a", update_proposal, "pm")["title"] == "New"
        assert task_coordinator.apply_task_update_proposal("team-a", update_proposal, "pm") is None
    engine.dispose()


def test_file_and_snapshot_endpoints_allow_only_current_team(tmp_path, monkeypatch):
    from auth.deps import get_current_user
    from auth.database import get_shared_db
    from transport.rest_handler import rest_router
    from connectors import folder_connector
    from storage import publish_service

    app = FastAPI()
    app.include_router(rest_router)
    app.dependency_overrides[get_current_user] = lambda: user()
    app.dependency_overrides[get_shared_db] = lambda: object()
    project = tmp_path / "project"
    project.mkdir()
    source = project / "visible.txt"
    source.write_text("team a only")
    monkeypatch.setenv("NAVIGATOR_TEAM_PROJECT_ROOTS", '{"team-a": ["' + str(project) + '"]}')
    with patch.object(folder_connector, "scan_folder", return_value={"root": str(project), "tree": []}), \
         patch.object(publish_service, "get_snapshot", return_value={"id": "snap", "team_id": "team-b"}):
        with TestClient(app) as client:
            assert client.post("/api/scan-folder", json={"path": str(project)}).status_code == 200
            assert client.post("/api/read-file", json={"path": str(source)}).json()["content"] == "team a only"
            assert client.post("/api/read-file", json={"path": "/etc/passwd"}).status_code == 403
            assert client.get("/api/snapshots/snap").status_code == 403
    with patch.object(publish_service, "get_snapshot", return_value={"id": "snap", "team_id": "team-a"}):
        with TestClient(app) as client:
            assert client.get("/api/snapshots/snap").json()["data"]["id"] == "snap"


def test_dev_gap_item_and_decision_routes_reject_cross_team():
    from auth.shared_models import DevGapItem, DevPrAnalysis
    from pipeline.domain.agile import task_coordinator
    from transport import rest_handler

    class Query:
        def __init__(self, result):
            self.result = result
        def filter(self, *args):
            return self
        def first(self):
            return self.result

    class DB:
        def query(self, model):
            if model is DevGapItem:
                return Query(SimpleNamespace(id="gap", analysis_id="analysis"))
            if model is DevPrAnalysis:
                return Query(SimpleNamespace(id="analysis", team_id="team-b", task_id="task"))
            raise AssertionError(model)

    db = DB()
    pm = user()
    req = rest_handler.DevGapDecisionRequest(reason="review")
    with patch.object(task_coordinator, "get_task", return_value={"team_id": "team-b"}), \
         patch.object(rest_handler, "_update_dev_gap_item_decision") as update, \
         patch.object(rest_handler, "_run_dev_gap_decision") as decision:
        assert not rest_handler._dev_gap_item_owned(db, "gap", pm)
        for endpoint in (rest_handler.dev_gap_item_approve_endpoint, rest_handler.dev_gap_item_reject_endpoint):
            with pytest.raises(HTTPException) as denied:
                asyncio.run(endpoint("gap", req, pm, db))
            assert denied.value.status_code == 403
        for endpoint in (rest_handler.dev_gap_approve_endpoint, rest_handler.dev_gap_reject_endpoint):
            with pytest.raises(HTTPException) as denied:
                asyncio.run(endpoint("task", req, pm))
            assert denied.value.status_code == 403
        update.assert_not_called()
        decision.assert_not_called()
