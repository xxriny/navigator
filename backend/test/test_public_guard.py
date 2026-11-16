from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_public_api_requires_login_and_team(monkeypatch):
    import main
    from transport import public_guard

    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.delenv("NAVIGATOR_GITHUB_WEBHOOK_SECRET", raising=False)
    with TestClient(main.app) as client:
        assert client.post("/api/upload-doc").status_code == 401
        assert client.get("/api/dev-tracking/analyses?team_id=other").status_code == 401
        assert client.post("/api/github/issues/import").status_code == 404
        assert client.post("/api/webhook/github", json={}).status_code == 503

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(public_guard, "decode_token", lambda token: {"sub": "u"})
    monkeypatch.setattr(public_guard, "SessionLocal", DB)
    monkeypatch.setattr(public_guard, "get_user_by_id", lambda db, user_id: SimpleNamespace(team_id="mine"))
    with TestClient(main.app) as client:
        headers = {"Authorization": "Bearer valid"}
        assert client.get("/api/dev-tracking/analyses?team_id=other", headers=headers).status_code == 403
        assert client.post("/api/dev-tracking/knowledge/query", json={"team_id": "other"}, headers=headers).status_code == 403
