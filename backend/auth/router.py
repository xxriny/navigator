"""
RBAC 인증 라우터: /auth/*
동적 OAuth 구성 및 팀별 설정을 지원합니다.
"""

from __future__ import annotations

import os
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from auth.database import get_db, get_shared_db
from auth.shared_models import User, Team, Subscription
from auth.models import AnalysisSession, DesignChangeRequest
from auth.schemas import (
    RegisterRequest, LoginRequest,
    ChangeRequestCreate, ChangeRequestUpdate, DevicePollRequest,
)
from auth.service import (
    authenticate_user, create_user, build_user_response,
    create_access_token, count_users, get_user_by_email,
    start_github_device_flow, poll_github_device_token,
    get_github_user_info, create_or_update_github_user,
    exchange_github_code,
)
import secrets
import urllib.parse
from datetime import datetime, timedelta
from fastapi import Request

# ── OAuth 세션 인메모리 스토어 ────────────────────────────────
_oauth_sessions: dict = {}  # session_id → {status, result, created_at}

def _create_oauth_session() -> str:
    sid = secrets.token_urlsafe(32)
    _oauth_sessions[sid] = {"status": "pending", "result": None, "created_at": datetime.utcnow()}
    return sid

def _set_oauth_result(sid: str, result: dict):
    if sid in _oauth_sessions:
        _oauth_sessions[sid]["status"] = "done"
        _oauth_sessions[sid]["result"] = result

def _get_oauth_session(sid: str) -> dict | None:
    s = _oauth_sessions.get(sid)
    if not s:
        return None
    if datetime.utcnow() - s["created_at"] > timedelta(minutes=10):
        _oauth_sessions.pop(sid, None)
        return None
    return s
from auth.deps import get_current_user, require_pm, require_engineer, get_current_user_optional
from auth.oauth_config import get_github_credentials, get_device_flow_client_id

auth_router = APIRouter()


def _get_plan(db: Session, team_id: Optional[str]) -> str:
    if not team_id:
        return "free"
    sub = db.query(Subscription).filter(Subscription.team_id == team_id).first()
    return sub.plan if sub else "free"


# ── 인증 ─────────────────────────────────────────────────────

@auth_router.get("/auth/status")
async def auth_status(db: Session = Depends(get_shared_db)):
    """앱 최초 실행 여부 체크."""
    total = count_users(db)
    return {"has_users": total > 0, "user_count": total}


@auth_router.post("/auth/register")
async def register(req: RegisterRequest, db: Session = Depends(get_shared_db)):
    if get_user_by_email(db, req.email):
        raise HTTPException(status_code=409, detail="이미 등록된 이메일입니다.")
    try:
        user = create_user(
            db,
            name=req.name,
            email=req.email,
            password=req.password,
            role=req.role,
            github_username=req.github_username,
            team_name=req.team_name,
            public_signup=True,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    token = create_access_token(user.id, user.email, user.role)
    plan = _get_plan(db, user.team_id)
    return {"access_token": token, "token_type": "bearer", "user": build_user_response(user, plan)}


@auth_router.post("/auth/login")
async def login(req: LoginRequest, db: Session = Depends(get_shared_db)):
    user = authenticate_user(db, req.email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    token = create_access_token(user.id, user.email, user.role)
    plan = _get_plan(db, user.team_id)
    return {"access_token": token, "token_type": "bearer", "user": build_user_response(user, plan)}


@auth_router.get("/auth/me")
async def me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_shared_db),
):
    plan = _get_plan(db, current_user.team_id)
    return build_user_response(current_user, plan)


# ── GitHub OAuth Web Flow ────────────────────────────────────

@auth_router.get("/auth/github/oauth-url")
async def github_oauth_url(request: Request, db: Session = Depends(get_shared_db)):
    """GitHub OAuth Web Flow 인증 URL + session_id 반환."""
    client_id, _ = get_github_credentials(db)
    if not client_id or "your_" in client_id.lower():
        raise HTTPException(status_code=503, detail="needs_oauth_setup")
    session_id = _create_oauth_session()
    #author: xxrin
    # OAuth authorize와 token 교환 단계에서 동일한 redirect_uri를 사용해야 하므로
    # 서버 callback URL을 단일 기준으로 생성한다.
    redirect_uri = str(request.base_url).rstrip("/") + "/auth/github/callback"
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "user:email read:user repo",
        "state": session_id,
    })
    return {"url": f"https://github.com/login/oauth/authorize?{params}", "session_id": session_id}


@auth_router.get("/auth/github/callback")
async def github_callback(code: str, state: str, request: Request, db: Session = Depends(get_shared_db)):
    """GitHub OAuth 콜백: code → token → 유저 생성 → 세션에 결과 저장 후 완료 HTML 반환."""
    from fastapi.responses import HTMLResponse
    if not _get_oauth_session(state):
        from fastapi.responses import HTMLResponse as HR
        return HR(content="<html><body>유효하지 않거나 만료된 인증 세션입니다.</body></html>", status_code=400)
    client_id, client_secret = get_github_credentials(db)
    try:
        #author: xxrin
        # authorize 단계와 동일한 callback URL을 token 교환에 전달해 redirect_uri mismatch를 방지한다.
        redirect_uri = str(request.base_url).rstrip("/") + "/auth/github/callback"
        token_data = exchange_github_code(client_id, client_secret, code, redirect_uri)
        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError("토큰 발급 실패")
        gh_user = get_github_user_info(access_token)
        user = create_or_update_github_user(
            db,
            github_id=gh_user["id"],
            github_login=gh_user["login"],
            email=gh_user["email"],
            name=gh_user["name"],
            oauth_token=access_token,
        )
        jwt_token = create_access_token(user.id, user.email, user.role)
        plan = _get_plan(db, user.team_id)
        _set_oauth_result(state, {"access_token": jwt_token, "user": build_user_response(user, plan)})
        html = """<html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#0d1117;color:#e6edf3">
<h2>✅ 로그인 완료!</h2><p>NAVIGATOR 앱으로 돌아가세요. 이 창은 닫아도 됩니다.</p>
<script>setTimeout(()=>window.close(),2000)</script></body></html>"""
    except Exception as e:
        _set_oauth_result(state, {"error": str(e)})
        html = f"""<html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#0d1117;color:#f85149">
<h2>❌ 인증 실패</h2><p>{e}</p><p>앱으로 돌아가서 다시 시도하세요.</p></body></html>"""
    return HTMLResponse(content=html)


@auth_router.get("/auth/github/callback-poll/{session_id}")
async def github_callback_poll(session_id: str):
    """프론트엔드가 OAuth 결과를 폴링하는 엔드포인트."""
    s = _get_oauth_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="세션 없음 또는 만료")
    if s["status"] == "pending":
        return {"status": "pending"}
    result = s["result"]
    _oauth_sessions.pop(session_id, None)
    if "error" in result:
        return {"status": "error", "error": result["error"]}
    return {"status": "done", "access_token": result["access_token"], "user": result["user"]}


# ── GitHub OAuth Device Flow (Dynamic) ───────────────────────

@auth_router.post("/auth/github/device-start")
async def github_device_start(db: Session = Depends(get_shared_db)):
    """GitHub Device Flow 시작. NAVIGATOR 기본 Client ID를 사용하므로 사용자 설정 불필요."""
    client_id = get_device_flow_client_id(db)

    if not client_id:
        raise HTTPException(status_code=503, detail="needs_oauth_setup")

    try:
        data = start_github_device_flow(client_id)
        if "error" in data:
            raise HTTPException(status_code=400, detail=data.get("error_description", data["error"]))
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GitHub 인증 시작 실패: {e}")


@auth_router.post("/auth/github/device-poll")
async def github_device_poll(req: DevicePollRequest, db: Session = Depends(get_shared_db)):
    """GitHub Device Flow 폴링 및 로그인 완료. Client Secret 불필요."""
    client_id = get_device_flow_client_id(db)

    if not client_id:
        raise HTTPException(status_code=503, detail="GitHub OAuth 구성 오류")

    try:
        token_data = poll_github_device_token(client_id, req.device_code)
        error = token_data.get("error")
        if error in ("authorization_pending", "slow_down"):
            resp = {"status": "pending", "error": error}
            if error == "slow_down":
                resp["interval"] = token_data.get("interval", 10)
            return resp
        if error:
            return {"status": "error", "error": token_data.get("error_description", error)}

        access_token = token_data.get("access_token")
        if not access_token:
            return {"status": "error", "error": "토큰을 받지 못했습니다."}

        gh_info = get_github_user_info(access_token)
        user = create_or_update_github_user(
            db,
            github_id=gh_info["id"],
            github_login=gh_info["login"],
            email=gh_info["email"],
            name=gh_info["name"],
            oauth_token=access_token,
        )
        jwt_token = create_access_token(user.id, user.email, user.role)
        plan = _get_plan(db, user.team_id)
        return {
            "status": "ok",
            "access_token": jwt_token,
            "token_type": "bearer",
            "user": build_user_response(user, plan),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GitHub 인증 실패: {e}")


@auth_router.post("/auth/github/disconnect")
async def github_disconnect(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_shared_db),
):
    current_user.github_id = None
    current_user.github_login = None
    current_user.github_oauth_token = None
    db.add(current_user)
    db.commit()
    return {"status": "ok"}


@auth_router.get("/auth/github/repos")
async def list_github_repos(current_user: User = Depends(get_current_user)):
    """현재 로그인한 GitHub 사용자의 레포 목록 반환."""
    if not current_user.github_oauth_token:
        raise HTTPException(status_code=400, detail="GitHub 연결이 필요합니다.")
    try:
        import httpx
        headers = {
            "Authorization": f"Bearer {current_user.github_oauth_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Navigator-App/2.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        repos = []
        page = 1
        with httpx.Client(timeout=15.0) as client:
            while True:
                resp = client.get(
                    "https://api.github.com/user/repos",
                    headers=headers,
                    params={"sort": "updated", "per_page": 100, "page": page},
                )
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                for r in batch:
                    repos.append({
                        "full_name": r["full_name"],
                        "name": r["name"],
                        "owner": r["owner"]["login"],
                        "description": r.get("description") or "",
                        "private": r["private"],
                        "language": r.get("language") or "",
                        "pushed_at": r.get("pushed_at") or "",
                    })
                if len(batch) < 100:
                    break
                page += 1
        return {"status": "ok", "repos": repos}
    except Exception as e:
        return {"status": "scope_error", "error": str(e)}


class OauthSetupRequest(BaseModel):
    client_id: str
    client_secret: str

@auth_router.post("/auth/setup-oauth")
async def setup_initial_oauth(req: OauthSetupRequest, db: Session = Depends(get_shared_db)):
    """최초 실행 시 로그인을 위한 OAuth 설정 엔드포인트"""
    if count_users(db) > 0:
        raise HTTPException(status_code=403, detail="이미 시스템이 초기화되었습니다. 설정 패널을 이용하세요.")

    team = db.query(Team).first()
    if not team:
        team = Team(name="Default Team")
        db.add(team)
        db.flush()

    team.github_client_id = req.client_id
    team.github_client_secret = req.client_secret
    db.commit()

    return {"status": "ok", "message": "초기 OAuth 설정이 완료되었습니다."}


# ── 설계 변경 요청 (Agile) — local.db ─────────────────────────

@auth_router.post("/api/change-requests")
async def create_change_request(
    req: ChangeRequestCreate,
    current_user: User = Depends(require_engineer),
    db: Session = Depends(get_db),
):
    if not current_user.team_id or not db.query(AnalysisSession).filter(
        AnalysisSession.run_id == req.session_id,
        AnalysisSession.team_id == current_user.team_id,
    ).first():
        raise HTTPException(status_code=404, detail="분석 세션을 찾을 수 없습니다.")
    cr = DesignChangeRequest(
        session_id=req.session_id,
        requested_by=current_user.id,
        target_section=req.target_section,
        description=req.description,
        status="pending",
    )
    db.add(cr)
    db.commit()
    db.refresh(cr)
    return {
        "status": "ok",
        "id": cr.id,
        "description": cr.description,
        "request_status": cr.status,
    }


@auth_router.get("/api/change-requests")
async def list_change_requests(
    session_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(DesignChangeRequest).join(
        AnalysisSession, AnalysisSession.run_id == DesignChangeRequest.session_id,
    ).filter(AnalysisSession.team_id == current_user.team_id)
    if session_id:
        q = q.filter(DesignChangeRequest.session_id == session_id)
    items = q.order_by(DesignChangeRequest.created_at.desc()).limit(50).all()
    return {
        "status": "ok",
        "items": [
            {
                "id": it.id,
                "session_id": it.session_id,
                "target_section": it.target_section,
                "description": it.description,
                "status": it.status,
                "created_at": str(it.created_at),
            }
            for it in items
        ],
    }


@auth_router.patch("/api/change-requests/{cr_id}")
async def update_change_request(
    cr_id: str,
    req: ChangeRequestUpdate,
    current_user: User = Depends(require_pm),
    db: Session = Depends(get_db),
):
    cr = db.query(DesignChangeRequest).join(
        AnalysisSession, AnalysisSession.run_id == DesignChangeRequest.session_id,
    ).filter(
        DesignChangeRequest.id == cr_id,
        AnalysisSession.team_id == current_user.team_id,
    ).first()
    if not cr:
        raise HTTPException(status_code=404, detail="요청을 찾을 수 없습니다.")
    if req.status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="status는 approved 또는 rejected여야 합니다.")
    cr.status = req.status
    cr.approved_by = current_user.id
    db.commit()
    return {"status": "ok", "new_status": cr.status}

# ── 팀 초대 시스템 (Team Invites) ──────────────────────────────
from auth.shared_models import TeamInvite
from auth.schemas import TeamInviteCreateRequest, TeamInviteResponse
import string
import random

def _generate_invite_code(length=12):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))

@auth_router.post("/auth/teams/{team_id}/invites", response_model=TeamInviteResponse)
async def create_team_invite(
    team_id: str,
    req: TeamInviteCreateRequest,
    current_user: User = Depends(require_pm),
    db: Session = Depends(get_shared_db),
):
    if current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="자신이 속한 팀의 초대만 생성할 수 있습니다.")
    
    invite = TeamInvite(
        team_id=team_id,
        code=_generate_invite_code(),
        creator_id=current_user.id,
        role=req.role,
        max_uses=req.max_uses,
        expires_at=datetime.utcnow() + timedelta(days=req.expires_in_days)
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


@auth_router.get("/auth/teams/{team_id}/invites")
async def list_team_invites(
    team_id: str,
    current_user: User = Depends(require_pm),
    db: Session = Depends(get_shared_db),
):
    if current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    invites = db.query(TeamInvite).filter(TeamInvite.team_id == team_id).all()
    return {"status": "ok", "items": invites}


@auth_router.delete("/auth/teams/{team_id}/invites/{code}")
async def delete_team_invite(
    team_id: str,
    code: str,
    current_user: User = Depends(require_pm),
    db: Session = Depends(get_shared_db),
):
    if current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    
    invite = db.query(TeamInvite).filter(TeamInvite.code == code, TeamInvite.team_id == team_id).first()
    if not invite:
        raise HTTPException(status_code=404, detail="초대 코드를 찾을 수 없습니다.")
    
    db.delete(invite)
    db.commit()
    return {"status": "ok", "message": "초대 코드가 삭제되었습니다."}


@auth_router.get("/auth/invites/{code}")
async def get_invite_info(
    code: str,
    db: Session = Depends(get_shared_db),
):
    invite = db.query(TeamInvite).filter(TeamInvite.code == code).first()
    if not invite:
        raise HTTPException(status_code=404, detail="유효하지 않은 초대 코드입니다.")
    
    if invite.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="만료된 초대 코드입니다.")
    
    if invite.max_uses > 0 and invite.used_count >= invite.max_uses:
        raise HTTPException(status_code=400, detail="사용 횟수가 초과된 초대 코드입니다.")
        
    team = db.query(Team).filter(Team.id == invite.team_id).first()
    return {
        "status": "ok",
        "team_name": team.name if team else "알 수 없는 팀",
        "role": invite.role,
        "expires_at": invite.expires_at,
    }


from auth.shared_models import TeamMember

@auth_router.post("/auth/invites/{code}/join")
async def join_team_via_invite(
    code: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_shared_db),
):
    invite = db.query(TeamInvite).filter(TeamInvite.code == code).first()
    if not invite:
        raise HTTPException(status_code=404, detail="유효하지 않은 초대 코드입니다.")
    
    if invite.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="만료된 초대 코드입니다.")
    
    if invite.max_uses > 0 and invite.used_count >= invite.max_uses:
        raise HTTPException(status_code=400, detail="사용 횟수가 초과된 초대 코드입니다.")
        
    
    # 이미 해당 팀 멤버인지 확인 (자기 초대 포함)
    existing = db.query(TeamMember).filter(
        TeamMember.user_id == user.id,
        TeamMember.team_id == invite.team_id
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="이미 해당 팀의 멤버입니다.")

    tm = TeamMember(user_id=user.id, team_id=invite.team_id, role=invite.role)
    db.add(tm)

    user.team_id = invite.team_id
    user.role = invite.role
    
    invite.used_count += 1
    db.commit()
    db.refresh(user)
    
    plan = _get_plan(db, user.team_id)
    return build_user_response(user, plan)

