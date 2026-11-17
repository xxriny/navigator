"""Artifact cards may reference only a matching snapshot from the sender's team."""

import asyncio
import json

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_chat_rejects_foreign_and_non_artifact_snapshots():
    from database import Base
    from models import PublishedSnapshot, Team, TeamChannel, TeamMember, User
    from routers.messages import ChannelMessageRequest, SendMessageRequest, send_channel_message, send_message

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            Team(id="team-a", name="A"), Team(id="team-b", name="B"),
            User(id="alice", team_id="team-a", name="Alice", email="alice@test.invalid"),
            User(id="bob", team_id="team-a", name="Bob", email="bob@test.invalid"),
            TeamMember(user_id="alice", team_id="team-a"), TeamMember(user_id="bob", team_id="team-a"),
            TeamChannel(id="channel-a", team_id="team-a", name="general"),
            PublishedSnapshot(id="snap-a", team_id="team-a", title="컴포넌트", snapshot_data=json.dumps({
                "artifact_kind": "components", "components": [{"component_name": "Gateway"}],
            })),
            PublishedSnapshot(id="snap-b", team_id="team-b", title="API", snapshot_data=json.dumps({
                "artifact_kind": "apis", "apis": [{"endpoint": "GET /private"}],
            })),
            PublishedSnapshot(id="full", team_id="team-a", title="전체 결과", snapshot_data=json.dumps({
                "components": [{"component_name": "Gateway"}], "secret": "other section",
            })),
        ])
        db.commit()
        alice = db.get(User, "alice")

        ok = asyncio.run(send_channel_message("channel-a", ChannelMessageRequest(
            content="[[navigator-share:components:snap-a]]",
        ), alice, db))
        assert ok["message"]["content"] == "[[navigator-share:components:snap-a]]"
        direct = asyncio.run(send_message(SendMessageRequest(
            receiver_id="bob", content="[[navigator-share:components:snap-a]]",
        ), alice, db))
        assert direct["message"]["content"] == "[[navigator-share:components:snap-a]]"

        for marker in ("[[navigator-share:apis:snap-b]]", "[[navigator-share:components:full]]"):
            try:
                asyncio.run(send_channel_message("channel-a", ChannelMessageRequest(content=marker), alice, db))
            except HTTPException as error:
                assert error.status_code == 404
            else:
                raise AssertionError("invalid artifact card was accepted")

        try:
            asyncio.run(send_message(SendMessageRequest(
                receiver_id="bob", content="[[navigator-share:apis:snap-b]]",
            ), alice, db))
        except HTTPException as error:
            assert error.status_code == 404
        else:
            raise AssertionError("foreign artifact was sent by direct message")
