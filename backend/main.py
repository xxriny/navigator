"""
PM Agent Pipeline v2 — FastAPI Backend Sidecar v2.2
앱 초기화 + 라우터 등록만 담당. (REQ-001 ~ REQ-007 리팩토링)

계층 구조:
  transport/      — WebSocket 연결 관리, REST APIRouter
  orchestration/  — 파이프라인 실행 로직
  result_shaping/ — raw 결과 정형화 (schema-driven)
  observability/  — structlog 기반 로깅, Prometheus 메트릭

Usage:
    python main.py --port 8765
    (Electron main.js가 빈 포트를 할당하여 인자로 전달)
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
import sys
import multiprocessing

import argparse
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from version import APP_VERSION

# ── 경로 설정 ────────────────────────────────────────────
# PyInstaller 번들로 실행 시 __file__은 bundle 내부(_internal/)를 가리키므로
# Electron이 주입하는 NAVIGATOR_BACKEND_ROOT 환경변수를 우선 사용.
# 일반 실행(개발/venv)에서는 이 환경변수가 없으므로 __file__ 기준 경로 사용.
if getattr(sys, 'frozen', False):
    ROOT = os.environ.get('NAVIGATOR_BACKEND_ROOT') or os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# .env 로드 (UTF-8 강제)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"), encoding="utf-8")
except ImportError:
    pass

# ── 계층 임포트 ──────────────────────────────────────────
from transport.rest_handler import rest_router
from transport.account_proxy import account_router
from transport.public_guard import check_public_api
from transport.team_router import team_router
from transport.message_router import message_router
from transport.ws_handler import websocket_pipeline
from observability.metrics import make_metrics_app
from observability.logger import get_logger
from auth.router import auth_router
from auth.database import init_db
from auth.quota import MODEL_HTTP_PATHS, check_model_call

ALLOWED_ORIGIN_REGEX = os.environ.get(
    "NAVIGATOR_ALLOWED_ORIGIN_REGEX",
    r"^(null|https?://(127\.0\.0\.1|localhost)(:\d+)?)$",
)
STATIC_DIR = os.environ.get(
    "NAVIGATOR_STATIC_DIR",
    os.path.join(os.path.dirname(ROOT), "dist"),
)


# ── App Lifespan ─────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    get_logger().info("backend_starting", pid=os.getpid(), version=APP_VERSION)
    yield
    get_logger().info("backend_shutdown")


# ── FastAPI 앱 생성 ──────────────────────────────────────
app = FastAPI(
    title="NAVIGATOR Backend",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_email_model_calls(request, call_next):
    if request.method == "POST" and request.url.path in MODEL_HTTP_PATHS:
        authorization = request.headers.get("authorization", "")
        parts = authorization.split()
        token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
        result = check_model_call(token)
        if result != "allowed":
            status = {"unauthorized": 401, "limited": 429, "unavailable": 503}[result]
            return JSONResponse({"detail": "모델 호출 한도를 확인할 수 없습니다." if status == 503 else
                                 "이메일 가입 계정의 모델 호출 10회를 모두 사용했습니다." if status == 429 else
                                 "인증이 필요합니다."}, status_code=status)
    return await call_next(request)


@app.middleware("http")
async def protect_public_api(request, call_next):
    denied = await check_public_api(request)
    return denied if denied is not None else await call_next(request)

# ── 라우터 등록 ──────────────────────────────────────────
app.include_router(auth_router)
app.include_router(account_router)
app.include_router(team_router)
app.include_router(message_router)
app.include_router(rest_router)
app.add_api_websocket_route("/ws/pipeline", websocket_pipeline)

# ── Prometheus 메트릭 엔드포인트 (prometheus_client 설치 시 활성화) ──
_metrics_app = make_metrics_app()
if _metrics_app is not None:
    app.mount("/metrics", _metrics_app)


if os.path.isdir(STATIC_DIR):
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        requested = os.path.realpath(os.path.join(STATIC_DIR, full_path))
        static_root = os.path.realpath(STATIC_DIR)
        if os.path.commonpath([requested, static_root]) == static_root and os.path.isfile(requested):
            return FileResponse(requested)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ── Entry Point ──────────────────────────────────────────
if __name__ == "__main__":
    multiprocessing.freeze_support()  # Windows spawn 모드에서 자식 프로세스 중복 실행 방지
    import uvicorn
    get_logger().info("backend_loading_subsystems")
    get_logger().info("backend_subsystems_initializing")
    
    parser = argparse.ArgumentParser(description="PM Agent Pipeline Backend")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")), help="Server port")
    parser.add_argument("--host", type=str, default=os.environ.get("HOST", "127.0.0.1"), help="Server host")
    args = parser.parse_args()

    get_logger().info("backend_entry", host=args.host, port=args.port)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
        ws_ping_interval=30,
        ws_ping_timeout=30,
    )
