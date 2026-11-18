"""
/keys/* 라우터 — 서버 관리 Gemini API 키.

배포 시 클라이언트는 자체 키 없이 서버 키를 통해 Gemini를 사용합니다.
- /keys/status       : 서버 키 유무 확인
- /keys/register     : 팀/전역 키 등록 (PM 전용)
- /keys/list         : 등록된 키 목록 (마스킹)
- /keys/activate     : 키 활성화/비활성화
- /keys/proxy        : 서버 키로 Gemini 요청 프록시 (배포 후 사용)
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import GeminiApiKey
from schemas import KeyRegisterRequest, KeyResponse
from routers.auth import get_current_user
from models import User

router = APIRouter(prefix="/keys", tags=["api-keys"])


# ── 헬퍼 ─────────────────────────────────────────────────────

def _mask(key: str) -> str:
    """API 키 마스킹 (앞 8자 + *****)."""
    return key[:8] + "*" * max(0, len(key) - 8) if len(key) > 8 else "****"


def _require_team_pm(user: User, team_id: Optional[str]) -> None:
    if user.role != "pm" or not team_id or team_id != user.team_id:
        raise HTTPException(status_code=403, detail="현재 팀 PM 권한이 필요합니다.")


PROXY_KEY_MAX_USES = 10000


def get_active_key(db: Session, team_id: Optional[str] = None) -> Optional[str]:
    """
    팀 키 → 전역 서버 키 → 환경변수 순서로 활성 Gemini 키 반환.
    NAVIGATOR 백엔드에서 호출하거나 프록시 엔드포인트에서 내부 사용.
    """
    # 1. 팀 전용 키
    if team_id:
        key_row = (
            db.query(GeminiApiKey)
            .filter(GeminiApiKey.team_id == team_id, GeminiApiKey.is_active == True)
            .order_by(GeminiApiKey.usage_count.asc())
            .first()
        )
        if key_row:
            key_row.usage_count += 1
            key_row.last_used_at = datetime.utcnow()
            db.commit()
            return key_row.api_key

    # 2. 전역 서버 키 (DB)
    global_row = (
        db.query(GeminiApiKey)
        .filter(GeminiApiKey.team_id == None, GeminiApiKey.is_active == True)  # noqa: E711
        .order_by(GeminiApiKey.usage_count.asc())
        .first()
    )
    if global_row:
        global_row.usage_count += 1
        global_row.last_used_at = datetime.utcnow()
        db.commit()
        return global_row.api_key

    # 3. 환경변수 폴백
    return os.environ.get("GEMINI_API_KEY", "") or None


# ── 엔드포인트 ───────────────────────────────────────────────

@router.get("/status")
async def key_status(db: Session = Depends(get_db)):
    """서버에 활성 Gemini 키가 있는지 확인."""
    env_key = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    db_key  = db.query(GeminiApiKey).filter(GeminiApiKey.is_active == True).count() > 0  # noqa: E712
    return {
        "has_server_key": env_key or db_key,
        "env_key_set": env_key,
        "db_key_count": db.query(GeminiApiKey).filter(GeminiApiKey.is_active == True).count(),  # noqa: E712
    }


@router.post("/register", response_model=KeyResponse)
async def register_key(
    req: KeyRegisterRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Gemini API 키 등록. 전역 키(team_id=None)는 PM 전용."""
    _require_team_pm(user, req.team_id)

    row = GeminiApiKey(
        team_id=req.team_id,
        label=req.label,
        api_key=req.api_key,
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return KeyResponse(
        id=row.id,
        label=row.label,
        team_id=row.team_id,
        is_active=row.is_active,
        usage_count=row.usage_count,
    )


@router.get("/list")
async def list_keys(
    team_id: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """등록된 키 목록 (API 키는 마스킹)."""
    _require_team_pm(user, team_id or user.team_id)
    rows = db.query(GeminiApiKey).filter(GeminiApiKey.team_id == user.team_id).all()
    return {"keys": [
        {
            "id": r.id,
            "label": r.label,
            "team_id": r.team_id,
            "key_preview": _mask(r.api_key),
            "is_active": r.is_active,
            "usage_count": r.usage_count,
            "last_used_at": r.last_used_at,
        }
        for r in rows
    ]}


@router.patch("/{key_id}/activate")
async def toggle_key(
    key_id: str,
    active: bool,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(GeminiApiKey).filter(GeminiApiKey.id == key_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="키를 찾을 수 없습니다.")
    _require_team_pm(user, row.team_id)
    row.is_active = active
    db.commit()
    return {"status": "ok", "is_active": active}


@router.delete("/{key_id}")
async def delete_key(
    key_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(GeminiApiKey).filter(GeminiApiKey.id == key_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="키를 찾을 수 없습니다.")
    _require_team_pm(user, row.team_id)
    db.delete(row)
    db.commit()
    return {"status": "ok"}


@router.get("/active")
async def get_active_key_endpoint(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """로컬 백엔드가 JWT로 팀의 활성 Gemini 키를 가져오는 엔드포인트."""
    _require_team_pm(user, user.team_id)
    row = db.query(GeminiApiKey).filter(
        GeminiApiKey.team_id == user.team_id, GeminiApiKey.is_active == True
    ).first()
    if not row:
        raise HTTPException(status_code=503, detail="현재 팀의 활성 Gemini 키가 없습니다.")
    return {"api_key": row.api_key}


@router.post("/proxy")
async def proxy_gemini(
    request: dict,
    team_id: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    서버 키로 Gemini API 요청을 프록시.

    클라이언트 → NAVIGATOR-SERVER /keys/proxy → Gemini API

    배포 환경에서 클라이언트가 Gemini API 키를 직접 갖지 않아도 되는 구조.
    request 바디는 Gemini generateContent API 형식 그대로 전달합니다.
    """
    import httpx

    if not user.team_id or (team_id and team_id != user.team_id):
        raise HTTPException(status_code=403, detail="현재 팀 키만 사용할 수 있습니다.")
    row = db.query(GeminiApiKey).filter(
        GeminiApiKey.team_id == user.team_id, GeminiApiKey.is_active == True
    ).order_by(GeminiApiKey.usage_count.asc()).first()
    if not row:
        raise HTTPException(status_code=503, detail="현재 팀의 활성 키가 없습니다.")
    claimed = db.query(GeminiApiKey).filter(
        GeminiApiKey.id == row.id,
        GeminiApiKey.is_active == True,
        GeminiApiKey.usage_count < PROXY_KEY_MAX_USES,
    ).update({
        GeminiApiKey.usage_count: GeminiApiKey.usage_count + 1,
        GeminiApiKey.last_used_at: datetime.utcnow(),
    }, synchronize_session=False)
    if not claimed:
        raise HTTPException(status_code=429, detail="Gemini 키 사용 한도를 초과했습니다.")
    db.commit()
    api_key = row.api_key

    model = request.get("model", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(url, json=request.get("body", request))
        return resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Gemini API 응답 시간 초과")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini 프록시 오류: {e}")
