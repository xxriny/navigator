"""
/auth/* 라우터 — 사용자·팀 관리, JWT 발급, GitHub OAuth.
"""

from __future__ import annotations

import os
import secrets
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

import bcrypt as _bcrypt
import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import get_db
from models import Team, User, Subscription, TeamInvite, TeamMember
from schemas import (
    LoginRequest, RegisterRequest, TokenResponse, UserResponse,
    TeamResponse, TeamCreateRequest, TeamUpdateRequest, DevicePollRequest,
    TeamInviteCreateRequest, TeamInviteResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# ── JWT 설정 ────────────────────────────────────────────────

_SECRET_KEY = os.environ.get("JWT_SECRET", "navigator-server-dev-secret-change-in-prod")
_ALGORITHM  = "HS256"
_TOKEN_DAYS = 7


def _make_token(user_id: str, email: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(days=_TOKEN_DAYS)
    return jwt.encode(
        {"sub": user_id, "email": email, "role": role, "exp": expire},
        _SECRET_KEY, algorithm=_ALGORITHM,
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
    except JWTError:
        return None


# ── 비밀번호 ─────────────────────────────────────────────────

def _hash(pw: str) -> str:
    return _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt(12)).decode()


def _verify(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── 공통 의존성 ───────────────────────────────────────────────

def _token_from_header(authorization: Optional[str] = Header(default=None)) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    return parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else None


def get_current_user(
    token: Optional[str] = Depends(_token_from_header),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_token(token or "")
    if not payload:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")
    return user


# ── 팀 플랜 조회 헬퍼 ─────────────────────────────────────────

def _team_plan(db: Session, team_id: Optional[str]) -> str:
    if not team_id:
        return "free"
    sub = db.query(Subscription).filter(Subscription.team_id == team_id).first()
    return sub.plan if sub else "free"


def _build_user_response(db: Session, user: User) -> UserResponse:
    team_name = user.team.name if user.team else None
    plan = _team_plan(db, user.team_id)
    # TeamMember role이 users.role과 다르면 TeamMember가 정확한 값 — 동기화
    effective_role = user.role
    if user.team_id:
        tm = db.query(TeamMember).filter(
            TeamMember.user_id == user.id,
            TeamMember.team_id == user.team_id,
        ).first()
        if tm and tm.role != user.role:
            user.role = tm.role
            db.commit()
            effective_role = tm.role
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=effective_role,
        github_username=user.github_username,
        github_id=user.github_id,
        github_login=user.github_login,
        team_id=user.team_id,
        team_name=team_name,
        plan=plan,
    )


# ── 엔드포인트 ───────────────────────────────────────────────

@router.get("/status")
async def auth_status(db: Session = Depends(get_db)):
    """앱 최초 실행 여부 체크."""
    count = db.query(User).count()
    return {"has_users": count > 0, "user_count": count}


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=409, detail="이미 등록된 이메일입니다.")

    # 팀 처리
    team: Optional[Team] = None
    if req.team_name:
        if db.query(Team).filter(Team.name == req.team_name).first():
            raise HTTPException(status_code=403, detail="기존 팀은 초대 코드로만 합류할 수 있습니다.")
        team = Team(name=req.team_name)
        db.add(team)
        db.flush()
        db.add(Subscription(team_id=team.id, plan="free", status="active"))

    role = "pm" if team else "software_engineer"
    user = User(
        name=req.name,
        email=req.email,
        password_hash=_hash(req.password),
        role=role,
        github_username=req.github_username,
        team_id=team.id if team else None,
    )
    db.add(user)
    db.flush()
    if team:
        db.add(TeamMember(user_id=user.id, team_id=team.id, role=role))
    db.commit()
    db.refresh(user)

    token = _make_token(user.id, user.email, user.role)
    return TokenResponse(token_type="bearer", access_token=token, user=_build_user_response(db, user))


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not _verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    token = _make_token(user.id, user.email, user.role)
    return TokenResponse(token_type="bearer", access_token=token, user=_build_user_response(db, user))


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _build_user_response(db, user)


# ── 팀 CRUD ─────────────────────────────────────────────────

@router.post("/teams", response_model=TeamResponse)
async def create_team(
    req: TeamCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if db.query(Team).filter(Team.name == req.name).first():
        raise HTTPException(status_code=409, detail="이미 존재하는 팀 이름입니다.")
    team = Team(name=req.name)
    db.add(team)
    db.flush()
    db.add(Subscription(team_id=team.id, plan="free", status="active"))
    db.add(TeamMember(user_id=user.id, team_id=team.id, role="pm"))
    user.team_id = team.id
    user.role = "pm"
    db.commit()
    db.refresh(team)
    return TeamResponse(id=team.id, name=team.name, plan="free")


@router.get("/teams/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.team_id != team_id:
        raise HTTPException(status_code=403, detail="해당 팀에 접근 권한이 없습니다.")
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="팀을 찾을 수 없습니다.")
    plan = _team_plan(db, team_id)
    return TeamResponse(id=team.id, name=team.name, github_repo=team.github_repo, plan=plan)


@router.patch("/teams/{team_id}")
async def update_team(
    team_id: str,
    req: TeamUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role != "pm":
        raise HTTPException(status_code=403, detail="PM 권한이 필요합니다.")
    if user.team_id != team_id:
        raise HTTPException(status_code=403, detail="자신의 팀만 수정할 수 있습니다.")
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="팀을 찾을 수 없습니다.")
    if req.github_repo is not None:
        team.github_repo = req.github_repo
    if req.github_token is not None:
        team.github_token = req.github_token
    if req.github_client_id is not None:
        team.github_client_id = req.github_client_id
    if req.github_client_secret is not None:
        team.github_client_secret = req.github_client_secret
    db.commit()
    return {"status": "ok"}


@router.get("/teams/{team_id}/members")
async def list_members(
    team_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 요청자가 해당 팀의 멤버인지 확인
    requester_tm = db.query(TeamMember).filter(
        TeamMember.user_id == user.id, TeamMember.team_id == team_id
    ).first()
    if not requester_tm:
        raise HTTPException(status_code=403, detail="해당 팀에 접근 권한이 없습니다.")
    memberships = db.query(TeamMember).filter(TeamMember.team_id == team_id).all()
    result = []
    for tm in memberships:
        u = db.query(User).filter(User.id == tm.user_id).first()
        if u:
            result.append({"id": u.id, "name": u.name, "email": u.email, "role": tm.role})
    return {"members": result}


# ── Team Invites ─────────────────────────────────────────────

@router.post("/teams/{team_id}/invites", response_model=TeamInviteResponse)
async def create_team_invite(
    team_id: str,
    req: TeamInviteCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.team_id != team_id:
        raise HTTPException(status_code=403, detail="자신의 팀만 초대할 수 있습니다.")
    if user.role != "pm":
        raise HTTPException(status_code=403, detail="PM 권한이 필요합니다.")
        
    code = secrets.token_urlsafe(16)
    expires_at = datetime.utcnow() + timedelta(days=req.expires_in_days)
    
    invite = TeamInvite(
        team_id=team_id,
        code=code,
        creator_id=user.id,
        role=req.role,
        max_uses=req.max_uses,
        expires_at=expires_at,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


@router.get("/teams/{team_id}/invites", response_model=list[TeamInviteResponse])
async def list_team_invites(
    team_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.team_id != team_id:
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    invites = db.query(TeamInvite).filter(TeamInvite.team_id == team_id).all()
    return invites


@router.delete("/teams/{team_id}/invites/{code}")
async def cancel_team_invite(
    team_id: str,
    code: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.team_id != team_id or user.role != "pm":
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    invite = db.query(TeamInvite).filter(TeamInvite.code == code, TeamInvite.team_id == team_id).first()
    if invite:
        db.delete(invite)
        db.commit()
    return {"status": "ok"}


@router.get("/invites/{code}", response_model=TeamResponse)
async def get_invite_info(
    code: str,
    db: Session = Depends(get_db),
):
    invite = db.query(TeamInvite).filter(TeamInvite.code == code).first()
    if not invite:
        raise HTTPException(status_code=404, detail="유효하지 않거나 만료된 초대입니다.")
    if invite.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="만료된 초대입니다.")
    if invite.max_uses > 0 and invite.used_count >= invite.max_uses:
        raise HTTPException(status_code=400, detail="최대 사용 횟수를 초과한 초대입니다.")
        
    return TeamResponse(id=invite.team.id, name=invite.team.name, plan=_team_plan(db, invite.team.id))


@router.post("/invites/{code}/join", response_model=UserResponse)
async def join_team_via_invite(
    code: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invite = db.query(TeamInvite).filter(TeamInvite.code == code).first()
    if not invite:
        raise HTTPException(status_code=404, detail="유효하지 않은 초대 코드입니다.")
    if invite.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="만료된 초대입니다.")
    if invite.max_uses > 0 and invite.used_count >= invite.max_uses:
        raise HTTPException(status_code=400, detail="사용 한도가 초과된 초대입니다.")

    # 팀 합류
    user.team_id = invite.team_id
    user.role = invite.role
    invite.used_count += 1

    existing_tm = db.query(TeamMember).filter(
        TeamMember.user_id == user.id, TeamMember.team_id == invite.team_id
    ).first()
    if not existing_tm:
        db.add(TeamMember(user_id=user.id, team_id=invite.team_id, role=invite.role))

    db.commit()
    db.refresh(user)

    return _build_user_response(db, user)


@router.patch("/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    req: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role != "pm":
        raise HTTPException(status_code=403, detail="PM 권한이 필요합니다.")
    target = db.query(User).filter(User.id == user_id).first()
    if not target or target.team_id != current_user.team_id:
        raise HTTPException(status_code=404, detail="팀원을 찾을 수 없습니다.")
    new_role = req.get("role")
    target.role = new_role
    tm = db.query(TeamMember).filter(TeamMember.user_id == user_id, TeamMember.team_id == current_user.team_id).first()
    if tm:
        tm.role = new_role
    db.commit()
    return {"status": "ok"}


@router.get("/github/repos")
async def list_github_repos(user: User = Depends(get_current_user)):
    """GitHub OAuth 토큰으로 사용자 레포 목록 반환."""
    if not user.github_oauth_token:
        raise HTTPException(status_code=400, detail="GitHub 연결이 필요합니다.")
    try:
        repos, page = [], 1
        headers = {
            "Authorization": f"Bearer {user.github_oauth_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Navigator-Server/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        with httpx.Client(timeout=15.0) as c:
            while True:
                resp = c.get(
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


@router.get("/github/branches")
async def list_github_branches(
    owner: str,
    repo: str,
    user: User = Depends(get_current_user),
):
    """GitHub OAuth 토큰으로 브랜치 목록 반환."""
    if not user.github_oauth_token:
        raise HTTPException(status_code=400, detail="GitHub 연결이 필요합니다.")
    try:
        headers = {
            "Authorization": f"Bearer {user.github_oauth_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Navigator-Server/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        with httpx.Client(timeout=15.0) as c:
            resp = c.get(
                f"https://api.github.com/repos/{owner}/{repo}/branches",
                headers=headers,
                params={"per_page": 100},
            )
            resp.raise_for_status()
            branches = [{"name": b["name"], "protected": b.get("protected", False)} for b in resp.json()]
        return {"status": "ok", "data": branches}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/github/token")
async def get_github_token(user: User = Depends(get_current_user)):
    """로컬 백엔드가 GitHub API 호출에 사용할 OAuth 토큰 반환."""
    if not user.github_oauth_token:
        raise HTTPException(status_code=404, detail="GitHub 연결이 필요합니다.")
    return {"github_oauth_token": user.github_oauth_token}


@router.get("/github/pulls")
async def list_github_pulls(
    owner: str,
    repo: str,
    state: str = "open",
    limit: int = 30,
    user: User = Depends(get_current_user),
):
    """GitHub OAuth 토큰으로 PR 목록 반환."""
    if not user.github_oauth_token:
        raise HTTPException(status_code=400, detail="GitHub 연결이 필요합니다.")
    try:
        headers = {
            "Authorization": f"Bearer {user.github_oauth_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Navigator-Server/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        with httpx.Client(timeout=15.0) as c:
            resp = c.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                headers=headers,
                params={"state": state, "per_page": min(limit, 100)},
            )
            resp.raise_for_status()
            pulls = [
                {
                    "number": p["number"],
                    "title": p["title"],
                    "state": p["state"],
                    "user": p["user"]["login"],
                    "head_branch": p["head"]["ref"],
                    "base_branch": p["base"]["ref"],
                    "head_sha": p["head"]["sha"],
                    "created_at": p.get("created_at", ""),
                    "updated_at": p.get("updated_at", ""),
                    "html_url": p.get("html_url", ""),
                }
                for p in resp.json()
            ]
        return {"status": "ok", "data": pulls}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _analyze_commits(commits: list) -> dict:
    """커밋 목록을 분석해 analytics dict 반환 (commit_analyzer.py 이식)."""
    import re
    from collections import Counter

    if not commits:
        return {
            "total_commits": 0, "by_author": {}, "by_date": {},
            "top_keywords": [], "recent_commits": [], "activity_trend": "stable",
        }

    by_author: Counter = Counter()
    by_date: Counter = Counter()
    all_words: Counter = Counter()
    recent = []

    stop_words = {"the", "add", "fix", "feat", "update", "merge", "and", "for", "with", "this"}

    for c in commits:
        author = (c.get("commit", {}).get("author") or {}).get("name", "unknown")
        date_str = (c.get("commit", {}).get("author") or {}).get("date", "")
        message = (c.get("commit") or {}).get("message", "")
        sha = c.get("sha", "")

        by_author[author] += 1
        date_key = date_str[:10] if date_str else "unknown"
        by_date[date_key] += 1

        words = re.findall(r"[a-zA-Z가-힣]{3,}", message.lower())
        for w in words:
            if w not in stop_words:
                all_words[w] += 1

        recent.append({"sha": sha[:7], "message": message[:80], "author": author, "date": date_key})

    sorted_dates = sorted(by_date.keys())
    half = len(sorted_dates) // 2
    if half > 0:
        first_half = sum(by_date[d] for d in sorted_dates[:half])
        second_half = sum(by_date[d] for d in sorted_dates[half:])
        trend = "increasing" if second_half > first_half * 1.3 else ("decreasing" if second_half < first_half * 0.7 else "stable")
    else:
        trend = "stable"

    return {
        "total_commits": len(commits),
        "by_author": dict(by_author.most_common(10)),
        "by_date": dict(sorted(by_date.items())),
        "top_keywords": all_words.most_common(10),
        "recent_commits": recent[:10],
        "activity_trend": trend,
    }


@router.get("/github/analytics")
async def github_analytics(
    owner: str,
    repo: str,
    branch: str = "main",
    limit: int = 100,
    user: User = Depends(get_current_user),
):
    """GitHub OAuth 토큰으로 커밋 분석 및 기여자 목록 반환."""
    if not user.github_oauth_token:
        raise HTTPException(status_code=400, detail="GitHub 연결이 필요합니다.")
    try:
        headers = {
            "Authorization": f"Bearer {user.github_oauth_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Navigator-Server/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        with httpx.Client(timeout=20.0) as c:
            commit_resp = c.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits",
                headers=headers,
                params={"sha": branch, "per_page": min(limit, 100)},
            )
            commit_resp.raise_for_status()
            raw_commits = commit_resp.json()

            contrib_resp = c.get(
                f"https://api.github.com/repos/{owner}/{repo}/contributors",
                headers=headers,
                params={"per_page": 50},
            )
            contributors = []
            if contrib_resp.status_code == 200:
                contributors = [
                    {"login": u["login"], "contributions": u["contributions"], "avatar_url": u.get("avatar_url", "")}
                    for u in contrib_resp.json()
                ]

        analytics = _analyze_commits(raw_commits)
        analytics["contributors"] = contributors
        return {"status": "ok", "data": analytics}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/github/issues")
async def list_github_issues(
    owner: str,
    repo: str,
    state: str = "open",
    user: User = Depends(get_current_user),
):
    """GitHub OAuth 토큰으로 이슈 목록 반환."""
    if not user.github_oauth_token:
        raise HTTPException(status_code=400, detail="GitHub 연결이 필요합니다.")
    try:
        headers = {
            "Authorization": f"Bearer {user.github_oauth_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Navigator-Server/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        with httpx.Client(timeout=15.0) as c:
            resp = c.get(
                f"https://api.github.com/repos/{owner}/{repo}/issues",
                headers=headers,
                params={"state": state, "per_page": 50},
            )
            resp.raise_for_status()
            issues = [
                {
                    "number": i["number"],
                    "title": i["title"],
                    "state": i["state"],
                    "user": i["user"]["login"],
                    "labels": [lb["name"] for lb in i.get("labels", [])],
                    "created_at": i.get("created_at", ""),
                    "html_url": i.get("html_url", ""),
                }
                for i in resp.json()
                if "pull_request" not in i  # PR은 제외
            ]
        return {"status": "ok", "data": issues}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/github/disconnect")
async def github_disconnect(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.github_id = None
    user.github_login = None
    user.github_username = None
    user.github_oauth_token = None
    db.commit()
    return {"status": "ok"}


@router.get("/users/me/teams")
async def get_my_teams(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    memberships = db.query(TeamMember).filter(TeamMember.user_id == user.id).all()
    teams_data = []
    for tm in memberships:
        t = db.query(Team).filter(Team.id == tm.team_id).first()
        if t:
            teams_data.append({
                "id": t.id,
                "name": t.name,
                "role": tm.role,
                "plan": _team_plan(db, t.id),
                "is_active": t.id == user.team_id,
            })
    return {"teams": teams_data}


@router.post("/users/me/teams/switch")
async def switch_team(
    req: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team_id = req.get("team_id")
    tm = db.query(TeamMember).filter(TeamMember.user_id == user.id, TeamMember.team_id == team_id).first()
    if not tm:
        raise HTTPException(status_code=403, detail="해당 팀의 멤버가 아닙니다.")
    user.team_id = team_id
    user.role = tm.role
    db.commit()
    db.refresh(user)
    return {"user": _build_user_response(db, user)}


# ── GitHub OAuth Device Flow ──────────────────────────────────

_DEVICE_SCOPE = "repo user:email read:user read:org workflow"

_oauth_sessions: dict = {}


@router.post("/github/device/start")
async def github_device_start(db: Session = Depends(get_db)):
    client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(status_code=503, detail="GitHub Client ID가 설정되지 않았습니다.")
    payload = {"client_id": client_id, "scope": _DEVICE_SCOPE}
    headers = {"Accept": "application/json", "User-Agent": "Navigator-Server/1.0"}
    with httpx.Client() as c:
        resp = c.post(
            "https://github.com/login/device/code",
            content=urllib.parse.urlencode(payload),
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
    return resp.json()


@router.post("/github/device/poll")
async def github_device_poll(
    req: DevicePollRequest,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    payload = {
        "client_id": client_id,
        "device_code": req.device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    with httpx.Client() as c:
        resp = c.post(
            "https://github.com/login/oauth/access_token",
            json=payload,
            headers={"Accept": "application/json", "User-Agent": "Navigator-Server/1.0"},
        )
    data = resp.json()
    if "access_token" not in data:
        return data  # authorization_pending 등

    # GitHub 유저 정보 조회
    gh_token = data["access_token"]
    with httpx.Client() as c:
        gh = c.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {gh_token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "Navigator-Server/1.0",
            },
        ).json()

    github_id = str(gh.get("id", ""))
    email = gh.get("email") or f"{gh.get('login', 'unknown')}@github.local"
    name  = gh.get("name") or gh.get("login", "GitHub User")
    login = gh.get("login", "")

    # 이미 로그인된 사용자가 GitHub를 연동하는 경우: 기존 계정에 GitHub를 연결
    logged_in_user: Optional[User] = None
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token_payload = decode_token(parts[1])
            if token_payload:
                logged_in_user = db.query(User).filter(User.id == token_payload["sub"]).first()

    if logged_in_user:
        # 다른 유저가 이미 같은 github_id를 갖고 있으면 먼저 해제 (UNIQUE 충돌 방지)
        existing = db.query(User).filter(
            User.github_id == github_id,
            User.id != logged_in_user.id,
        ).first()
        if existing:
            existing.github_id = None
            existing.github_login = None
            existing.github_oauth_token = None

        # 기존 로그인 사용자에 GitHub 정보 연결 (계정 전환 없음)
        logged_in_user.github_id          = github_id
        logged_in_user.github_login       = login
        logged_in_user.github_username    = login
        logged_in_user.github_oauth_token = gh_token
        db.commit()
        db.refresh(logged_in_user)
        jwt_token = _make_token(logged_in_user.id, logged_in_user.email, logged_in_user.role)
        return {
            "access_token": jwt_token,
            "token_type": "bearer",
            "user": _build_user_response(db, logged_in_user).model_dump(),
        }

    # 비로그인 상태 — github_id 또는 email로 기존 계정 탐색
    user = db.query(User).filter(User.github_id == github_id).first()
    if not user:
        user = db.query(User).filter(User.email == email).first()

    if not user:
        user = User(
            name=name, email=email, password_hash="",
            role="software_engineer",
            github_id=github_id,
            github_login=login,
            github_username=login,
            github_oauth_token=gh_token,
        )
        db.add(user)
    else:
        user.github_id           = github_id
        user.github_login        = login
        user.github_oauth_token  = gh_token
        user.github_username     = login

    db.commit()
    db.refresh(user)
    jwt_token = _make_token(user.id, user.email, user.role)
    return {
        "access_token": jwt_token,
        "token_type": "bearer",
        "user": _build_user_response(db, user).model_dump(),
    }
