"""
인증 서비스: 사용자 생성, JWT 토큰 발급/검증, 비밀번호 해시
GitHub 연동에는 githubkit과 httpx를 혼합 사용합니다.
"""

from __future__ import annotations

import os
import urllib.parse
import httpx
from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
import bcrypt as _bcrypt_lib
from sqlalchemy.orm import Session

from auth.shared_models import User, Team, Subscription, TeamMember

_SECRET_KEY = os.environ.get("NAVIGATOR_JWT_SECRET", "navigator-dev-secret-change-in-production")
_ALGORITHM = "HS256"
_ACCESS_TOKEN_EXPIRE_DAYS = 7


# ── 비밀번호 ──────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = _bcrypt_lib.gensalt(rounds=12)
    return _bcrypt_lib.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt_lib.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── JWT ──────────────────────────────────────────────────────

def create_access_token(user_id: str, email: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(days=_ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
    except JWTError:
        return None


# ── 사용자 CRUD ──────────────────────────────────────────────

def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: str) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def create_user(
    db: Session,
    name: str,
    email: str,
    password: str,
    role: str = "software_engineer",
    github_username: Optional[str] = None,
    team_name: Optional[str] = None,
    public_signup: bool = False,
) -> User:
    #author: xxrin
    # shared DB role 제약과 호환되도록 구버전 role 값(engineer)을 표준값으로 정규화한다.
    if role == "engineer":
        role = "software_engineer"

    team: Optional[Team] = None
    if team_name:
        team = db.query(Team).filter(Team.name == team_name).first()
        if public_signup and team:
            raise ValueError("기존 팀은 초대 코드로만 합류할 수 있습니다.")
        if not team:
            team = Team(name=team_name)
            db.add(team)
            db.flush()
            db.add(Subscription(team_id=team.id, plan="free", status="active"))

    if public_signup:
        role = "pm" if team else "software_engineer"
    user = User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        signup_method="email",
        role=role,
        github_username=github_username,
        team_id=team.id if team else None,
    )
    db.add(user)
    db.flush()

    if team:
        tm = TeamMember(user_id=user.id, team_id=team.id, role=role)
        db.add(tm)

    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    user = get_user_by_email(db, email)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def count_users(db: Session) -> int:
    return db.query(User).count()


# ── GitHub OAuth Web Flow ────────────────────────────────────

REDIRECT_URI = "navigator://auth/callback"

def exchange_github_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    """Authorization Code를 access_token으로 교환."""
    url = "https://github.com/login/oauth/access_token"
    #author: xxrin
    # authorize 요청과 token 교환 요청의 redirect_uri를 동일하게 맞춰 OAuth 검증 실패를 방지한다.
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    headers = {"Accept": "application/json", "User-Agent": "Navigator-App/2.0"}
    with httpx.Client(follow_redirects=True) as client:
        resp = client.post(url, json=payload, headers=headers)
    try:
        data = resp.json()
    except Exception:
        raise ValueError(f"GitHub 토큰 교환 실패 (HTTP {resp.status_code})")
    if "error" in data:
        raise ValueError(data.get("error_description") or data["error"])
    return data  # { access_token, token_type, scope }


# ── GitHub OAuth Device Flow ──────────────────────────────────

DEVICE_FLOW_SCOPE = "repo user:email read:user read:org workflow gist"

def start_github_device_flow(client_id: str) -> dict:
    """GitHub Device Flow 시작."""
    url = "https://github.com/login/device/code"
    payload = {"client_id": client_id, "scope": DEVICE_FLOW_SCOPE}
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Navigator-App/2.0",
    }
    with httpx.Client(follow_redirects=True) as client:
        resp = client.post(url, content=urllib.parse.urlencode(payload), headers=headers)
        try:
            data = resp.json()
            if resp.status_code != 200:
                desc = data.get("error_description") or data.get("error") or resp.text
                return {"error": "github_error", "error_description": f"GitHub API 에러 ({resp.status_code}): {desc}"}
            return data
        except Exception:
            return {
                "error": "invalid_client",
                "error_description": f"GitHub 서버 응답 오류 ({resp.status_code}): {resp.text[:200]}",
            }


def poll_github_device_token(client_id: str, device_code: str) -> dict:
    """GitHub Device Flow 폴링. Device Flow는 client_secret 불필요. JSON 방식 사용."""
    url = "https://github.com/login/oauth/access_token"
    payload = {
        "client_id": client_id,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    headers = {
        "Accept": "application/json",
        "User-Agent": "Navigator-App/2.0",
    }
    with httpx.Client(follow_redirects=True) as client:
        resp = client.post(url, json=payload, headers=headers)
        try:
            return resp.json()
        except Exception:
            # GitHub가 JSON이 아닌 응답을 보낸 경우 (form-encoded 등)
            return {"error": "parse_error", "error_description": f"응답 파싱 실패 ({resp.status_code}): {resp.text[:200]}"}


def get_github_user_info(access_token: str) -> dict:
    """GitHub OAuth 토큰으로 사용자 정보 조회 (순수 httpx, 동기 이벤트 루프 차단 없음)."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Navigator-App/2.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=15.0) as client:
        resp = client.get("https://api.github.com/user", headers=headers)
        resp.raise_for_status()
        user_data = resp.json()

        email = user_data.get("email")
        if not email:
            try:
                emails_resp = client.get("https://api.github.com/user/emails", headers=headers)
                emails_resp.raise_for_status()
                emails = emails_resp.json()
                primary = next((e["email"] for e in emails if e.get("primary")), None)
                if primary:
                    email = primary
            except Exception:
                pass

        login = user_data["login"]
        return {
            "id": str(user_data["id"]),
            "login": login,
            "name": user_data.get("name") or login,
            "email": email or f"{login}@users.noreply.github.com",
            "avatar_url": user_data.get("avatar_url", ""),
        }


def create_or_update_github_user(
    db: Session,
    github_id: str,
    github_login: str,
    email: str,
    name: str,
    oauth_token: str,
) -> User:
    """GitHub OAuth로 로그인 시 사용자 생성 또는 업데이트."""
    user = db.query(User).filter(User.github_id == github_id).first()
    if not user and email:
        user = db.query(User).filter(User.email == email).first()
    
    if user:
        user.github_id = github_id
        user.github_login = github_login
        user.github_oauth_token = oauth_token
        if not user.github_username:
            user.github_username = github_login
    else:
        # 신규 가입 — team_id는 null로 시작, 프론트 TeamCreateScreen에서 팀을 만들 때 PM 부여
        user = User(
            name=name,
            email=email,
            password_hash="",
            signup_method="github",
            #author: xxrin
            # GitHub OAuth 신규 유저 생성 시 role CHECK 제약 오류를 피하기 위해 허용된 role만 저장한다.
            role="software_engineer",
            github_username=github_login,
            github_id=github_id,
            github_login=github_login,
            github_oauth_token=oauth_token,
        )
        db.add(user)

    db.commit()
    db.refresh(user)
    return user


def build_user_response(user: User, plan: str = "free") -> dict:
    team_name = user.team.name if user.team else None
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "github_username": user.github_username,
        "github_id": user.github_id,
        "github_login": user.github_login,
        "team_id": user.team_id,
        "team_name": team_name,
        "plan": plan,
    }
