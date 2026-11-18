"""
공유 DB (shared.db) 모델 — NAVIGATOR-SERVER가 소유하는 테이블.

User, Team, Subscription, PublishedSnapshot 모두 SharedBase를 사용하므로
shared_engine에 바인딩됩니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, ForeignKey, DateTime, Text, Integer, Boolean,
    CheckConstraint, Index, UniqueConstraint, Float, text,
)
from sqlalchemy import inspect
from sqlalchemy.orm import relationship

from auth.database import SharedBase


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Team(SharedBase):
    __tablename__ = "teams"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    name = Column(String(255), nullable=False)
    github_repo = Column(String(500), nullable=True)
    github_token = Column(String(500), nullable=True)
    github_client_id = Column(String(255), nullable=True)
    github_client_secret = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="team", cascade="all, delete-orphan")
    subscription = relationship("Subscription", back_populates="team", uselist=False)


class User(SharedBase):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    role = Column(
        String(20),
        CheckConstraint("role IN ('pm', 'software_engineer', 'backend', 'frontend', 'devops')"),
        nullable=False,
        default="software_engineer",
    )
    github_username = Column(String(255), nullable=True)
    github_id = Column(String(64), unique=True, nullable=True)
    github_login = Column(String(255), nullable=True)
    github_oauth_token = Column(String(500), nullable=True)
    password_hash = Column(String(255), nullable=False)
    signup_method = Column(String(16), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    team = relationship("Team", back_populates="users")

class TeamMember(SharedBase):
    """N:M 다중 팀 소속 관계 테이블."""
    __tablename__ = "team_members"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    role = Column(
        String(20),
        CheckConstraint("role IN ('pm', 'software_engineer', 'backend', 'frontend', 'devops')"),
        nullable=False,
        default="software_engineer",
    )
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # 동일한 사용자가 같은 팀에 중복 소속되지 않도록 제한
        UniqueConstraint("user_id", "team_id", name="uq_user_team"),
    )

    user = relationship("User")
    team = relationship("Team")


class Subscription(SharedBase):
    __tablename__ = "subscriptions"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"), unique=True, nullable=False)
    plan = Column(
        String(20),
        CheckConstraint("plan IN ('free', 'pro', 'enterprise')"),
        nullable=False,
        default="free",
    )
    status = Column(
        String(20),
        CheckConstraint("status IN ('active', 'canceled', 'past_due', 'trialing')"),
        nullable=False,
        default="active",
    )
    stripe_customer_id = Column(String(255), nullable=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    team = relationship("Team", back_populates="subscription")


class PublishedSnapshot(SharedBase):
    """팀 산출물 공유 단위."""
    __tablename__ = "published_snapshots"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    run_id = Column(String(255), nullable=True)
    team_id = Column(String(36), nullable=True)
    published_by = Column(String(36), nullable=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    snapshot_data = Column(Text, nullable=False)
    published_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_published_snapshots_team_id", "team_id"),
    )

class TeamInvite(SharedBase):
    __tablename__ = "team_invites"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    code = Column(String(100), unique=True, index=True, nullable=False)
    creator_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False, default="engineer")
    max_uses = Column(Integer, nullable=False, default=1)
    used_count = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    team = relationship("Team")
    creator = relationship("User")

class DevPrAnalysis(SharedBase):
    # author: xxrin
    # Dev Tracking 영속화 테스트와 PR 분석 이력 저장을 위해 추가한 모델입니다.
    __tablename__ = "dev_pr_analyses"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    project_id = Column(String(255), nullable=True, default="")
    team_id = Column(String(36), nullable=True)
    owner = Column(String(255), nullable=False, default="")
    repo = Column(String(255), nullable=False, default="")
    pr_number = Column(Integer, nullable=False, default=0)
    branch_name = Column(String(255), nullable=False, default="")
    base_branch = Column(String(255), nullable=True, default="")
    head_sha = Column(String(255), nullable=True, default="")
    branch_created_at = Column(String(64), nullable=True, default="")
    source_dir = Column(Text, nullable=True, default="")
    spec_snapshot_id = Column(String(36), nullable=True, default="")
    spec_outdated = Column(Boolean, nullable=False, default=False)
    approval_status = Column(String(64), nullable=True, default="")
    analysis_status = Column(String(64), nullable=True, default="")
    task_id = Column(String(64), nullable=True, default="")
    pm_report = Column(Text, nullable=True, default="{}")
    timeline = Column(Text, nullable=True, default="[]")
    gap_count = Column(Integer, nullable=False, default=0)
    has_high_gap = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    gap_items = relationship("DevGapItem", back_populates="analysis", cascade="all, delete-orphan")


class DevGapItem(SharedBase):
    # author: xxrin
    # DevPrAnalysis 레코드와 연결된 GAP 분류 항목을 저장하기 위해 추가했습니다.
    __tablename__ = "dev_gap_items"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    analysis_id = Column(String(36), ForeignKey("dev_pr_analyses.id", ondelete="CASCADE"), nullable=False)
    gap_id = Column(String(128), nullable=False, default="")
    severity = Column(String(16), nullable=True, default="")
    type = Column(String(64), nullable=True, default="")
    spec_target = Column(Text, nullable=True, default="")
    implementation_target = Column(Text, nullable=True, default="")
    spec_outdated_related = Column(Boolean, nullable=False, default=False)
    intent = Column(String(32), nullable=True, default="")
    confidence = Column(Float, nullable=True)
    recommended_action = Column(String(64), nullable=True, default="")
    approval_status = Column(String(64), nullable=True, default="PENDING_PM_APPROVAL")
    approved_by = Column(String(36), nullable=True, default="")
    approved_at = Column(String(64), nullable=True, default="")
    description = Column(Text, nullable=True, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    analysis = relationship("DevPrAnalysis", back_populates="gap_items")


def ensure_dev_tracking_schema(bind_or_session) -> None:
    # author: xxrin
    # 기존 SQLite 파일에도 Dev Tracking 문서 기준 추가 컬럼이 생기도록 create_all 이후 보강 마이그레이션을 수행한다.
    bind = bind_or_session.get_bind() if hasattr(bind_or_session, "get_bind") else bind_or_session
    SharedBase.metadata.create_all(bind=bind, tables=[DevPrAnalysis.__table__, DevGapItem.__table__])
    additions = {
        "dev_pr_analyses": {
            "project_id": "VARCHAR(255) DEFAULT ''",
            "branch_created_at": "VARCHAR(64) DEFAULT ''",
            "spec_outdated": "BOOLEAN DEFAULT 0",
            "gap_count": "INTEGER DEFAULT 0",
            "has_high_gap": "BOOLEAN DEFAULT 0",
            "updated_at": "DATETIME",
        },
        "dev_gap_items": {
            "spec_outdated_related": "BOOLEAN DEFAULT 0",
            "confidence": "FLOAT",
            "approval_status": "VARCHAR(64) DEFAULT 'PENDING_PM_APPROVAL'",
            "approved_by": "VARCHAR(36) DEFAULT ''",
            "approved_at": "VARCHAR(64) DEFAULT ''",
        },
    }
    with bind.begin() as conn:
        for table_name, columns in additions.items():
            if conn.dialect.name == "sqlite":
                existing = {
                    row[1]
                    for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
                }
            else:
                existing = {col["name"] for col in inspect(conn).get_columns(table_name)}
            for column_name, column_sql in columns.items():
                if column_name not in existing:
                    if conn.dialect.name != "sqlite":
                        column_sql = column_sql.replace("BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE")
                        column_sql = column_sql.replace("DATETIME", "TIMESTAMP")
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"))


class DirectMessage(SharedBase):
    """팀 내 앱 내 쪽지 — followup 질문 컨텍스트와 함께 담당자에게 전송."""
    __tablename__ = "direct_messages"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    team_id = Column(String(36), nullable=True)
    sender_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    sender_name = Column(String(255), nullable=False, default="")
    receiver_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    receiver_name = Column(String(255), nullable=False, default="")
    content = Column(Text, nullable=False)
    context_question = Column(Text, nullable=True, default="")  # followup 원문
    context_domain = Column(String(20), nullable=True, default="general")
    is_read = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    sender = relationship("User", foreign_keys=[sender_id])
    receiver = relationship("User", foreign_keys=[receiver_id])

    __table_args__ = (
        Index("ix_dm_receiver_read", "receiver_id", "is_read"),
        Index("ix_dm_team_created", "team_id", "created_at"),
    )


class DevKnowledgeArtifact(SharedBase):
    # author: xxrin
    # PM/SA 컨텍스트 로딩에 사용하는 검색 가능한 Dev Tracking 아티팩트 저장용 모델입니다.
    __tablename__ = "dev_knowledge_artifacts"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    team_id = Column(String(36), nullable=True, default="")
    artifact_type = Column(String(64), nullable=False, default="DEV_GAP_REPORT")
    source = Column(String(64), nullable=False, default="dev_tracking")
    owner = Column(String(255), nullable=True, default="")
    repo = Column(String(255), nullable=True, default="")
    pr_number = Column(Integer, nullable=True, default=0)
    branch_name = Column(String(255), nullable=True, default="")
    task_id = Column(String(64), nullable=True, default="")
    decision_status = Column(String(64), nullable=True, default="")
    content_json = Column(Text, nullable=False, default="{}")
    searchable_text = Column(Text, nullable=True, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_dev_knowledge_team_created", "team_id", "created_at"),
        Index("ix_dev_knowledge_repo_pr", "owner", "repo", "pr_number"),
    )
