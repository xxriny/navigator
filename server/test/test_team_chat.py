"""Team channel and direct conversation boundaries."""

import asyncio

import pytest
from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from database import get_db
from models import Team, TeamMember, User
from routers.messages import (
    ChannelCreateRequest,
    ChannelMessageRequest,
    SendMessageRequest,
    create_channel,
    get_thread,
    list_channel_messages,
    list_channels,
    send_channel_message,
    send_message,
)
from routers import messages as messages_router
from routers.auth import get_current_user


def test_channels_keep_messages_inside_the_current_team():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add_all([
            Team(id="a", name="A"), Team(id="b", name="B"),
            User(id="alice", team_id="a", name="Alice", email="alice@test.invalid"),
            User(id="bob", team_id="a", name="Bob", email="bob@test.invalid"),
            User(id="eve", team_id="b", name="Eve", email="eve@test.invalid"),
            TeamMember(user_id="alice", team_id="a"),
            TeamMember(user_id="bob", team_id="a"),
            TeamMember(user_id="eve", team_id="b"),
        ])
        db.commit()
        alice, bob, eve = db.get(User, "alice"), db.get(User, "bob"), db.get(User, "eve")

        general = asyncio.run(list_channels(alice, db))["channels"][0]
        assert general["name"] == "일반"
        design = asyncio.run(create_channel(ChannelCreateRequest(name="설계"), alice, db))["channel"]
        asyncio.run(send_channel_message(design["id"], ChannelMessageRequest(content="팀 A 설계"), alice, db))
        asyncio.run(send_channel_message(design["id"], ChannelMessageRequest(content="검토했어요"), bob, db))
        assert next(c for c in asyncio.run(list_channels(alice, db))["channels"] if c["id"] == design["id"])["unread_count"] == 1
        assert [m["content"] for m in asyncio.run(list_channel_messages(design["id"], alice, db))["messages"]] == ["팀 A 설계", "검토했어요"]
        assert next(c for c in asyncio.run(list_channels(alice, db))["channels"] if c["id"] == design["id"])["unread_count"] == 0
        assert [c["name"] for c in asyncio.run(list_channels(eve, db))["channels"]] == ["일반"]
        with pytest.raises(HTTPException) as denied:
            asyncio.run(list_channel_messages(design["id"], eve, db))
        assert denied.value.status_code == 404
    engine.dispose()


def test_direct_thread_requires_same_team_membership_and_excludes_other_teams():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add_all([
            Team(id="a", name="A"), Team(id="b", name="B"),
            User(id="alice", team_id="a", name="Alice", email="alice@test.invalid"),
            User(id="bob", team_id="a", name="Bob", email="bob@test.invalid"),
            User(id="eve", team_id="b", name="Eve", email="eve@test.invalid"),
            TeamMember(user_id="alice", team_id="a"),
            TeamMember(user_id="bob", team_id="a"),
            TeamMember(user_id="eve", team_id="b"),
        ])
        db.commit()
        alice, bob, eve = (db.get(User, user_id) for user_id in ("alice", "bob", "eve"))

        asyncio.run(send_message(SendMessageRequest(receiver_id="bob", content="안녕"), alice, db))
        assert [m["content"] for m in asyncio.run(get_thread("bob", alice, db))["messages"]] == ["안녕"]
        assert [m["content"] for m in asyncio.run(get_thread("alice", bob, db))["messages"]] == ["안녕"]
        with pytest.raises(HTTPException) as denied:
            asyncio.run(get_thread("alice", eve, db))
        assert denied.value.status_code == 404
        with pytest.raises(HTTPException) as denied:
            asyncio.run(send_message(SendMessageRequest(receiver_id="eve", content="안녕"), alice, db))
        assert denied.value.status_code == 404
    engine.dispose()


def test_chat_http_flow_for_channel_and_direct_message():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)
    with session() as db:
        db.add_all([
            Team(id="a", name="A"),
            User(id="alice", team_id="a", name="Alice", email="alice@http.invalid"),
            User(id="bob", team_id="a", name="Bob", email="bob@http.invalid"),
            TeamMember(user_id="alice", team_id="a"),
            TeamMember(user_id="bob", team_id="a"),
        ])
        db.commit()

    active_user = {"id": "alice"}
    app = FastAPI()
    app.include_router(messages_router.router)

    def db_override():
        with session() as db:
            yield db

    def user_override():
        with session() as db:
            return db.get(User, active_user["id"])

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    client = TestClient(app)
    try:
        general = client.get("/messages/channels").json()["channels"][0]
        assert general["name"] == "일반"
        assert client.post(f"/messages/channels/{general['id']}/messages", json={"content": "  모두 안녕  "}).status_code == 201
        active_user["id"] = "bob"
        assert client.get("/messages/channels").json()["channels"][0]["unread_count"] == 1
        assert client.get(f"/messages/channels/{general['id']}/messages").json()["messages"][0]["content"] == "모두 안녕"
        assert client.get("/messages/channels").json()["channels"][0]["unread_count"] == 0
        assert client.post("/messages", json={"receiver_id": "alice", "content": "개인 대화"}).status_code == 200
        active_user["id"] = "alice"
        assert client.get("/messages/threads/bob").json()["messages"][0]["content"] == "개인 대화"
        assert client.get("/messages/unread-count").json()["unread_count"] == 0
    finally:
        client.close()
        engine.dispose()
