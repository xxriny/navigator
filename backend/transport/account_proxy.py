"""Public webapp entry for the private account service."""

import asyncio
import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.id_token import fetch_id_token


account_router = APIRouter()


@account_router.api_route("/account/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
async def forward_account_request(path: str, request: Request):
    if any(part in {"", ".", ".."} for part in path.split("/")) or "%" in path or "\\" in path:
        raise HTTPException(status_code=404)
    if not (path.startswith("auth/") or path == "messages" or
            path.startswith("messages/") or path == "api/teams/me" or
            path.startswith("api/teams/me/")):
        raise HTTPException(status_code=404)
    audience = os.environ.get("NAVIGATOR_AUTH_URL", "").rstrip("/")
    if not audience.startswith("https://"):
        raise HTTPException(status_code=503, detail="계정 서비스가 설정되지 않았습니다.")
    try:
        service_token = await asyncio.to_thread(fetch_id_token, GoogleRequest(), audience)
        headers = {"X-Serverless-Authorization": f"Bearer {service_token}"}
        for name in ("authorization", "content-type"):
            if request.headers.get(name):
                headers[name] = request.headers[name]
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            upstream = await client.request(
                request.method, f"{audience}/{path}", params=request.query_params,
                content=await request.body(), headers=headers,
            )
    except (GoogleAuthError, httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail="계정 서비스에 연결할 수 없습니다.") from exc
    response_headers = {key: upstream.headers[key] for key in ("content-type", "location") if key in upstream.headers}
    return Response(upstream.content, status_code=upstream.status_code, headers=response_headers)
