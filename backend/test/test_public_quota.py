"""Email signups get ten model requests across HTTP and WebSocket."""

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient


def test_email_signup_has_ten_total_calls(tmp_path):
    from auth.quota import Base, reserve_model_call

    engine = create_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    Base.metadata.create_all(engine)
    email_user = SimpleNamespace(id="email", password_hash="hashed-password")
    github_user = SimpleNamespace(id="github", password_hash="")

    with Session(engine) as db:
        assert all(reserve_model_call(db, email_user) for _ in range(10))
        assert reserve_model_call(db, email_user) is False
        assert reserve_model_call(db, github_user) is True

    with Session(engine) as db:
        assert reserve_model_call(db, email_user) is False


def test_http_limit_blocks_before_pipeline(monkeypatch):
    import main
    from transport import rest_handler

    monkeypatch.setattr(main, "init_db", lambda: None)
    async def allow_request(request):
        return None
    monkeypatch.setattr(main, "check_public_api", allow_request)
    monkeypatch.setattr(main, "check_model_call", lambda token: "limited")
    monkeypatch.setattr(rest_handler, "_ensure_pipeline", lambda: (_ for _ in ()).throw(AssertionError("pipeline loaded")))
    with TestClient(main.app) as client:
        response = client.post("/api/idea-chat", json={"message": "hello"}, headers={"Authorization": "Bearer email-token"})
    assert response.status_code == 429
    assert "10회" in response.json()["detail"]
