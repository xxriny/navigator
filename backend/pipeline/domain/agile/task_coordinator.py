"""
Main Agent 태스크 조정기.
PM 승인이 필요한 작업을 큐에 적재하고 상태를 관리한다.
SQLite(auth DB)의 agile_tasks 테이블을 사용한다.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)
from typing import Optional

from sqlalchemy import Column, String, Text, DateTime, create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os
from observability.logger import get_logger

_STORAGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "storage",
)
_DB_PATH = os.path.join(_STORAGE_DIR, "local.db")
_DB_URL = f"sqlite:///{_DB_PATH}"

_engine = create_engine(_DB_URL, connect_args={"check_same_thread": False})
_Session = sessionmaker(bind=_engine)


class _Base(DeclarativeBase):
    pass


class AgileTask(_Base):
    __tablename__ = "agile_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_type = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, default="")
    status = Column(String(32), default="unassigned")  # unassigned | pending_approval | in_progress | pr_pending | completed | rejected
    area = Column(String(32), default="")           # backend | frontend | fullstack | devops
    assignee = Column(String(255), default="")
    feature_ref = Column(String(64), default="")   # RTM FEAT_ID
    effort = Column(String(4), default="")          # S|M|L|XL
    payload = Column(Text, default="{}")  # JSON
    result = Column(Text, default="")
    created_by = Column(String(36), default="")
    reviewed_by = Column(String(36), default="")
    team_id = Column(String(36), default="")
    dev_gap_item_id = Column(String(36), default="")
    pr_number = Column(String(32), default="")
    analysis_id = Column(String(36), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_tasks_db():
    _Base.metadata.create_all(bind=_engine)
    with _engine.connect() as conn:
        for col_def in [
            "area TEXT DEFAULT ''",
            "assignee TEXT DEFAULT ''",
            "team_id TEXT DEFAULT ''",
            "feature_ref TEXT DEFAULT ''",
            "effort TEXT DEFAULT ''",
            "dev_gap_item_id TEXT DEFAULT ''",
            "pr_number TEXT DEFAULT ''",
            "analysis_id TEXT DEFAULT ''",
        ]:
            try:
                # author: xxrin
                # 기존 local.db의 agile_tasks에도 Dev GAP 연결 컬럼을 점진적으로 추가한다.
                conn.execute(text(f"ALTER TABLE agile_tasks ADD COLUMN {col_def}"))
                conn.commit()
            except Exception:
                pass


# ── CRUD ────────────────────────────────────────────────────

def create_task(
    task_type: str,
    title: str,
    description: str = "",
    payload: dict | None = None,
    created_by: str = "",
    area: str = "",
    assignee: str = "",
    team_id: str = "",
    feature_ref: str = "",
    effort: str = "",
    status: str = "unassigned",
    dev_gap_item_id: str = "",
    pr_number: str | int = "",
    analysis_id: str = "",
) -> dict:
    import json
    import re

    def _norm(s: str) -> str:
        return re.sub(r"[\s\-_()（）]", "", s).lower()

    init_tasks_db()
    with _Session() as session:
        q = session.query(AgileTask).filter(AgileTask.team_id == team_id)
        if feature_ref:
            dup = q.filter(AgileTask.feature_ref == feature_ref).first()
        else:
            title_norm = _norm(title)
            dup = q.filter(AgileTask.title == title).first()
            if dup is None:
                # 정규화 제목으로도 체크 (SQLite에서 LOWER 사용)
                from sqlalchemy import func
                dup = session.query(AgileTask).filter(
                    AgileTask.team_id == team_id,
                    func.lower(AgileTask.title) == title.lower(),
                ).first()

        if dup is not None:
            get_logger().info(f"[create_task] 중복 차단 (feature_ref={feature_ref!r}, title={title!r})")
            return None

        task = AgileTask(
            id=str(uuid.uuid4()),
            task_type=task_type,
            title=title,
            description=description,
            status=status,
            area=area,
            assignee=assignee,
            feature_ref=feature_ref,
            effort=effort,
            payload=json.dumps(payload or {}),
            created_by=created_by,
            team_id=team_id,
            dev_gap_item_id=str(dev_gap_item_id or ""),
            pr_number=str(pr_number or ""),
            analysis_id=str(analysis_id or ""),
        )
        session.add(task)
        session.commit()
        return _task_to_dict(task)


def list_tasks(status: str | None = None, team_id: str | None = None) -> list[dict]:
    init_tasks_db()
    with _Session() as session:
        q = session.query(AgileTask)
        if status:
            q = q.filter(AgileTask.status == status)
        if team_id:
            q = q.filter(AgileTask.team_id == team_id)
        tasks = q.order_by(AgileTask.created_at.desc()).all()
        return [_task_to_dict(t) for t in tasks]


def get_task(task_id: str) -> dict | None:
    init_tasks_db()
    with _Session() as session:
        task = session.query(AgileTask).filter(AgileTask.id == task_id).first()
        return _task_to_dict(task) if task else None


def delete_task(task_id: str) -> bool:
    init_tasks_db()
    with _Session() as session:
        task = session.query(AgileTask).filter(AgileTask.id == task_id).first()
        if not task:
            return False
        session.delete(task)
        session.commit()
        return True


def update_task_status(
    task_id: str,
    status: str,
    reviewed_by: str = "",
    result: str = "",
    editable_fields: dict | None = None,
) -> dict | None:
    init_tasks_db()
    with _Session() as session:
        task = session.query(AgileTask).filter(AgileTask.id == task_id).first()
        if not task:
            return None
        task.status = status
        task.updated_at = _now()
        if reviewed_by:
            task.reviewed_by = reviewed_by
        if result:
            task.result = result
        if editable_fields:
            for field in ("title", "description", "area", "effort", "task_type", "assignee"):
                if field in editable_fields:
                    setattr(task, field, editable_fields[field])
        session.commit()
        return _task_to_dict(task)


def _task_to_dict(task: AgileTask) -> dict:
    import json
    return {
        "id": task.id,
        "task_type": task.task_type,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "area": task.area or "",
        "assignee": task.assignee or "",
        "feature_ref": task.feature_ref or "",
        "effort": task.effort or "",
        "payload": json.loads(task.payload or "{}"),
        "result": task.result,
        "created_by": task.created_by,
        "reviewed_by": task.reviewed_by,
        "team_id": task.team_id or "",
        "dev_gap_item_id": task.dev_gap_item_id or "",
        "pr_number": task.pr_number or "",
        "analysis_id": task.analysis_id or "",
        "created_at": task.created_at.isoformat() if task.created_at else "",
        "updated_at": task.updated_at.isoformat() if task.updated_at else "",
    }


def get_existing_feature_refs(team_id: str) -> set[str]:
    """team_id에 속한 태스크의 feature_ref 집합 반환 (중복 생성 방지용)."""
    init_tasks_db()
    with _Session() as session:
        rows = session.query(AgileTask.feature_ref).filter(
            AgileTask.team_id == team_id,
            AgileTask.feature_ref != "",
        ).all()
        return {r[0] for r in rows}


def assign_task(
    task_id: str,
    assignee: str,
    reviewed_by: str = "",
    *,
    team_id: str = "",
    assignee_id: str = "",
) -> dict | None:
    """현재 팀의 미할당 태스크만 실제 팀원에게 배분한다."""
    from auth.database import SharedSessionLocal
    from auth.shared_models import TeamMember, User

    if not team_id or not assignee_id:
        return None
    init_tasks_db()
    with _Session() as session:
        query = session.query(AgileTask).filter(
            AgileTask.id == task_id,
            AgileTask.team_id == team_id,
            AgileTask.status == "unassigned",
        )
        task = query.first()
        if not task:
            return None

        with SharedSessionLocal() as shared:
            member = shared.query(TeamMember.role, User.name).join(
                User, User.id == TeamMember.user_id,
            ).filter(
                TeamMember.team_id == team_id,
                TeamMember.user_id == assignee_id,
            ).first()
        allowed_areas = {
            "software_engineer": {"backend", "frontend", "fullstack", "devops", "sa"},
            "engineer": {"backend", "frontend", "fullstack", "devops", "sa"},
            "backend": {"backend", "fullstack"},
            "frontend": {"frontend", "fullstack"},
            "devops": {"devops"},
        }
        if not member or member.name != assignee or not (
            member.role in allowed_areas
            and (not task.area or task.area in allowed_areas[member.role])
        ):
            return None

        values = {
            "assignee": assignee,
            "status": "pending_approval",
            "updated_at": _now(),
        }
        if reviewed_by:
            values["reviewed_by"] = reviewed_by
        if not query.update(values, synchronize_session=False):
            return None
        session.commit()
        session.refresh(task)
        return _task_to_dict(task)


# ── 자동 실행 ─────────────────────────────────────────────────

def execute_approved_task(task: dict) -> str:
    """승인된 태스크를 실행하고 결과를 반환."""
    import json
    task_type = task.get("task_type", "")
    payload = task.get("payload", {})

    try:
        if task_type == "publish_docs":
            from pipeline.domain.agile.wiki_publisher import publish_to_github
            result = publish_to_github(
                result_data=payload.get("result_data", {}),
                owner=payload.get("owner", ""),
                repo=payload.get("repo", ""),
                token=payload.get("token", ""),
                page_title=payload.get("page_title", "SA 설계 문서"),
            )
            return json.dumps(result)

        elif task_type == "verify_sa":
            from pipeline.domain.agile.nodes.verifier import run_verifier
            result = run_verifier(
                sa_data=payload.get("sa_data", {}),
                api_key=payload.get("api_key", ""),
                use_llm=False,
            )
            return json.dumps({"coherence_score": result.coherence_score, "passed": result.passed})

        elif task_type == "import_issues":
            return json.dumps({"imported": len(payload.get("issues", []))})

        elif task_type in ("feature", "bugfix", "refactor", "infra", "doc_sync"):
            return json.dumps({
                "message": f"'{task_type}' 태스크가 승인되었습니다. 담당자에게 알림이 전송됩니다.",
                "area": task.get("area", ""),
                "assignee": task.get("assignee", ""),
            })

        else:
            return json.dumps({"message": f"태스크 타입 '{task_type}' 실행됨"})

    except Exception as e:
        return json.dumps({"error": str(e) or repr(e) or f"{type(e).__name__} (메시지 없음)"})
