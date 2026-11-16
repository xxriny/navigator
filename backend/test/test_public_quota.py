"""Email signups get ten model requests across HTTP and WebSocket."""

from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient


def test_email_signup_has_ten_total_calls(tmp_path):
    from auth.quota import Base, reserve_model_call

    engine = create_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    Base.metadata.create_all(engine)
    email_user = SimpleNamespace(id="email", signup_method="email", password_hash="hashed-password")
    github_user = SimpleNamespace(id="github", signup_method="github", password_hash="hashed-password")
    linked_user = SimpleNamespace(id="linked", signup_method="email", password_hash="hashed-password", github_id="123")

    with Session(engine) as db:
        assert all(reserve_model_call(db, email_user) for _ in range(10))
        assert reserve_model_call(db, email_user) is False
        assert all(reserve_model_call(db, github_user) for _ in range(11))
        assert all(reserve_model_call(db, linked_user) for _ in range(10))
        assert reserve_model_call(db, linked_user) is False

    with Session(engine) as db:
        assert reserve_model_call(db, email_user) is False


def test_github_signup_records_origin(tmp_path):
    from auth.database import SharedBase
    from auth.shared_models import User
    from auth.service import create_or_update_github_user

    engine = create_engine(f"sqlite:///{tmp_path / 'users.db'}")
    SharedBase.metadata.create_all(engine)
    with Session(engine) as db:
        user = create_or_update_github_user(db, "123", "octocat", "octocat@example.com", "Octocat", "token")
        assert user.signup_method == "github"
        assert user.password_hash == ""
        db.add(User(name="Email", email="email@test.invalid", role="software_engineer",
                    password_hash="real-hash", signup_method="email"))
        db.commit()
        linked = create_or_update_github_user(db, "456", "linked", "email@test.invalid", "Linked", "token")
        assert linked.signup_method == "email"
        assert linked.password_hash == "real-hash"


def test_existing_github_users_are_unlimited_after_migration(tmp_path):
    from auth import database
    from auth.shared_models import SharedBase

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    SharedBase.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO users (id, name, email, role, password_hash, github_id)
            VALUES ('github', 'GitHub', 'github@test.invalid', 'software_engineer', 'synthetic-hash', '123')
        """))
        conn.execute(text("""
            INSERT INTO users (id, name, email, role, password_hash, github_id, signup_method)
            VALUES ('linked', 'Linked', 'linked@test.invalid', 'software_engineer', 'real-hash', '456', 'email')
        """))
    with patch.object(database, "engine", engine), patch.object(database, "shared_engine", engine):
        database._run_migrations()
    with engine.connect() as conn:
        methods = dict(conn.execute(text("SELECT id, signup_method FROM users")).all())
    assert methods == {"github": "github", "linked": "email"}


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
