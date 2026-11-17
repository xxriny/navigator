"""
/messages/* 라우터 — 팀 내 앱 내 쪽지 (followup 컨텍스트 포함).

Endpoints:
  POST   /messages                   — 쪽지 전송
  GET    /messages/inbox             — 수신함
  GET    /messages/sent              — 발신함
  GET    /messages/unread-count      — 미읽 건수 (30초 폴링용)
  PATCH  /messages/{id}/read         — 읽음 처리
"""

from __future__ import annotations

import uuid
import json
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models import DirectMessage, PublishedSnapshot, TeamChannel, TeamChannelMessage, TeamChannelRead, TeamMember, User
from routers.auth import get_current_user

router = APIRouter(prefix="/messages", tags=["messages"])


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _team_id(current_user: User, db: Session) -> str:
    team_id = current_user.team_id
    if not team_id or not db.query(TeamMember).filter(
        TeamMember.user_id == current_user.id, TeamMember.team_id == team_id,
    ).first():
        raise HTTPException(status_code=403, detail="현재 팀에 접근 권한이 없습니다.")
    return team_id


def _content(value: str) -> str:
    content = value.strip()
    if not content:
        raise HTTPException(status_code=400, detail="메시지를 입력해 주세요.")
    return content


_SHARE_MARKER = re.compile(r"\[\[navigator-share:(components|apis):([A-Za-z0-9-]{1,64})\]\]")


def _validate_artifact_share(content: str, team_id: str, db: Session) -> None:
    if not content.startswith("[[navigator-share:"):
        return
    match = _SHARE_MARKER.fullmatch(content)
    if not match:
        raise HTTPException(status_code=400, detail="공유 카드 형식이 올바르지 않습니다.")
    kind, snapshot_id = match.groups()
    snapshot = db.query(PublishedSnapshot).filter(
        PublishedSnapshot.id == snapshot_id, PublishedSnapshot.team_id == team_id,
    ).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="공유할 결과를 찾을 수 없습니다.")
    try:
        data = json.loads(snapshot.snapshot_data)
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict) or data.get("artifact_kind") != kind or not isinstance(data.get(kind), list):
        raise HTTPException(status_code=404, detail="공유할 결과를 찾을 수 없습니다.")


def _to_out(msg: DirectMessage) -> dict:
    return {
        "id": msg.id,
        "sender_id": msg.sender_id,
        "sender_name": msg.sender_name,
        "receiver_id": msg.receiver_id,
        "receiver_name": msg.receiver_name,
        "content": msg.content,
        "context_question": msg.context_question or "",
        "context_domain": msg.context_domain or "general",
        "is_read": bool(msg.is_read),
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
    }


class SendMessageRequest(BaseModel):
    receiver_id: str
    content: str = Field(max_length=5000)
    context_question: Optional[str] = ""
    context_domain: Optional[str] = "general"


class ChannelCreateRequest(BaseModel):
    name: str = Field(max_length=50)


class ChannelMessageRequest(BaseModel):
    content: str = Field(max_length=5000)


def _channel_out(channel: TeamChannel, unread_count: int = 0) -> dict:
    return {"id": channel.id, "name": channel.name, "unread_count": unread_count}


def _channel_message_out(msg: TeamChannelMessage) -> dict:
    return {
        "id": msg.id,
        "sender_id": msg.sender_id,
        "sender_name": msg.sender_name,
        "content": msg.content,
        "created_at": msg.created_at.isoformat(),
    }


@router.get("/channels")
async def list_channels(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    team_id = _team_id(current_user, db)
    general = db.query(TeamChannel).filter(TeamChannel.team_id == team_id, TeamChannel.name == "일반").first()
    if not general:
        db.add(TeamChannel(team_id=team_id, name="일반"))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    channels = db.query(TeamChannel).filter(TeamChannel.team_id == team_id).order_by(TeamChannel.created_at, TeamChannel.id).all()
    channels.sort(key=lambda channel: channel.name != "일반")
    result = []
    for channel in channels:
        last_read = db.query(TeamChannelRead).filter(
            TeamChannelRead.channel_id == channel.id, TeamChannelRead.user_id == current_user.id,
        ).first()
        unread = db.query(TeamChannelMessage).filter(
            TeamChannelMessage.team_id == team_id,
            TeamChannelMessage.channel_id == channel.id,
            TeamChannelMessage.sender_id != current_user.id,
        )
        if last_read:
            unread = unread.filter(TeamChannelMessage.created_at > last_read.last_read_at)
        result.append(_channel_out(channel, unread.count()))
    return {"channels": result}


@router.post("/channels", status_code=201)
async def create_channel(
    req: ChannelCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team_id = _team_id(current_user, db)
    name = req.name.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="채널 이름을 입력해 주세요.")
    channel = TeamChannel(team_id=team_id, name=name)
    db.add(channel)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="같은 이름의 채널이 이미 있습니다.")
    db.refresh(channel)
    return {"channel": _channel_out(channel)}


def _channel_for_team(channel_id: str, team_id: str, db: Session) -> TeamChannel:
    channel = db.query(TeamChannel).filter(TeamChannel.id == channel_id, TeamChannel.team_id == team_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="채널을 찾을 수 없습니다.")
    return channel


@router.get("/channels/{channel_id}/messages")
async def list_channel_messages(
    channel_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team_id = _team_id(current_user, db)
    _channel_for_team(channel_id, team_id, db)
    messages = list(reversed(db.query(TeamChannelMessage).filter(
        TeamChannelMessage.channel_id == channel_id, TeamChannelMessage.team_id == team_id,
    ).order_by(TeamChannelMessage.created_at.desc(), TeamChannelMessage.id.desc()).limit(100).all()))
    if messages:
        read = db.query(TeamChannelRead).filter(
            TeamChannelRead.channel_id == channel_id, TeamChannelRead.user_id == current_user.id,
        ).first()
        latest = messages[-1].created_at
        if not read:
            db.add(TeamChannelRead(channel_id=channel_id, user_id=current_user.id, last_read_at=latest))
            db.commit()
        elif read.last_read_at < latest:
            read.last_read_at = latest
            db.commit()
    return {"messages": [_channel_message_out(message) for message in messages]}


@router.post("/channels/{channel_id}/messages", status_code=201)
async def send_channel_message(
    channel_id: str,
    req: ChannelMessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team_id = _team_id(current_user, db)
    _channel_for_team(channel_id, team_id, db)
    content = _content(req.content)
    _validate_artifact_share(content, team_id, db)
    message = TeamChannelMessage(
        team_id=team_id, channel_id=channel_id, sender_id=current_user.id,
        sender_name=current_user.name or current_user.email, content=content,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return {"message": _channel_message_out(message)}


@router.get("/threads/{peer_id}")
async def get_thread(
    peer_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team_id = _team_id(current_user, db)
    if not db.query(TeamMember).filter(TeamMember.team_id == team_id, TeamMember.user_id == peer_id).first():
        raise HTTPException(status_code=404, detail="팀원을 찾을 수 없습니다.")
    messages = list(reversed(db.query(DirectMessage).filter(
        DirectMessage.team_id == team_id,
        or_(
            and_(DirectMessage.sender_id == current_user.id, DirectMessage.receiver_id == peer_id),
            and_(DirectMessage.sender_id == peer_id, DirectMessage.receiver_id == current_user.id),
        ),
    ).order_by(DirectMessage.created_at.desc(), DirectMessage.id.desc()).limit(100).all()))
    changed = False
    for message in messages:
        if message.receiver_id == current_user.id and not message.is_read:
            message.is_read = True
            changed = True
    if changed:
        db.commit()
    return {"messages": [_to_out(message) for message in messages]}


@router.post("")
async def send_message(
    req: SendMessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team_id = _team_id(current_user, db)
    receiver = db.query(User).filter(User.id == req.receiver_id).first()
    if not receiver or not db.query(TeamMember).filter(
        TeamMember.team_id == team_id, TeamMember.user_id == req.receiver_id,
    ).first():
        raise HTTPException(status_code=404, detail="수신자를 찾을 수 없습니다.")
    if receiver.id == current_user.id:
        raise HTTPException(status_code=400, detail="본인에게 메시지를 보낼 수 없습니다.")

    content = _content(req.content)
    _validate_artifact_share(content, team_id, db)

    msg = DirectMessage(
        id=_new_uuid(),
        team_id=team_id,
        sender_id=current_user.id,
        sender_name=current_user.name or current_user.email,
        receiver_id=receiver.id,
        receiver_name=receiver.name or receiver.email,
        content=content,
        context_question=req.context_question or "",
        context_domain=req.context_domain or "general",
        is_read=False,
        created_at=datetime.utcnow(),
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"ok": True, "message": _to_out(msg)}


@router.get("/inbox")
async def get_inbox(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team_id = _team_id(current_user, db)
    msgs = (
        db.query(DirectMessage)
        .filter(DirectMessage.receiver_id == current_user.id, DirectMessage.team_id == team_id)
        .order_by(DirectMessage.created_at.desc())
        .limit(100)
        .all()
    )
    return {"messages": [_to_out(m) for m in msgs]}


@router.get("/sent")
async def get_sent(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team_id = _team_id(current_user, db)
    msgs = (
        db.query(DirectMessage)
        .filter(DirectMessage.sender_id == current_user.id, DirectMessage.team_id == team_id)
        .order_by(DirectMessage.created_at.desc())
        .limit(100)
        .all()
    )
    return {"messages": [_to_out(m) for m in msgs]}


@router.get("/unread-count")
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team_id = _team_id(current_user, db)
    count = (
        db.query(DirectMessage)
        .filter(
            DirectMessage.receiver_id == current_user.id,
            DirectMessage.team_id == team_id,
            DirectMessage.is_read == False,  # noqa: E712
        )
        .count()
    )
    return {"unread_count": count}


@router.patch("/{message_id}/read")
async def mark_read(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team_id = _team_id(current_user, db)
    msg = db.query(DirectMessage).filter(DirectMessage.id == message_id, DirectMessage.team_id == team_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="쪽지를 찾을 수 없습니다.")
    if msg.receiver_id != current_user.id:
        raise HTTPException(status_code=403, detail="본인 수신 쪽지만 읽음 처리할 수 있습니다.")
    msg.is_read = True
    db.commit()
    return {"ok": True}
