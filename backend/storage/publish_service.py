"""
공유 DB Publish/Snapshot 서비스.
local_db (local.db) ↔ shared_db (shared.db) 분리 운영.
"""
from __future__ import annotations

import json
from typing import Optional
from sqlalchemy.orm import Session

from auth.models import AnalysisResult, AnalysisSession
from auth.shared_models import PublishedSnapshot


# ── 헬퍼 ─────────────────────────────────────────────────────────

def _snap_to_dict(snap: PublishedSnapshot, include_data: bool = False) -> dict:
    d = {
        "id": snap.id,
        "run_id": snap.run_id,
        "team_id": snap.team_id,
        "published_by": snap.published_by,
        "title": snap.title,
        "description": snap.description,
        "version": snap.version,
        "published_at": snap.published_at.isoformat() if snap.published_at else None,
    }
    if include_data:
        try:
            d["data"] = json.loads(snap.snapshot_data) if snap.snapshot_data else None
        except Exception:
            d["data"] = snap.snapshot_data
    return d


# ── 로컬 결과 목록 (publish 모달용) ──────────────────────────────

def list_local_results(local_db: Session, shared_db: Session, limit: int = 50, team_id: str = "") -> list[dict]:
    """Publish 대상 선택용 로컬 AnalysisResult 목록."""
    results = (
        local_db.query(AnalysisResult)
        .join(AnalysisSession, AnalysisSession.run_id == AnalysisResult.run_id)
        .filter(AnalysisSession.team_id == team_id)
        .order_by(AnalysisResult.saved_at.desc())
        .limit(limit)
        .all()
    )

    # 이미 publish된 run_id 집합 (shared DB에서 조회)
    published_ids = {
        row[0]
        for row in shared_db.query(PublishedSnapshot.run_id).filter(
            PublishedSnapshot.run_id.isnot(None)
        ).all()
    }

    out = []
    for r in results:
        session = local_db.query(AnalysisSession).filter(
            AnalysisSession.run_id == r.run_id
        ).first()

        try:
            data = json.loads(r.shaped_result) if r.shaped_result else {}
        except Exception:
            data = {}

        out.append({
            "run_id": r.run_id,
            "title": (session.title if session else None) or r.run_id,
            "saved_at": r.saved_at.isoformat() if r.saved_at else None,
            "is_published": r.run_id in published_ids,
            "data": data,
        })
    return out


# ── Publish ───────────────────────────────────────────────────────

def publish_snapshot(
    local_db: Session,
    shared_db: Session,
    run_id: str,
    title: str,
    description: str = "",
    team_id: Optional[str] = None,
    user_id: Optional[str] = None,
    kind: Optional[str] = None,
) -> dict:
    """로컬 AnalysisResult를 shared.db PublishedSnapshot으로 게시."""
    if not team_id or not user_id:
        raise ValueError("인증된 팀과 게시자가 필요합니다.")
    result = local_db.query(AnalysisResult).join(
        AnalysisSession, AnalysisSession.run_id == AnalysisResult.run_id
    ).filter(AnalysisResult.run_id == run_id, AnalysisSession.team_id == team_id).first()
    if not result:
        raise ValueError(f"현재 팀의 로컬 결과를 찾을 수 없습니다: {run_id}")

    snapshot_data = result.shaped_result
    if kind is not None:
        if kind not in {"components", "apis"}:
            raise ValueError("공유할 수 없는 결과 항목입니다.")
        shaped = json.loads(result.shaped_result)
        if not isinstance(shaped, dict):
            raise ValueError("공유할 결과 형식이 올바르지 않습니다.")
        sa_output = shaped.get("sa_output")
        sa_data = sa_output.get("data") if isinstance(sa_output, dict) else {}
        if not isinstance(sa_data, dict):
            sa_data = {}
        items = shaped.get(kind) or sa_data.get(kind)
        if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
            raise ValueError("공유할 결과가 없습니다.")
        snapshot_data = json.dumps({"artifact_kind": kind, kind: items}, ensure_ascii=False)

    existing_count = (
        shared_db.query(PublishedSnapshot)
        .filter(PublishedSnapshot.team_id == team_id)
        .count()
    ) if team_id else 0

    snap = PublishedSnapshot(
        run_id=run_id,
        team_id=team_id,
        published_by=user_id,
        title=title,
        description=description or "",
        version=existing_count + 1,
        snapshot_data=snapshot_data,
    )
    shared_db.add(snap)
    shared_db.commit()
    shared_db.refresh(snap)
    return _snap_to_dict(snap)


# ── 스냅샷 목록 ──────────────────────────────────────────────────

def list_snapshots(
    shared_db: Session,
    team_id: Optional[str] = None,
    limit: int = 30,
    offset: int = 0,
) -> list[dict]:
    q = shared_db.query(PublishedSnapshot)
    if team_id:
        q = q.filter(PublishedSnapshot.team_id == team_id)
    snaps = q.order_by(PublishedSnapshot.published_at.desc()).offset(offset).limit(limit).all()
    return [_snap_to_dict(s) for s in snaps]


# ── 스냅샷 상세 ──────────────────────────────────────────────────

def get_snapshot(shared_db: Session, snapshot_id: str) -> Optional[dict]:
    snap = shared_db.query(PublishedSnapshot).filter(PublishedSnapshot.id == snapshot_id).first()
    if not snap:
        return None
    return _snap_to_dict(snap, include_data=True)


# ── 스냅샷 삭제 ──────────────────────────────────────────────────

def delete_snapshot(shared_db: Session, snapshot_id: str) -> None:
    snap = shared_db.query(PublishedSnapshot).filter(PublishedSnapshot.id == snapshot_id).first()
    if snap:
        shared_db.delete(snap)
        shared_db.commit()


# ── Pull (스냅샷 → 로컬 덮어쓰기) ───────────────────────────────

def pull_snapshot(shared_db: Session, local_db: Session, snapshot_id: str, run_id: str) -> dict:
    """공유 스냅샷 데이터를 로컬 run_id 세션에 덮어씁니다."""
    snap = shared_db.query(PublishedSnapshot).filter(PublishedSnapshot.id == snapshot_id).first()
    if not snap:
        raise ValueError(f"스냅샷을 찾을 수 없습니다: {snapshot_id}")
    try:
        shaped = json.loads(snap.snapshot_data) if snap.snapshot_data else {}
    except (TypeError, ValueError):
        shaped = {}
    if isinstance(shaped, dict) and shaped.get("artifact_kind") in {"components", "apis"}:
        raise ValueError("부분 공유 결과는 전체 분석에 Pull할 수 없습니다.")

    local = local_db.query(AnalysisResult).filter(AnalysisResult.run_id == run_id).first()
    if local:
        local.shaped_result = snap.snapshot_data
    else:
        local = AnalysisResult(run_id=run_id, shaped_result=snap.snapshot_data)
        local_db.add(local)

    local_db.commit()


    return {
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "shaped_result": shaped,
    }
