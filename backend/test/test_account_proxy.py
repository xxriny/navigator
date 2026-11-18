from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx

from transport import account_proxy


def test_account_proxy_keeps_user_jwt_separate_from_service_token(monkeypatch):
    monkeypatch.setenv("NAVIGATOR_AUTH_URL", "https://auth.example.run.app")
    monkeypatch.setattr(account_proxy, "fetch_id_token", lambda request, audience: "service-token")
    seen = {}

    async def fake_request(client, method, url, **kwargs):
        seen.update(method=method, url=url, **kwargs)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    app = FastAPI()
    app.include_router(account_proxy.account_router)
    with TestClient(app) as client:
        response = client.post(
            "/account/messages?before=123", json={"body": "hi"},
            headers={"Authorization": "Bearer user-jwt"},
        )
    assert response.status_code == 200
    assert seen["url"] == "https://auth.example.run.app/messages"
    assert seen["headers"]["authorization"] == "Bearer user-jwt"
    assert seen["headers"]["X-Serverless-Authorization"] == "Bearer service-token"
    assert seen["params"]["before"] == "123"


def test_account_proxy_refuses_billing_path(monkeypatch):
    app = FastAPI()
    app.include_router(account_proxy.account_router)
    with TestClient(app) as client:
        assert client.post("/account/billing/checkout").status_code == 404
        assert client.post("/account/auth/../billing/checkout").status_code == 404
