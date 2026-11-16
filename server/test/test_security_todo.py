"""Public signup and Gemini trust-boundary checks."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import GeminiApiKey, Team, User
from routers.auth import github_device_poll, register
from routers.gemini import PROXY_KEY_MAX_USES, _require_team_pm, proxy_gemini
from schemas import DevicePollRequest, RegisterRequest


def test_server_signup_requires_invite_for_existing_team_and_ignores_role():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(Team(id="existing", name="Existing"))
        db.commit()
        with pytest.raises(HTTPException) as denied:
            asyncio.run(register(RegisterRequest(name="bad", email="bad@example.invalid", password="pw",
                                                 role="pm", team_name="Existing"), db))
        assert denied.value.status_code == 403
        assert db.query(User).filter(User.email == "bad@example.invalid").first() is None
        founder = asyncio.run(register(RegisterRequest(name="founder", email="founder@example.invalid",
                                                       password="pw", role="backend", team_name="New"), db))
        assert founder.user.role == "pm"
        standalone = asyncio.run(register(RegisterRequest(name="solo", email="solo@example.invalid",
                                                          password="pw", role="pm"), db))
        assert standalone.user.role == "software_engineer"
    engine.dispose()


def test_github_device_login_creates_supported_role():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    class FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, *args, **kwargs):
            return SimpleNamespace(json=lambda: {"access_token": "test-token"})
        def get(self, *args, **kwargs):
            return SimpleNamespace(json=lambda: {"id": 123, "login": "tester", "name": "Tester"})

    with sessionmaker(bind=engine)() as db, patch("httpx.Client", FakeClient):
        result = asyncio.run(github_device_poll(DevicePollRequest(device_code="test"), None, db))
        assert result["user"]["role"] == "software_engineer"
        assert db.query(User).one().role == "software_engineer"
    engine.dispose()


def test_gemini_keys_are_team_pm_only_and_proxy_has_quota():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add_all([Team(id="a", name="A"), Team(id="b", name="B")])
        db.add(GeminiApiKey(id="key", team_id="a", label="A", api_key="fake", is_active=True,
                            usage_count=PROXY_KEY_MAX_USES))
        db.commit()
        pm = SimpleNamespace(id="pm", team_id="a", role="pm")
        with pytest.raises(HTTPException) as denied:
            _require_team_pm(pm, "b")
        assert denied.value.status_code == 403
        with pytest.raises(HTTPException) as denied:
            _require_team_pm(SimpleNamespace(team_id="a", role="backend"), "a")
        assert denied.value.status_code == 403
        with pytest.raises(HTTPException) as denied:
            asyncio.run(proxy_gemini({"body": {}}, team_id="b", user=pm, db=db))
        assert denied.value.status_code == 403
        with pytest.raises(HTTPException) as denied:
            asyncio.run(proxy_gemini({"body": {}}, team_id="a", user=pm, db=db))
        assert denied.value.status_code == 429
        db.query(GeminiApiKey).filter(GeminiApiKey.id == "key").update({"usage_count": 0})
        db.commit()
        response = SimpleNamespace(json=lambda: {"ok": True})
        client = SimpleNamespace(__enter__=lambda self: self, __exit__=lambda *args: None)
        class FakeClient:
            def __init__(self, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def post(self, *args, **kwargs):
                return response
        with patch("httpx.Client", FakeClient):
            assert asyncio.run(proxy_gemini({"body": {}}, team_id="a", user=pm, db=db)) == {"ok": True}
        db.refresh(db.query(GeminiApiKey).filter(GeminiApiKey.id == "key").first())
        assert db.query(GeminiApiKey).filter(GeminiApiKey.id == "key").first().usage_count == 1
    engine.dispose()
