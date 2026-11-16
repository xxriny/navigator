"""
NAVIGATOR-SERVER ORM 모델 — shared.db 전용.

테이블 목록:
  users               — 전체 사용자 (GitHub/이메일 통합)
  teams               — 팀
  subscriptions       — 팀별 구독 플랜 (free / pro / enterprise)
  published_snapshots — 팀 공유 SA 스냅샷
  gemini_api_keys     — 서버 관리 Gemini API 키
  direct_messages     — 팀 내 앱 내 쪽지
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime,
    ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ── Team ────────────────────────────────────────────────────

class Team(Base):
    __tablename__ = "teams"

    id                  = Column(String(36),  primary_key=True, default=_new_uuid)
    name                = Column(String(255), nullable=False)
    github_repo         = Column(String(500), nullable=True)
    github_token        = Column(String(500), nullable=True)
    github_client_id    = Column(String(255), nullable=True)
    github_client_secret= Column(String(255), nullable=True)
    created_at          = Column(DateTime,    default=datetime.utcnow)

    users        = relationship("User",         back_populates="team", cascade="all, delete-orphan")
    subscription = relationship("Subscription", back_populates="team", uselist=False, cascade="all, delete-orphan")


# ── User ────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id                 = Column(String(36),  primary_key=True, default=_new_uuid)
    team_id            = Column(String(36),  ForeignKey("teams.id", ondelete="CASCADE"), nullable=True)
    name               = Column(String(255), nullable=False)
    email              = Column(String(255), unique=True, nullable=False)
    role               = Column(
        String(20),
        CheckConstraint("role IN ('pm','engineer','backend','frontend','devops','software_engineer','qa')"),
        nullable=False,
        default="engineer",
    )
    github_username    = Column(String(255), nullable=True)
    github_id          = Column(String(64),  unique=True, nullable=True)
    github_login       = Column(String(255), nullable=True)
    github_oauth_token = Column(String(500), nullable=True)
    password_hash      = Column(String(255), nullable=False, default="")
    created_at         = Column(DateTime,    default=datetime.utcnow)

    team = relationship("Team", back_populates="users")

    __table_args__ = (
        Index("ix_users_email", "email"),
        Index("ix_users_github_id", "github_id"),
    )


# ── Subscription ────────────────────────────────────────────

class Subscription(Base):
    """팀별 구독 플랜. 팀 생성 시 free로 자동 생성."""
    __tablename__ = "subscriptions"

    id                     = Column(String(36), primary_key=True, default=_new_uuid)
    team_id                = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, unique=True)
    plan                   = Column(
        String(20),
        CheckConstraint("plan IN ('free','pro','enterprise')"),
        nullable=False,
        default="free",
    )
    status                 = Column(
        String(20),
        CheckConstraint("status IN ('active','canceled','past_due','trialing')"),
        nullable=False,
        default="active",
    )
    stripe_customer_id     = Column(String(255), nullable=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    current_period_end     = Column(DateTime,    nullable=True)
    created_at             = Column(DateTime,    default=datetime.utcnow)
    updated_at             = Column(DateTime,    default=datetime.utcnow, onupdate=datetime.utcnow)

    team = relationship("Team", back_populates="subscription")


# ── PublishedSnapshot ────────────────────────────────────────

class PublishedSnapshot(Base):
    """팀 공유 SA 스냅샷 (NAVIGATOR local.db → shared.db로 publish)."""
    __tablename__ = "published_snapshots"

    id            = Column(String(36),  primary_key=True, default=_new_uuid)
    run_id        = Column(String(255), nullable=True)
    team_id       = Column(String(36),  nullable=True)
    published_by  = Column(String(36),  nullable=True)
    title         = Column(String(500), nullable=False)
    description   = Column(Text,        nullable=True)
    version       = Column(Integer,     nullable=False, default=1)
    snapshot_data = Column(Text,        nullable=False)
    published_at  = Column(DateTime,    default=datetime.utcnow)

    __table_args__ = (
        Index("ix_published_snapshots_team_id", "team_id"),
    )


# ── GeminiApiKey ─────────────────────────────────────────────

class GeminiApiKey(Base):
    """서버 관리 Gemini API 키. team_id=NULL이면 전역 서버 키."""
    __tablename__ = "gemini_api_keys"

    id            = Column(String(36),  primary_key=True, default=_new_uuid)
    team_id       = Column(String(36),  nullable=True)   # NULL = 서버 전역 키
    label         = Column(String(255), nullable=True)
    api_key       = Column(Text,        nullable=False)   # 배포 시 암호화 권장
    is_active     = Column(Boolean,     default=True)
    usage_count   = Column(Integer,     default=0)
    last_used_at  = Column(DateTime,    nullable=True)
    created_at    = Column(DateTime,    default=datetime.utcnow)

    __table_args__ = (
        Index("ix_gemini_api_keys_team_id", "team_id"),
    )


# ── TeamMember (N:M) ─────────────────────────────────────────

class TeamMember(Base):
    """사용자-팀 다중 소속 관계."""
    __tablename__ = "team_members"

    id      = Column(String(36), primary_key=True, default=_new_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    role    = Column(String(20), nullable=False, default="engineer")

    __table_args__ = (
        Index("ix_team_members_user_id", "user_id"),
        Index("ix_team_members_team_id", "team_id"),
    )


# ── TeamInvite ─────────────────────────────────────────────

class TeamInvite(Base):
    """팀 초대 링크. PM이 생성하여 팀원을 초대."""
    __tablename__ = "team_invites"

    id         = Column(String(36),  primary_key=True, default=_new_uuid)
    team_id    = Column(String(36),  ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    code       = Column(String(100), unique=True, nullable=False, index=True)
    creator_id = Column(String(36),  ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    role       = Column(
        String(20),
        CheckConstraint("role IN ('pm','engineer','backend','frontend','devops','software_engineer','qa')"),
        nullable=False,
        default="engineer",
    )
    max_uses   = Column(Integer,     nullable=False, default=1)  # 0 = 무제한
    used_count = Column(Integer,     nullable=False, default=0)
    expires_at = Column(DateTime,    nullable=False)
    created_at = Column(DateTime,    default=datetime.utcnow)

    team    = relationship("Team")
    creator = relationship("User")


# ── DirectMessage ───────────────────────────────────────────

class DirectMessage(Base):
    """팀 내 앱 내 쪽지 — followup 컨텍스트 포함."""
    __tablename__ = "direct_messages"

    id               = Column(String(36),  primary_key=True, default=_new_uuid)
    team_id          = Column(String(36),  nullable=True)
    sender_id        = Column(String(36),  ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    sender_name      = Column(String(255), nullable=False, default="")
    receiver_id      = Column(String(36),  ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    receiver_name    = Column(String(255), nullable=False, default="")
    content          = Column(Text,        nullable=False)
    context_question = Column(Text,        nullable=True,  default="")
    context_domain   = Column(String(20),  nullable=True,  default="general")
    is_read          = Column(Boolean,     nullable=False, default=False)
    created_at       = Column(DateTime,    default=datetime.utcnow)

    sender   = relationship("User", foreign_keys=[sender_id])
    receiver = relationship("User", foreign_keys=[receiver_id])

    __table_args__ = (
        Index("ix_dm_receiver_read", "receiver_id", "is_read"),
        Index("ix_dm_team_created",  "team_id",     "created_at"),
    )


class TeamChannel(Base):
    __tablename__ = "team_channels"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("team_id", "name", name="uq_team_channels_team_name"),)


class TeamChannelMessage(Base):
    __tablename__ = "team_channel_messages"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    channel_id = Column(String(36), ForeignKey("team_channels.id", ondelete="CASCADE"), nullable=False)
    sender_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    sender_name = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_team_channel_messages_channel_created", "channel_id", "created_at"),)


class TeamChannelRead(Base):
    __tablename__ = "team_channel_reads"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    channel_id = Column(String(36), ForeignKey("team_channels.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    last_read_at = Column(DateTime, nullable=False)

    __table_args__ = (UniqueConstraint("channel_id", "user_id", name="uq_team_channel_reads_channel_user"),)

