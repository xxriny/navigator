"""Authentication and team checks for legacy routes on the public web service."""

import os

from fastapi import Request
from fastapi.responses import JSONResponse

from auth.database import SessionLocal
from auth.service import decode_token, get_user_by_id


async def check_public_api(request: Request):
    path = request.url.path
    if request.method == "OPTIONS":
        return None
    if path.startswith("/metrics"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    if path == "/auth/setup-oauth":
        return JSONResponse({"detail": "Not found"}, status_code=404)
    if path == "/api/webhook/github":
        if not os.environ.get("NAVIGATOR_GITHUB_WEBHOOK_SECRET"):
            return JSONResponse({"detail": "Webhook is not configured"}, status_code=503)
        return None
    if not path.startswith("/api/") or path == "/api/config":
        return None
    if path == "/api/github/issues/import":
        return JSONResponse({"detail": "이 기능은 공개 배포에서 사용할 수 없습니다."}, status_code=404)
    parts = request.headers.get("authorization", "").split()
    claims = decode_token(parts[1]) if len(parts) == 2 and parts[0].lower() == "bearer" else None
    if not claims:
        return JSONResponse({"detail": "인증이 필요합니다."}, status_code=401)
    try:
        with SessionLocal() as db:
            user = get_user_by_id(db, claims.get("sub", ""))
            if not user or not user.team_id:
                return JSONResponse({"detail": "현재 팀이 필요합니다."}, status_code=403)
            if path == "/api/dev-tracking/knowledge/query":
                body = await request.json()
                if not isinstance(body, dict) or body.get("team_id") != user.team_id:
                    return JSONResponse({"detail": "현재 팀의 데이터만 사용할 수 있습니다."}, status_code=403)
            elif path == "/api/dev-tracking/analyses":
                if request.query_params.get("team_id") != user.team_id:
                    return JSONResponse({"detail": "현재 팀의 데이터만 사용할 수 있습니다."}, status_code=403)
            elif path.startswith("/api/dev-tracking/analyses/"):
                from auth.shared_models import DevPrAnalysis
                analysis_id = path.removeprefix("/api/dev-tracking/analyses/")
                if not db.query(DevPrAnalysis).filter(
                    DevPrAnalysis.id == analysis_id,
                    DevPrAnalysis.team_id == user.team_id,
                ).first():
                    return JSONResponse({"detail": "분석 이력을 찾을 수 없습니다."}, status_code=404)
    except ValueError:
        return JSONResponse({"detail": "요청 형식이 올바르지 않습니다."}, status_code=400)
    except Exception:
        return JSONResponse({"detail": "권한을 확인할 수 없습니다."}, status_code=503)
    return None
