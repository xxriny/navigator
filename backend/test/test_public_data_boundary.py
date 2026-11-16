from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from auth.database import Base, SharedBase, get_db
from auth.deps import get_current_user, get_current_user_optional, require_engineer, require_pm
from auth.models import AnalysisResult, AnalysisSession, DesignChangeRequest, MemoItem
from auth.router import auth_router
from transport.rest_handler import rest_router


def test_session_and_memos_stay_inside_current_team(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'data.db'}", connect_args={"check_same_thread": False})
    SharedBase.metadata.create_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            AnalysisSession(run_id="mine", team_id="team-a"),
            AnalysisSession(run_id="other", team_id="team-b"),
        ])
        db.flush()
        db.add_all([
            AnalysisResult(run_id="mine", shaped_result='{"value": "mine"}'),
            AnalysisResult(run_id="other", shaped_result='{"value": "secret"}'),
            MemoItem(id="foreign-memo", session_id="other", team_id="team-b", text="secret"),
            DesignChangeRequest(id="foreign-change", session_id="other", description="secret"),
        ])
        db.commit()

    def test_db():
        with Session(engine) as db:
            yield db

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(rest_router)
    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u", team_id="team-a")
    app.dependency_overrides[require_engineer] = lambda: SimpleNamespace(id="u", team_id="team-a")
    app.dependency_overrides[require_pm] = lambda: SimpleNamespace(id="u", team_id="team-a")
    with TestClient(app) as client:
        assert client.get("/api/session/other/restore").status_code == 404
        assert client.get("/api/memos?session_id=other").status_code == 404
        assert client.get("/api/session/mine/restore").json()["data"] == {"value": "mine"}
        assert client.post("/api/memos", json={"session_id": "other", "text": "bad"}).status_code == 404
        assert client.post("/api/memos", json={"session_id": "mine", "text": "own"}).status_code == 200
        assert client.delete("/api/memos/foreign-memo").status_code == 200
        assert client.post("/api/memos/apply", json={"memo_ids": ["foreign-memo"]}).json()["updated"] == 0
        assert [m["text"] for m in client.get("/api/memos?session_id=mine").json()["memos"]] == ["own"]
        assert client.get("/api/change-requests").json()["items"] == []
        assert client.patch("/api/change-requests/foreign-change", json={"status": "approved"}).status_code == 404
        assert client.post("/api/change-requests", json={"session_id": "other", "target_section": "x", "description": "bad"}).status_code == 404
        assert client.post("/api/change-requests", json={"session_id": "mine", "target_section": "x", "description": "good"}).status_code == 200

    with Session(engine) as db:
        assert db.get(MemoItem, "foreign-memo").applied is False
    app.dependency_overrides.pop(get_current_user)
    app.dependency_overrides[get_current_user_optional] = lambda: None
    with TestClient(app) as client:
        assert client.get("/api/session/mine/restore").status_code == 401


def test_saved_analysis_records_verified_team(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from auth import service
    from orchestration import pipeline_runner
    from pipeline.core.context import active_jwt_token

    engine = create_engine(f"sqlite:///{tmp_path / 'result.db'}")
    SharedBase.metadata.create_all(engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(pipeline_runner, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(service, "decode_token", lambda token: {"sub": "u"} if token == "verified" else None)
    monkeypatch.setattr(service, "get_user_by_id", lambda db, user_id: SimpleNamespace(id="u", team_id="team-a"))
    token = active_jwt_token.set("verified")
    try:
        pipeline_runner._persist_analysis_result("new-result", {"ok": True})
    finally:
        active_jwt_token.reset(token)
    with Session(engine) as db:
        assert db.get(AnalysisSession, "new-result").team_id == "team-a"
