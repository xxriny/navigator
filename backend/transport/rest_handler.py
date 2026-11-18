"""
REST API 엔드포인트 (REQ-001)
/health, /api/* 엔드포인트를 APIRouter로 분리.
main.py에서 app.include_router(rest_router) 호출.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import os
import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Request, UploadFile, File, HTTPException
from fastapi.security import OAuth2PasswordBearer
from auth.deps import get_current_user, get_current_user_optional, _extract_token
from auth.database import get_db, get_shared_db
from sqlalchemy.orm import Session
from auth.models import User
from pydantic import BaseModel, Field
from version import APP_VERSION, DEFAULT_MODEL
from observability.logger import get_logger
from pipeline.core.context import active_jwt_token
# pipeline 관련 임포트는 첫 요청 시 지연 로드 (langgraph 콜드스타트 방지)
_pipeline_loaded = False

def _ensure_pipeline():
    global _pipeline_loaded, normalize_action_type
    global get_analysis_pipeline, get_idea_pipeline
    global execute_pipeline, validate_analysis_inputs, build_reverse_context, analysis_pipeline_type
    if _pipeline_loaded:
        return
    from pipeline.core.action_type import normalize_action_type as _nat
    from pipeline.orchestration.facade import (
        get_analysis_pipeline as _gap,
        get_idea_pipeline as _gip,
    )
    from orchestration.executor import execute_pipeline as _ep
    from orchestration.pipeline_runner import (
        validate_analysis_inputs as _vai,
        build_reverse_context as _brc,
        analysis_pipeline_type as _apt,
    )
    normalize_action_type = _nat
    get_analysis_pipeline = _gap
    get_idea_pipeline = _gip
    execute_pipeline = _ep
    validate_analysis_inputs = _vai
    build_reverse_context = _brc
    analysis_pipeline_type = _apt
    _pipeline_loaded = True

# ── 상수 ─────────────────────────────────────────────────
AVAILABLE_MODELS = [
    DEFAULT_MODEL,
    # "gemini-3.0-flash",  # not available yet
    # "gemini-3.0-pro",    # not available yet
]


# ── 경로 보안 헬퍼 ────────────────────────────────────────
_allowed_project_roots: set[str] = set()


def _normalize_path(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _is_within_root(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def register_project_root(root_path: str):
    _allowed_project_roots.add(_normalize_path(root_path))


def is_allowed_project_file(path: str) -> bool:
    normalized = _normalize_path(path)
    return any(_is_within_root(normalized, root) for root in _allowed_project_roots)


def _require_team(user: User, team_id: str) -> None:
    if not user.team_id or not team_id or user.team_id != team_id:
        raise HTTPException(status_code=403, detail="현재 팀의 데이터만 사용할 수 있습니다.")


def _require_owned_session(db: Session, user: User, run_id: str) -> None:
    from auth.models import AnalysisSession
    if not user.team_id or not db.query(AnalysisSession).filter(
        AnalysisSession.run_id == run_id,
        AnalysisSession.team_id == user.team_id,
    ).first():
        raise HTTPException(status_code=404, detail="분석 세션을 찾을 수 없습니다.")


def _ensure_memo_session(db: Session, user: User, session_id: str) -> bool:
    """Register browser-created timestamp sessions for team-scoped memo storage."""
    from auth.models import AnalysisSession
    session = db.query(AnalysisSession).filter(AnalysisSession.run_id == session_id).first()
    if session:
        _require_owned_session(db, user, session_id)
        return True
    if not user.team_id or not re.fullmatch(r"\d{10,16}", session_id or ""):
        raise HTTPException(status_code=404, detail="분석 세션을 찾을 수 없습니다.")
    return False


def _team_project_path_allowed(user: User, path: str) -> bool:
    try:
        roots = json.loads(os.environ.get("NAVIGATOR_TEAM_PROJECT_ROOTS", "{}"))
        team_roots = roots.get(user.team_id, []) if isinstance(roots, dict) else []
        return isinstance(team_roots, list) and any(
            isinstance(root, str) and _is_within_root(_normalize_path(path), _normalize_path(root))
            for root in team_roots
        )
    except (ValueError, TypeError):
        return False


# ── Pydantic 모델 ─────────────────────────────────────────
class AnalysisRequest(BaseModel):
    idea: str
    context: str = ""
    api_key: str = ""
    model: str = DEFAULT_MODEL
    action_type: str = "CREATE"
    source_dir: str = ""
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    use_dev_knowledge: bool = True
    owner: str = ""
    repo: str = ""
    branch_name: str = ""
    dev_knowledge_query: str = ""


class IdeaChatRequest(BaseModel):
    message: str
    chat_history: list = []
    previous_result: dict = {}
    api_key: str = ""
    model: str = DEFAULT_MODEL


class ExtractFinalIdeaRequest(BaseModel):
    chat_history: list = []
    memos: list = []
    api_key: str = ""
    model: str = DEFAULT_MODEL
    auth_token: str = ""


class ScanRequest(BaseModel):
    path: str
    max_depth: int = 3


class ReadFileRequest(BaseModel):
    path: str


class DeleteSessionRequest(BaseModel):
    pass


class MemoRequest(BaseModel):
    session_id: str
    text: str
    selected_text: str = ""
    section: str = "Global"
    detail: str = ""


class MemoApplyRequest(BaseModel):
    memo_ids: list = []
    reflected_version: Optional[str] = None


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = APP_VERSION


# ── Router ───────────────────────────────────────────────
rest_router = APIRouter()


@rest_router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse()


@rest_router.get("/api/config")
async def get_config():
    from pipeline.core.utils import get_effective_key
    try:
        has_key = bool(get_effective_key(""))
    except ValueError:
        has_key = False
    return {
        "has_api_key": has_key,
        "default_model": DEFAULT_MODEL,
        "available_models": AVAILABLE_MODELS,
    }


@rest_router.post("/api/scan-folder")
async def scan_folder_endpoint(req: ScanRequest, current_user: User = Depends(get_current_user)):
    from connectors.folder_connector import scan_folder
    if not req.path:
        return {"status": "error", "error": "폴더 경로가 비어있습니다."}

    normalized_root = _normalize_path(req.path)
    if not _team_project_path_allowed(current_user, normalized_root):
        raise HTTPException(status_code=403, detail="팀에 허용된 프로젝트 경로가 아닙니다.")
    if not os.path.isdir(normalized_root):
        return {"status": "error", "error": f"유효하지 않은 폴더: {req.path}"}

    try:
        register_project_root(normalized_root)
        result = scan_folder(normalized_root, max_depth=req.max_depth)
        return {"status": "ok", **result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/read-file")
async def read_file_endpoint(req: ReadFileRequest, current_user: User = Depends(get_current_user)):
    if not req.path:
        return {"status": "error", "error": "파일 경로가 비어있습니다."}

    normalized_path = _normalize_path(req.path)
    if not _team_project_path_allowed(current_user, normalized_path):
        raise HTTPException(status_code=403, detail="팀에 허용된 프로젝트 경로가 아닙니다.")
    if not os.path.isfile(normalized_path):
        return {"status": "error", "error": f"유효하지 않은 파일: {req.path}"}
    if not _allowed_project_roots:
        return {"status": "error", "error": "먼저 프로젝트 폴더를 스캔하세요."}
    if not is_allowed_project_file(normalized_path):
        return {"status": "error", "error": "선택한 프로젝트 폴더 밖의 파일은 읽을 수 없습니다."}

    try:
        with open(normalized_path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read()
        return {"status": "ok", "path": normalized_path, "content": content}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _to_response(result) -> dict:
    """PipelineResult → REST JSON 응답 변환."""
    if result.success:
        return {"status": "ok", "data": result.data}
    return {"status": "error", "error": result.error}


def _compact_result_value(value, *, depth: int = 0):
    if depth > 4:
        return "<truncated>"
    if isinstance(value, str):
        return value if len(value) <= 4000 else value[:4000] + "\n<truncated>"
    if isinstance(value, list):
        return [_compact_result_value(item, depth=depth + 1) for item in value[:25]]
    if isinstance(value, dict):
        compact = {}
        for key, item in value.items():
            if key in {
                "content",
                "source",
                "raw",
                "stdout",
                "stderr",
                "stdout_tail",
                "stderr_tail",
                "prompt",
                "user_msg",
                "system_msg",
                "messages",
                "semantic_slices",
            }:
                compact[key] = _compact_result_value(item, depth=depth + 1)
            elif key in {"files", "generated_files"} and isinstance(item, list):
                compact[key] = [
                    {
                        sub_key: _compact_result_value(sub_value, depth=depth + 1)
                        for sub_key, sub_value in file_item.items()
                        if sub_key not in {"content", "source"}
                    }
                    for file_item in item[:50]
                    if isinstance(file_item, dict)
                ]
            else:
                compact[key] = _compact_result_value(item, depth=depth + 1)
        return compact
    return value


@rest_router.post("/api/analyze")
async def analyze(
    req: AnalysisRequest,
    shared_db: Session = Depends(get_shared_db),
    current_user: User = Depends(get_current_user),
):
    _require_team(current_user, req.team_id or current_user.team_id)
    _ensure_pipeline()
    try:
        api_key = req.api_key
        action_type = normalize_action_type(req.action_type)
        validation_error = validate_analysis_inputs(action_type, req.idea, req.source_dir)
        if validation_error:
            return {"status": "error", "error": validation_error}

        context = req.context
        extra_state: dict = {}
        if action_type == "REVERSE_ENGINEER" and not (context or "").strip():
            context, code_inventory = build_reverse_context(req.source_dir)
            if not context:
                return {
                    "status": "error",
                    "error": "선택한 폴더에서 분석 가능한 함수/메서드를 찾지 못했습니다.",
                }
            if code_inventory:
                extra_state["code_inventory"] = code_inventory

        dev_knowledge_context = ""
        if req.use_dev_knowledge and action_type in {"UPDATE", "REVERSE_ENGINEER"}:
            try:
                from pipeline.domain.dev_tracking.knowledge import query_dev_knowledge_artifacts

                knowledge = await asyncio.to_thread(
                    query_dev_knowledge_artifacts,
                    shared_db,
                    team_id=current_user.team_id,
                    owner=req.owner,
                    repo=req.repo,
                    branch_name=req.branch_name,
                    query=req.dev_knowledge_query or req.idea,
                    limit=5,
                )
                dev_knowledge_context = knowledge.get("context_text", "")
            except Exception as knowledge_error:
                get_logger().warning(f"Dev Tracking knowledge lookup skipped for SA analysis: {knowledge_error}")

        return _to_response(execute_pipeline(
            get_analysis_pipeline(action_type),
            {
                "api_key": api_key,
                "model": req.model,
                "input_idea": req.idea,
                "project_context": context,
                "source_dir": req.source_dir,
                "action_type": action_type,
                "dev_knowledge_context": dev_knowledge_context,
                "run_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
                **extra_state,
            },
            analysis_pipeline_type(action_type),
        ))
    except Exception as e:
        get_logger().exception("analyze endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/idea-chat")
async def idea_chat(req: IdeaChatRequest, current_user: User = Depends(get_current_user)):
    _require_team(current_user, current_user.team_id)
    _ensure_pipeline()
    try:
        api_key = req.api_key
        return _to_response(execute_pipeline(
            get_idea_pipeline(),
            {
                "api_key": api_key,
                "model": req.model,
                "user_request": req.message,
                "chat_history": req.chat_history,
                "previous_result": req.previous_result,
            },
            "idea_chat",
            result_mutator=lambda s: s.update({"chat_reply": s.get("agent_reply", "")}),
        ))
    except Exception as e:
        get_logger().exception("idea_chat endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/upload-doc")
async def upload_doc(file: UploadFile = File(...)):
    """PDF / Word / TXT 파일에서 텍스트를 추출해 반환한다."""
    name = (file.filename or "").lower()
    content = await file.read()
    try:
        if name.endswith(".pdf"):
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        elif name.endswith(".docx"):
            import io
            from docx import Document
            doc = Document(io.BytesIO(content))
            text = "\n".join(p.text for p in doc.paragraphs)
        elif name.endswith(".doc"):
            return {"status": "error", "error": ".doc 형식은 지원하지 않습니다. .docx로 변환 후 업로드해 주세요."}
        else:
            # .txt, .md 등 텍스트 계열
            text = content.decode("utf-8", errors="replace")

        text = text.strip()
        if not text:
            return {"status": "error", "error": "파일에서 텍스트를 추출하지 못했습니다."}
        return {"status": "ok", "text": text, "chars": len(text)}
    except Exception as e:
        get_logger().exception("upload_doc failed")
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/extract-final-idea")
async def extract_final_idea_endpoint(
    req: ExtractFinalIdeaRequest,
    current_user: User = Depends(get_current_user),
    auth_token: str = Depends(_extract_token),
):
    """Sync 빌드 전 Pre-flight 단계. 날것의 대화/메모를 정제해 최종 확정 요구사항 마크다운만 추출."""
    _require_team(current_user, current_user.team_id)
    try:
        from pipeline.domain.chat.idea_extractor import extract_final_idea
        result = extract_final_idea(
            chat_history=req.chat_history or [],
            memos=req.memos or [],
            api_key=req.api_key or "",
            model=req.model or DEFAULT_MODEL,
            auth_token=auth_token,
        )
        return result
    except Exception as e:
        get_logger().exception("extract_final_idea endpoint failed")
        return {
            "status": "error",
            "summary_markdown": "",
            "dropped_points": [],
            "error": str(e),
        }


@rest_router.delete("/api/session/{run_id}")
async def delete_session(run_id: str, req: Optional[DeleteSessionRequest] = None):
    if not re.match(r"^\d{8}_\d{6}$", run_id):
        return {"status": "error", "error": "Invalid run_id format. Expected YYYYMMDD_HHMMSS"}
    return {"status": "ok", "message": f"Session {run_id} deleted"}


@rest_router.get("/api/session/{run_id}/restore")
async def restore_session(run_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """로컬 DB(AnalysisResult)에서 이전 분석 결과를 복원."""
    import json
    from auth.models import AnalysisResult
    _require_owned_session(db, current_user, run_id)
    try:
        record = db.query(AnalysisResult).filter(AnalysisResult.run_id == run_id).first()
        if not record:
            return {"status": "error", "error": f"No saved result for run_id '{run_id}'"}
        data = json.loads(record.shaped_result)
        return {"status": "ok", "data": data}
    except Exception as e:
        get_logger().exception(f"restore_session failed for {run_id}")
        return {"status": "error", "error": str(e)}


@rest_router.get("/api/memos")
async def get_memos_endpoint(session_id: Optional[str] = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from auth.models import MemoItem
    if not session_id:
        return {"status": "ok", "memos": []}
    if not _ensure_memo_session(db, current_user, session_id):
        return {"status": "ok", "memos": []}
    try:
        q = db.query(MemoItem).filter(MemoItem.session_id == session_id, MemoItem.team_id == current_user.team_id)
        items = q.order_by(MemoItem.created_at.asc()).all()
        memos = [
            {
                "id": m.id,
                "session_id": m.session_id,
                "text": m.text,
                "metadata": {
                    "selected_text": m.selected_text,
                    "section": m.section,
                    "detail": m.detail,
                    "applied": m.applied,
                    "applied_at": m.applied_at,
                    "reflected_version": getattr(m, "reflected_version", None),
                },
            }
            for m in items
        ]
        return {"status": "ok", "memos": memos}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/memos")
async def add_memo_endpoint(req: MemoRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from auth.models import AnalysisSession, MemoItem
    if not _ensure_memo_session(db, current_user, req.session_id):
        db.add(AnalysisSession(run_id=req.session_id, team_id=current_user.team_id, created_by=current_user.id))
    try:
        memo = MemoItem(
            session_id=req.session_id,
            team_id=current_user.team_id,
            text=req.text,
            selected_text=req.selected_text,
            section=req.section,
            detail=req.detail,
        )
        db.add(memo)
        db.commit()
        db.refresh(memo)
        return {"status": "ok", "memo_id": memo.id}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@rest_router.delete("/api/memos/{memo_id}")
async def delete_memo_endpoint(memo_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from auth.models import MemoItem
    try:
        memo = db.query(MemoItem).filter(MemoItem.id == memo_id, MemoItem.team_id == current_user.team_id).first()
        if memo:
            db.delete(memo)
            db.commit()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/memos/apply")
async def apply_memos_endpoint(req: MemoApplyRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from auth.models import MemoItem
    from datetime import datetime as _dt
    try:
        ts = _dt.utcnow().isoformat()
        updated = (
            db.query(MemoItem)
            .filter(MemoItem.id.in_(req.memo_ids), MemoItem.team_id == current_user.team_id)
            .all()
        )
        for m in updated:
            m.applied = True
            m.applied_at = ts
            if req.reflected_version:
                m.reflected_version = req.reflected_version
        db.commit()
        return {"status": "ok", "updated": len(updated)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Agile Layer ───────────────────────────────────────────


class AgileImpactRequest(BaseModel):
    change_description: str
    sa_data: dict
    auth_token: str = ""
    api_key: str = ""
    model: str = DEFAULT_MODEL
    session_id: Optional[str] = None
    use_llm: bool = True
    use_dev_knowledge: bool = True
    team_id: str = ""
    owner: str = ""
    repo: str = ""
    branch_name: str = ""
    dev_knowledge_query: str = ""



@rest_router.post("/api/agile/impact")
async def agile_impact(
    req: AgileImpactRequest,
    shared_db: Session = Depends(get_shared_db),
    current_user: User = Depends(get_current_user),
    auth_token: str = Depends(_extract_token),
):
    """변경 영향 분석 (RAG + LLM 2-stage)."""
    _require_team(current_user, req.team_id or current_user.team_id)
    active_jwt_token.set(auth_token)
    if not req.change_description.strip():
        return {"status": "error", "error": "change_description이 비어있습니다."}
    try:
        from pipeline.domain.agile.nodes.impact import run_impact_analyzer
        dev_knowledge_context = ""
        if req.use_dev_knowledge:
            try:
                from pipeline.domain.dev_tracking.knowledge import query_dev_knowledge_artifacts

                knowledge = query_dev_knowledge_artifacts(
                    shared_db,
                    team_id=current_user.team_id,
                    owner=req.owner,
                    repo=req.repo,
                    branch_name=req.branch_name,
                    query=req.dev_knowledge_query or req.change_description,
                    limit=5,
                )
                dev_knowledge_context = knowledge.get("context_text", "")
            except Exception as knowledge_error:
                get_logger().warning(f"Dev Tracking knowledge lookup skipped: {knowledge_error}")
        result = run_impact_analyzer(
            change_description=req.change_description,
            sa_data=req.sa_data,
            api_key=req.api_key,
            model=req.model,
            session_id=req.session_id,
            use_llm=req.use_llm,
            dev_knowledge_context=dev_knowledge_context,
        )
        return {"status": "ok", "data": result.model_dump()}
    except Exception as e:
        get_logger().exception("agile_impact endpoint failed")
        return {"status": "error", "error": str(e)}


# ── GitHub Integration ────────────────────────────────────

class GitHubVerifyRequest(BaseModel):
    token: str
    owner: str
    repo: str


class GitHubPublishRequest(BaseModel):
    token: Optional[str] = None  # 호환성 유지 (deprecated, JWT 우선)
    owner: str
    repo: str
    result_data: dict
    page_title: str = "SA 설계 문서"
    project_name: str = "Project"
    publish_mode: str = "wiki"  # "wiki" | "issue"
    api_key: str = ""
    model: str = "gemini-2.0-flash-lite"


class GitHubAnalyticsRequest(BaseModel):
    token: Optional[str] = None  # deprecated
    owner: str
    repo: str
    branch: str = "main"
    limit: int = 30


class GitHubIssuesRequest(BaseModel):
    token: Optional[str] = None  # deprecated
    owner: str
    repo: str
    state: str = "open"


class GitHubPullsRequest(BaseModel):
    token: Optional[str] = None  # deprecated
    owner: str
    repo: str
    state: str = "open"
    limit: int = 30


@rest_router.post("/api/github/verify")
async def github_verify(req: GitHubVerifyRequest):
    """GitHub 토큰 + 레포지토리 접근 확인."""
    try:
        from connectors.github_connector import verify_token, GitHubConnector
        token_info = verify_token(req.token)
        if not token_info["valid"]:
            return {"status": "error", "error": token_info.get("error", "Invalid token")}
        connector = GitHubConnector(req.token)
        repo_obj = connector.get_repo(req.owner, req.repo)
        return {
            "status": "ok",
            "user": token_info["login"],
            "repo": repo_obj.full_name,
            "private": repo_obj.private,
            "default_branch": repo_obj.default_branch,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/github/publish")
async def github_publish(
    req: GitHubPublishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SA 설계 문서를 GitHub Issues(design-doc)에 퍼블리시."""
    # JWT로 인증한 사용자의 DB OAuth 토큰 우선, 없으면 body.token 폴백
    token = (current_user.github_oauth_token if current_user else None) or req.token
    if not token:
        return {"status": "error", "error": "GitHub OAuth 토큰이 없습니다. GitHub 연결 후 다시 시도하세요."}
    try:
        from pipeline.domain.agile.wiki_publisher import publish_to_github
        result = publish_to_github(
            result_data=req.result_data,
            owner=req.owner,
            repo=req.repo,
            token=token,
            page_title=req.page_title,
            project_name=req.project_name,
            mode=req.publish_mode,
            api_key=req.api_key,
            model=req.model,
        )
        return {"status": "ok", **result}
    except Exception as e:
        get_logger().exception("github_publish endpoint failed")
        err_str = str(e)
        wiki_disabled = "wiki_disabled=true" in err_str
        return {
            "status": "error",
            "error": err_str.replace(" | wiki_disabled=true", ""),
            "wiki_disabled": wiki_disabled,
        }


@rest_router.post("/api/github/analytics")
async def github_analytics(
    req: GitHubAnalyticsRequest,
    current_user: User = Depends(get_current_user),
):
    """커밋 히스토리 분석."""
    token = (current_user.github_oauth_token if current_user else None) or req.token
    if not token:
        return {"status": "error", "error": "GitHub OAuth 토큰이 필요합니다."}
    try:
        from connectors.github_connector import GitHubConnector
        from pipeline.domain.agile.commit_analyzer import analyze_commits
        connector = GitHubConnector(token)
        commits = connector.get_commits(req.owner, req.repo, req.branch, req.limit)
        analytics = analyze_commits(commits)
        return {
            "status": "ok",
            "data": {
                "total_commits": analytics.total_commits,
                "by_author": analytics.by_author,
                "by_date": analytics.by_date,
                "top_keywords": analytics.top_keywords,
                "recent_commits": analytics.recent_commits,
                "activity_trend": analytics.activity_trend,
                "contributors": [
                    c.__dict__ for c in connector.get_contributors(req.owner, req.repo)
                ],
            },
        }
    except Exception as e:
        get_logger().exception("github_analytics endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/github/branches")
async def github_branches(
    req: GitHubAnalyticsRequest,
    current_user: User = Depends(get_current_user),
):
    """GitHub 브랜치 목록 조회."""
    token = (current_user.github_oauth_token if current_user else None) or req.token
    if not token:
        return {"status": "error", "error": "GitHub OAuth 토큰이 필요합니다."}
    try:
        from connectors.github_connector import GitHubConnector
        connector = GitHubConnector(token)
        branches = connector.list_branches(req.owner, req.repo)
        return {"status": "ok", "data": branches}
    except Exception as e:
        get_logger().exception("github_branches endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/github/issues")
async def github_issues(
    req: GitHubIssuesRequest,
    current_user: User = Depends(get_current_user),
):
    """GitHub Issues 목록 조회."""
    token = (current_user.github_oauth_token if current_user else None) or req.token
    if not token:
        return {"status": "error", "error": "GitHub OAuth 토큰이 필요합니다."}
    try:
        from connectors.github_connector import GitHubConnector
        connector = GitHubConnector(token)
        issues = connector.get_issues(req.owner, req.repo, req.state)
        return {"status": "ok", "data": [i.__dict__ for i in issues]}
    except Exception as e:
        get_logger().exception("github_issues endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/github/pulls")
async def github_pulls(
    req: GitHubPullsRequest,
    current_user: User = Depends(get_current_user),
):
    """GitHub PR 목록 조회."""
    token = (current_user.github_oauth_token if current_user else None) or req.token
    if not token:
        return {"status": "error", "error": "GitHub OAuth 토큰이 필요합니다."}
    try:
        from connectors.github_connector import GitHubConnector
        connector = GitHubConnector(token)
        # author: xxrin
        # Dev Tracking 수동 실행 폼에서 브랜치와 HEAD SHA를 자동 선택할 수 있도록 PR 목록을 제공한다.
        pulls = connector.list_pull_requests(req.owner, req.repo, req.state, req.limit)
        return {"status": "ok", "data": [p.__dict__ for p in pulls]}
    except Exception as e:
        get_logger().exception("github_pulls endpoint failed")
        return {"status": "error", "error": str(e)}


# ── Phase 5: Task Coordinator + Doc Sync ────────────────────

class TaskCreateRequest(BaseModel):
    task_type: str
    title: str
    description: str = ""
    area: str = ""       # backend | frontend | fullstack | devops
    assignee: str = ""
    payload: dict = {}
    created_by: str = ""
    team_id: str = ""


class TaskUpdateRequest(BaseModel):
    status: str
    reviewed_by: str = ""
    result: str = ""


class DevGapDecisionRequest(BaseModel):
    reason: str = ""
    result: dict = Field(default_factory=dict)


class DevGapSaReviewRequest(BaseModel):
    reason: str = ""
    result: dict = Field(default_factory=dict)


def _normalize_status_check(result: dict | None) -> dict:
    normalized = {
        "status": "WARN",
        "status_updated": False,
        "state": "",
        "context": "NAVIGATOR Dev Tracking",
        "description": "",
        "error": "",
    }
    if isinstance(result, dict):
        normalized.update(result)
    return normalized


def _normalize_followup(result: dict | None, *, decision_status: str) -> dict:
    normalized = {
        "status": "WARN",
        "artifact": {},
        "rag_metadata": {"stored": False, "write_enabled": False},
        "code_chunk_upsert": {"write_enabled": False, "code_chunks_upserted": False, "stored_count": 0},
        "doc_sync": {},
        "pr_comment": {},
    }
    if isinstance(result, dict):
        normalized.update(result)
    if not isinstance(normalized.get("rag_metadata"), dict):
        normalized["rag_metadata"] = {"stored": False, "write_enabled": False}
    if not isinstance(normalized.get("code_chunk_upsert"), dict):
        normalized["code_chunk_upsert"] = {"write_enabled": False, "code_chunks_upserted": False, "stored_count": 0}
    normalized["doc_sync"] = {
        "synced": False,
        "action": "unknown",
        "message": "",
        "updater": "doc_updater",
        "decision_status": decision_status,
        **(normalized.get("doc_sync") if isinstance(normalized.get("doc_sync"), dict) else {}),
    }
    normalized["pr_comment"] = {
        "status": "WARN",
        "comment_created": False,
        "error": "",
        **(normalized.get("pr_comment") if isinstance(normalized.get("pr_comment"), dict) else {}),
    }
    return normalized


def _parse_json_object(value: str | dict | None) -> dict:
    # author: xxrin
    # 기존 PATCH와 신규 전용 endpoint가 같은 result payload 형식을 쓰도록 안전하게 dict로 맞춘다.
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"raw_result": value}
    except Exception:
        return {"raw_result": value}


def _can_review_dev_gap(current_user: Optional[User]) -> tuple[bool, str]:
    # author: xxrin
    # Dev GAP 승인은 PM 의사결정이므로 전용 endpoint와 기존 PATCH 모두 같은 권한 기준을 적용한다.
    if not current_user:
        return False, "Authentication required for Dev GAP approval"
    if getattr(current_user, "role", "") not in {"pm", "admin"}:
        return False, "PM or admin role required for Dev GAP approval"
    if not getattr(current_user, "team_id", None):
        return False, "Reviewer must belong to a team"
    return True, ""


def _dev_gap_task_owned(task_id: str, current_user: User) -> bool:
    from pipeline.domain.agile.task_coordinator import get_task

    task = get_task(task_id)
    return bool(task and task.get("team_id") == current_user.team_id)


def _dev_gap_item_owned(shared_db: Session, gap_item_id: str, current_user: User) -> bool:
    from auth.shared_models import DevGapItem, DevPrAnalysis

    gap = shared_db.query(DevGapItem).filter(DevGapItem.id == gap_item_id).first()
    if not gap:
        return False
    analysis = shared_db.query(DevPrAnalysis).filter(DevPrAnalysis.id == gap.analysis_id).first()
    if not analysis or analysis.team_id != current_user.team_id:
        return False
    return not analysis.task_id or _dev_gap_task_owned(analysis.task_id, current_user)


def _create_sa_review_task(
    source_task: dict,
    reviewed_by: str,
    result_payload: dict | None = None,
) -> dict:
    # author: xxrin
    # PM이 비의도 변경으로 거절한 GAP은 개발자 수정 전에 SA가 설계/스펙 영향도를 재검토할 수 있도록 별도 task로 분리한다.
    from pipeline.domain.agile.task_coordinator import create_task

    payload = source_task.get("payload", {}) if isinstance(source_task.get("payload"), dict) else {}
    result_payload = dict(result_payload or {})
    pr_context = payload.get("pr_context", {}) if isinstance(payload.get("pr_context"), dict) else {}
    rejected_gaps = result_payload.get("rejected_gaps")
    if not isinstance(rejected_gaps, list) or not rejected_gaps:
        rejected_gaps = payload.get("gap_report", []) if isinstance(payload.get("gap_report"), list) else []

    reason = str(result_payload.get("reason") or result_payload.get("message") or "").strip()
    pr_number = pr_context.get("pr_number") or source_task.get("pr_number") or ""
    review_task = create_task(
        task_type="sa_re_review",
        title=f"PR #{pr_number} SA GAP 재검토",
        description=reason or "PM rejected a Dev Tracking GAP and requested SA review.",
        area="sa",
        payload={
            "source": "dev_tracking_pm_rejection",
            "parent_task_id": source_task.get("id") or "",
            "approval_status": "REJECTED_UNINTENTIONAL_CHANGE",
            "requested_by": reviewed_by,
            "requested_reason": reason,
            "pr_context": pr_context,
            "pm_report": payload.get("pm_report") or {},
            "gap_report": rejected_gaps,
            "original_gap_report": payload.get("gap_report") or [],
            "intent_classification": payload.get("intent_classification") or [],
            "milestone_status": payload.get("milestone_status") or {},
            "source_dir": payload.get("source_dir") or "",
            "spec_outdated": bool(payload.get("spec_outdated")),
            "approved_spec_version_lock": str(result_payload.get("approved_spec_version_lock") or ""),
            "result": result_payload,
        },
        created_by=reviewed_by,
        team_id=str(source_task.get("team_id") or payload.get("team_id") or ""),
        status="unassigned",
        pr_number=pr_number,
        analysis_id=str(result_payload.get("analysis_id") or source_task.get("analysis_id") or ""),
    )
    return {
        "created": True,
        "task": review_task,
        "task_id": review_task.get("id"),
        "status": review_task.get("status"),
        "task_type": review_task.get("task_type"),
    }


def _run_dev_gap_decision(
    task_id: str,
    decision_status: str,
    reviewed_by: str,
    result_payload: dict | None = None,
) -> dict:
    # author: xxrin
    # Dev GAP 승인/거절의 상태 변경, status check, 후속 doc/RAG/PR 처리를 한 곳에서 수행한다.
    import json as _json
    from pipeline.domain.agile.task_coordinator import (
        get_task,
        update_task_status,
        execute_approved_task,
        init_tasks_db,
    )

    init_tasks_db()
    existing_task = get_task(task_id)
    if not existing_task:
        return {"status": "error", "error": "Task not found"}
    if existing_task.get("task_type") != "dev_gap_approval":
        return {"status": "error", "error": "Task is not a Dev GAP approval task"}

    payload = existing_task.get("payload", {}) if isinstance(existing_task.get("payload"), dict) else {}
    result_payload = dict(result_payload or {})

    if decision_status == "APPROVED_INTENTIONAL_CHANGE":
        seed_result = {
            "approval_status": "APPROVED_INTENTIONAL_CHANGE",
            **result_payload,
        }
        task = update_task_status(
            task_id,
            "in_progress",
            reviewed_by,
            _json.dumps(seed_result, ensure_ascii=False),
        )
        if not task:
            return {"status": "error", "error": "Task not found"}
        exec_result = execute_approved_task(task)
        try:
            exec_payload = _json.loads(exec_result)
        except Exception:
            exec_payload = {"raw_result": exec_result}
        exec_payload = {**seed_result, **exec_payload}
        try:
            from pipeline.domain.dev_tracking.nodes import update_pr_status_check

            status_check = update_pr_status_check(
                {"pr_context": task.get("payload", {}).get("pr_context", {})},
                "success",
                "PM approved the intentional implementation change.",
            )
        except Exception as status_error:
            status_check = {
                "status": "WARN",
                "status_updated": False,
                "error": str(status_error) or type(status_error).__name__,
            }
        status_check = _normalize_status_check(status_check)
        try:
            from pipeline.domain.dev_tracking.nodes import run_dev_gap_decision_followup

            followup = run_dev_gap_decision_followup(
                task,
                "APPROVED_INTENTIONAL_CHANGE",
                reviewed_by,
                exec_payload,
            )
        except Exception as followup_error:
            followup = {
                "status": "WARN",
                "error": str(followup_error) or type(followup_error).__name__,
            }
        followup = _normalize_followup(
            followup,
            decision_status="APPROVED_INTENTIONAL_CHANGE",
        )
        exec_payload["status_check"] = status_check
        exec_payload["followup"] = followup
        updated = update_task_status(
            task_id,
            "completed",
            reviewed_by=reviewed_by,
            result=_json.dumps(exec_payload, ensure_ascii=False),
        )
        return {"status": "ok", "data": updated or task}

    if decision_status == "REJECTED_UNINTENTIONAL_CHANGE":
        reject_payload = {
            "message": "Dev Tracking GAP report rejected by PM.",
            "approval_status": "REJECTED_UNINTENTIONAL_CHANGE",
            "pr_context": payload.get("pr_context", {}),
            "recommended_actions": ["REQUEST_FIX"],
            **result_payload,
        }
        task = update_task_status(
            task_id,
            "rejected",
            reviewed_by,
            _json.dumps(reject_payload, ensure_ascii=False),
        )
        if not task:
            return {"status": "error", "error": "Task not found"}
        try:
            from pipeline.domain.dev_tracking.nodes import update_pr_status_check

            status_check = update_pr_status_check(
                {"pr_context": payload.get("pr_context", {})},
                "failure",
                "PM rejected the implementation change.",
            )
        except Exception as status_error:
            status_check = {
                "status": "WARN",
                "status_updated": False,
                "error": str(status_error) or type(status_error).__name__,
            }
        status_check = _normalize_status_check(status_check)
        try:
            from pipeline.domain.dev_tracking.nodes import run_dev_gap_decision_followup

            followup = run_dev_gap_decision_followup(
                task,
                "REJECTED_UNINTENTIONAL_CHANGE",
                reviewed_by,
                reject_payload,
            )
        except Exception as followup_error:
            followup = {
                "status": "WARN",
                "error": str(followup_error) or type(followup_error).__name__,
            }
        followup = _normalize_followup(
            followup,
            decision_status="REJECTED_UNINTENTIONAL_CHANGE",
        )
        try:
            sa_review_task = _create_sa_review_task(task, reviewed_by, reject_payload)
        except Exception as sa_review_error:
            sa_review_task = {
                "created": False,
                "error": str(sa_review_error) or type(sa_review_error).__name__,
            }
        reject_payload["status_check"] = status_check
        reject_payload["followup"] = followup
        reject_payload["sa_review_task"] = sa_review_task
        updated = update_task_status(
            task_id,
            "rejected",
            reviewed_by=reviewed_by,
            result=_json.dumps(reject_payload, ensure_ascii=False),
        )
        return {"status": "ok", "data": updated or task}

    return {"status": "error", "error": "Unsupported Dev GAP decision"}


class DocSyncRequest(BaseModel):
    result_data: dict
    github_token: str
    owner: str
    repo: str
    previous_hash: str = ""
    page_title: str = "SA 설계 문서"
    project_name: str = "Project"


class GitHubIssuesImportRequest(BaseModel):
    token: str
    owner: str
    repo: str
    api_key: str = ""
    model: str = DEFAULT_MODEL


class DevTrackingRequest(BaseModel):
    # author:xxrin
    # navi_v3 PR 기반 Dev Tracking MVP endpoint의 요청 형식.
    trigger: str = "GITHUB_PR_WEBHOOK"
    repository: dict
    pull_request: dict
    actor: dict = Field(default_factory=dict)
    source_dir: Optional[str] = None
    github_token: Optional[str] = None
    auth_token: str = ""
    api_key: str = ""
    model: str = DEFAULT_MODEL
    # author:xxrin
    # None은 api_key 기반 기본 동작을 유지하고, false는 해당 노드 LLM 호출을 명시적으로 끈다.
    use_llm_forensic_profiler: Optional[bool] = None
    use_llm_gap_analyzer: Optional[bool] = None
    use_llm_intent_classifier: Optional[bool] = None
    compress_prompt: bool = False
    notify_pr: bool = False
    team_id: Optional[str] = None
    created_by: str = ""


class DevKnowledgeQueryRequest(BaseModel):
    team_id: str = ""
    owner: str = ""
    repo: str = ""
    branch_name: str = ""
    artifact_type: str = ""
    query: str = ""
    limit: int = Field(default=10, ge=1, le=50)


def _safe_json_loads(value, fallback):
    # author: xxrin
    # Dev Tracking 이력 테이블의 JSON 문자열 필드를 UI 응답용 dict/list로 안전하게 복원한다.
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _serialize_dev_pr_analysis(row) -> dict:
    # author: xxrin
    # webhook과 수동 실행으로 저장된 PR 분석 이력을 DevTrackingTab에서 쓰기 쉬운 형태로 압축한다.
    pm_report = _safe_json_loads(getattr(row, "pm_report", ""), {})
    timeline = _safe_json_loads(getattr(row, "timeline", ""), [])
    gap_items = list(getattr(row, "gap_items", []) or [])
    return {
        "id": getattr(row, "id", ""),
        "project_id": getattr(row, "project_id", ""),
        "team_id": getattr(row, "team_id", ""),
        "owner": getattr(row, "owner", ""),
        "repo": getattr(row, "repo", ""),
        "pr_number": getattr(row, "pr_number", 0),
        "branch_name": getattr(row, "branch_name", ""),
        "base_branch": getattr(row, "base_branch", ""),
        "head_sha": getattr(row, "head_sha", ""),
        "branch_created_at": getattr(row, "branch_created_at", ""),
        "source_dir": getattr(row, "source_dir", ""),
        "spec_snapshot_id": getattr(row, "spec_snapshot_id", ""),
        "spec_outdated": bool(getattr(row, "spec_outdated", False)),
        "approval_status": getattr(row, "approval_status", ""),
        "analysis_status": getattr(row, "analysis_status", ""),
        "task_id": getattr(row, "task_id", ""),
        "gap_count": getattr(row, "gap_count", None) if getattr(row, "gap_count", None) is not None else len(gap_items),
        "has_high_gap": bool(getattr(row, "has_high_gap", False)),
        "high_gap_count": sum(1 for gap in gap_items if str(getattr(gap, "severity", "")).upper() == "HIGH"),
        "pm_report_summary": pm_report.get("summary", "") if isinstance(pm_report, dict) else "",
        "timeline": timeline if isinstance(timeline, list) else [],
        "created_at": getattr(row, "created_at", None).isoformat() if getattr(row, "created_at", None) else "",
        "updated_at": getattr(row, "updated_at", None).isoformat() if getattr(row, "updated_at", None) else "",
    }


def _serialize_dev_pr_analysis_detail(row) -> dict:
    # author: xxrin
    # 분석 상세 화면에서 GAP 항목까지 보여줄 수 있도록 list 응답보다 깊은 payload를 만든다.
    data = _serialize_dev_pr_analysis(row)
    gap_items = list(getattr(row, "gap_items", []) or [])
    data["gap_items"] = [
        {
            "id": getattr(gap, "id", ""),
            "gap_id": getattr(gap, "gap_id", ""),
            "severity": getattr(gap, "severity", ""),
            "type": getattr(gap, "type", ""),
            "spec_target": getattr(gap, "spec_target", ""),
            "implementation_target": getattr(gap, "implementation_target", ""),
            "spec_outdated_related": bool(getattr(gap, "spec_outdated_related", False)),
            "intent": getattr(gap, "intent", ""),
            "confidence": getattr(gap, "confidence", None),
            "recommended_action": getattr(gap, "recommended_action", ""),
            "approval_status": getattr(gap, "approval_status", ""),
            "approved_by": getattr(gap, "approved_by", ""),
            "approved_at": getattr(gap, "approved_at", ""),
            "description": getattr(gap, "description", ""),
            "created_at": getattr(gap, "created_at", None).isoformat() if getattr(gap, "created_at", None) else "",
        }
        for gap in gap_items
    ]
    data["pm_report"] = _safe_json_loads(getattr(row, "pm_report", ""), {})
    return data


def _serialize_dev_gap_item(gap) -> dict:
    return {
        "id": getattr(gap, "id", ""),
        "analysis_id": getattr(gap, "analysis_id", ""),
        "gap_id": getattr(gap, "gap_id", ""),
        "severity": getattr(gap, "severity", ""),
        "type": getattr(gap, "type", ""),
        "spec_target": getattr(gap, "spec_target", ""),
        "implementation_target": getattr(gap, "implementation_target", ""),
        "spec_outdated_related": bool(getattr(gap, "spec_outdated_related", False)),
        "intent": getattr(gap, "intent", ""),
        "confidence": getattr(gap, "confidence", None),
        "recommended_action": getattr(gap, "recommended_action", ""),
        "approval_status": getattr(gap, "approval_status", ""),
        "approved_by": getattr(gap, "approved_by", ""),
        "approved_at": getattr(gap, "approved_at", ""),
        "description": getattr(gap, "description", ""),
        "created_at": getattr(gap, "created_at", None).isoformat() if getattr(gap, "created_at", None) else "",
    }


def _aggregate_gap_decision_status(gap_items: list) -> str:
    statuses = {str(getattr(gap, "approval_status", "") or "") for gap in gap_items}
    if "REJECTED_UNINTENTIONAL_CHANGE" in statuses:
        return "REJECTED_UNINTENTIONAL_CHANGE"
    if gap_items and statuses <= {"APPROVED_INTENTIONAL_CHANGE"}:
        return "APPROVED_INTENTIONAL_CHANGE"
    return "PENDING_PM_APPROVAL"


def _update_dev_gap_item_decision(
    shared_db: Session,
    gap_item_id: str,
    decision_status: str,
    reviewed_by: str,
    reason: str = "",
) -> dict:
    from auth.shared_models import DevGapItem, DevPrAnalysis, ensure_dev_tracking_schema

    ensure_dev_tracking_schema(shared_db)
    gap = shared_db.query(DevGapItem).filter(DevGapItem.id == gap_item_id).first()
    if not gap:
        return {"status": "error", "error": "Dev GAP item not found"}

    # author: xxrin
    # GAP 항목별 PM 판단을 저장하고 같은 analysis의 전체 승인 상태를 항목 상태에서 재계산한다.
    gap.approval_status = decision_status
    gap.approved_by = reviewed_by
    gap.approved_at = datetime.utcnow().isoformat()
    if reason:
        prefix = f"[PM decision reason] {reason}"
        gap.description = f"{gap.description}\n\n{prefix}" if gap.description else prefix

    analysis = shared_db.query(DevPrAnalysis).filter(DevPrAnalysis.id == gap.analysis_id).first()
    aggregate_status = ""
    task_decision: dict = {}
    if analysis:
        gap_items = list(analysis.gap_items or [])
        aggregate_status = _aggregate_gap_decision_status(gap_items)
        analysis.approval_status = aggregate_status
        analysis.gap_count = len(gap_items)
        analysis.has_high_gap = any(str(getattr(item, "severity", "")).upper() == "HIGH" for item in gap_items)
        if hasattr(analysis, "updated_at"):
            analysis.updated_at = datetime.utcnow()
    shared_db.commit()

    if analysis and getattr(analysis, "task_id", "") and aggregate_status in {
        "APPROVED_INTENTIONAL_CHANGE",
        "REJECTED_UNINTENTIONAL_CHANGE",
    }:
        gap_items = list(analysis.gap_items or [])
        approved_gaps = [
            _serialize_dev_gap_item(item)
            for item in gap_items
            if str(getattr(item, "approval_status", "")) == "APPROVED_INTENTIONAL_CHANGE"
        ]
        rejected_gaps = [
            _serialize_dev_gap_item(item)
            for item in gap_items
            if str(getattr(item, "approval_status", "")) == "REJECTED_UNINTENTIONAL_CHANGE"
        ]
        # author: xxrin
        # 모든 GAP 항목 판단이 끝난 경우에만 task 단위 후속 처리(doc_updater/status/comment)로 승격한다.
        task_decision = _run_dev_gap_decision(
            str(getattr(analysis, "task_id", "")),
            aggregate_status,
            reviewed_by,
            {
                "reason": reason,
                "approved_gaps": approved_gaps,
                "rejected_gaps": rejected_gaps,
                "analysis_id": getattr(analysis, "id", ""),
                "approval_status": aggregate_status,
            },
        )

    return {
        "status": "ok",
        "data": {
            "gap_item": _serialize_dev_gap_item(gap),
            "analysis": _serialize_dev_pr_analysis_detail(analysis) if analysis else {},
            "analysis_approval_status": getattr(analysis, "approval_status", "") if analysis else "",
            "task_decision": task_decision,
        },
    }


def _verify_github_webhook_signature(
    raw_body: bytes,
    signature_header: str,
    secret: str,
) -> tuple[bool, str]:
    if not secret:
        return False, "webhook secret is not configured"
    if not signature_header:
        return False, "missing X-Hub-Signature-256"
    if not signature_header.startswith("sha256="):
        return False, "invalid X-Hub-Signature-256 format"

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        return False, "invalid X-Hub-Signature-256"
    return True, ""


def _normalize_github_pr_webhook(payload: dict) -> dict:
    repository = payload.get("repository") if isinstance(payload.get("repository"), dict) else {}
    pull_request = payload.get("pull_request") if isinstance(payload.get("pull_request"), dict) else {}
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    owner_obj = repository.get("owner") if isinstance(repository.get("owner"), dict) else {}
    head = pull_request.get("head") if isinstance(pull_request.get("head"), dict) else {}
    base = pull_request.get("base") if isinstance(pull_request.get("base"), dict) else {}

    return {
        "trigger": "GITHUB_PR_WEBHOOK",
        "repository": {
            "owner": owner_obj.get("login") or repository.get("owner") or "",
            "repo": repository.get("name") or "",
        },
        "pull_request": {
            "pr_number": pull_request.get("number") or payload.get("number"),
            "branch_name": head.get("ref") or "",
            "base_branch": base.get("ref") or "",
            "head_sha": head.get("sha") or "",
            "created_at": pull_request.get("created_at") or "",
            "title": pull_request.get("title") or "",
            "description": pull_request.get("body") or "",
        },
        "actor": {
            "github_id": sender.get("login") or "",
            "role": "developer",
        },
    }


def _role_is_developer_plus(role: str) -> bool:
    return role in {"pm", "software_engineer", "backend", "frontend", "devops", "developer", "admin"}


def _resolve_github_webhook_actor(normalized: dict, shared_db: Session | None) -> tuple[bool, dict]:
    # author: xxrin
    # GitHub webhook sender를 shared.db 사용자와 매핑해 내부 RBAC 기준으로 developer+ 여부를 검증한다.
    # webhook에는 JWT가 없으므로 GitHub login/id와 가입 사용자 정보를 연결하는 방식으로 권한을 판단한다.
    actor = normalized.get("actor") if isinstance(normalized.get("actor"), dict) else {}
    github_actor = str(actor.get("github_id") or "").strip()
    if not github_actor:
        return False, {"error": "GitHub webhook actor is missing"}
    if shared_db is None or not hasattr(shared_db, "query"):
        return False, {"error": "shared_db is required for GitHub webhook RBAC"}

    try:
        from auth.shared_models import User as SharedUser

        matched_user = None
        for field_name in ("github_login", "github_username", "github_id"):
            field = getattr(SharedUser, field_name)
            matched_user = shared_db.query(SharedUser).filter(field == github_actor).first()
            if matched_user is not None:
                break
        if matched_user is None:
            return False, {
                "error": f"GitHub actor '{github_actor}' is not linked to a NAVIGATOR user",
                "github_actor": github_actor,
            }

        role = str(getattr(matched_user, "role", "") or "")
        if not _role_is_developer_plus(role):
            return False, {
                "error": f"GitHub actor '{github_actor}' does not have developer+ role",
                "github_actor": github_actor,
                "role": role,
            }

        return True, {
            "github_actor": github_actor,
            "user_id": str(getattr(matched_user, "id", "") or ""),
            "team_id": str(getattr(matched_user, "team_id", "") or ""),
            "role": role,
        }
    except Exception as exc:
        return False, {
            "error": f"GitHub webhook RBAC check failed: {str(exc) or type(exc).__name__}",
            "github_actor": github_actor,
        }


@rest_router.post("/api/tasks")
async def create_task_endpoint(req: TaskCreateRequest, current_user: User = Depends(get_current_user)):
    """새 태스크 생성 (PM 승인 대기)."""
    _require_team(current_user, req.team_id)
    try:
        from pipeline.domain.agile.task_coordinator import create_task, init_tasks_db
        init_tasks_db()
        task = create_task(
            task_type=req.task_type,
            title=req.title,
            description=req.description,
            area=req.area,
            assignee=req.assignee,
            payload=req.payload,
            created_by=current_user.id,
            team_id=current_user.team_id,
        )
        return {"status": "ok", "data": task}
    except Exception as e:
        get_logger().exception("create_task endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/webhook/github")
async def github_webhook_endpoint(
    request: Request,
    shared_db: Session = Depends(get_shared_db),
):
    """GitHub PR webhook adapter for Dev Tracking."""
    try:
        from pipeline.domain.dev_tracking import run_dev_tracking_analysis

        raw_body = await request.body()
        event = request.headers.get("X-GitHub-Event", "")
        signature = request.headers.get("X-Hub-Signature-256", "")
        secret = os.environ.get("NAVIGATOR_GITHUB_WEBHOOK_SECRET", "")

        verified, verification_error = _verify_github_webhook_signature(raw_body, signature, secret)
        if not verified:
            return {
                "status": "error",
                "error": verification_error,
                "handled": False,
                "signature_verified": False,
            }

        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}")
        except Exception:
            return {"status": "error", "error": "Invalid JSON payload", "handled": False}

        if event != "pull_request":
            return {
                "status": "ok",
                "handled": False,
                "reason": f"ignored event: {event or 'unknown'}",
                "signature_verified": bool(secret),
                "signature_warning": verification_error if not secret else "",
            }

        action = str(payload.get("action") or "")
        if action not in {"opened", "synchronize", "reopened"}:
            return {
                "status": "ok",
                "handled": False,
                "reason": f"ignored pull_request action: {action or 'unknown'}",
                "signature_verified": bool(secret),
                "signature_warning": verification_error if not secret else "",
            }

        normalized = _normalize_github_pr_webhook(payload)
        rbac_allowed, actor_meta = _resolve_github_webhook_actor(normalized, shared_db)
        if not rbac_allowed:
            return {
                "status": "error",
                "handled": False,
                "error": actor_meta.get("error", "GitHub webhook actor is not allowed"),
                "github_actor": actor_meta.get("github_actor", ""),
                "role": actor_meta.get("role", ""),
                "signature_verified": bool(secret),
                "signature_warning": verification_error if not secret else "",
            }
        normalized["actor"] = {
            **(normalized.get("actor") if isinstance(normalized.get("actor"), dict) else {}),
            **actor_meta,
        }
        # author: xxrin
        # 무엇: 동일 PR head_sha가 이미 분석된 경우 webhook 재진입을 차단한다.
        # 왜: 중복 분석으로 동일 승인 태스크/코멘트가 반복 생성되는 것을 방지하기 위해서다.
        try:
            from auth.shared_models import DevPrAnalysis

            pr_ctx = normalized.get("pull_request", {}) if isinstance(normalized, dict) else {}
            repo_ctx = normalized.get("repository", {}) if isinstance(normalized, dict) else {}
            head_sha = str(pr_ctx.get("head_sha") or "").strip()
            owner = str(repo_ctx.get("owner") or "").strip()
            repo = str(repo_ctx.get("repo") or "").strip()
            pr_number = int(pr_ctx.get("pr_number") or 0)
            duplicate = None
            if head_sha and owner and repo and pr_number > 0:
                duplicate = (
                    shared_db.query(DevPrAnalysis)
                    .filter(DevPrAnalysis.owner == owner)
                    .filter(DevPrAnalysis.repo == repo)
                    .filter(DevPrAnalysis.pr_number == pr_number)
                    .filter(DevPrAnalysis.head_sha == head_sha)
                    .order_by(DevPrAnalysis.created_at.desc())
                    .first()
                )
            if duplicate is not None:
                return {
                    "status": "ok",
                    "handled": False,
                    "reason": "duplicate head_sha already analyzed",
                    "signature_verified": bool(secret),
                    "signature_warning": verification_error if not secret else "",
                }
        except Exception:
            # duplicate check 실패는 webhook 처리 자체를 막지 않는다.
            pass

        normalized.update(
            {
                "source_dir": "",
                "github_oauth_token": os.environ.get("NAVIGATOR_GITHUB_TOKEN")
                or os.environ.get("GITHUB_TOKEN")
                or "",
                "notify_pr": True,
                "team_id": os.environ.get("NAVIGATOR_DEFAULT_TEAM_ID", "") or actor_meta.get("team_id", ""),
                "created_by": actor_meta.get("user_id", "") or normalized.get("actor", {}).get("github_id", ""),
            }
        )
        result = run_dev_tracking_analysis(normalized, shared_db=shared_db)
        return {
            "status": "ok",
            "handled": True,
            "signature_verified": bool(secret),
            "signature_warning": verification_error if not secret else "",
            "data": result,
        }
    except Exception as e:
        get_logger().exception("github_webhook_endpoint failed")
        return {"status": "error", "error": str(e), "handled": False}


@rest_router.post("/api/dev-tracking/pr")
async def dev_tracking_pr_endpoint(
    req: DevTrackingRequest,
    current_user: User = Depends(get_current_user),
    shared_db: Session = Depends(get_shared_db),
    auth_token: str = Depends(_extract_token),
):
    """PR 기반 Dev Tracking MVP 실행"""
    _require_team(current_user, req.team_id or current_user.team_id)
    active_jwt_token.set(auth_token)
    try:
        # author:xxrin
        # 이 endpoint는 분리된 dev_tracking service를 호출하는 어댑터 역할만 함
        from pipeline.domain.dev_tracking import run_dev_tracking_analysis

        github_token = req.github_token
        if not github_token:
            github_token = current_user.github_oauth_token
        #payload
        payload = {
            "trigger": req.trigger,
            "repository": req.repository,
            "pull_request": req.pull_request,
            "actor": req.actor,
            "source_dir": req.source_dir or "",
            "github_oauth_token": github_token or "",
            "api_key": req.api_key,
            "model": req.model or DEFAULT_MODEL,
            "use_llm_forensic_profiler": req.use_llm_forensic_profiler,
            "use_llm_gap_analyzer": req.use_llm_gap_analyzer,
            "use_llm_intent_classifier": req.use_llm_intent_classifier,
            "compress_prompt": req.compress_prompt,
            "notify_pr": req.notify_pr,
            "team_id": current_user.team_id,
            "created_by": current_user.id,
        }
        result = run_dev_tracking_analysis(payload, shared_db=shared_db)
        return {"status": "ok", "data": result}
    except Exception as e:
        get_logger().exception("dev_tracking_pr_endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/dev-tracking/knowledge/query")
async def dev_tracking_knowledge_query_endpoint(
    req: DevKnowledgeQueryRequest,
    shared_db: Session = Depends(get_shared_db),
):
    """Dev Tracking 지식 아티팩트를 조회해 PM/SA 프롬프트 컨텍스트로 반환한다."""
    try:
        from pipeline.domain.dev_tracking.knowledge import query_dev_knowledge_artifacts

        result = query_dev_knowledge_artifacts(
            shared_db,
            team_id=req.team_id,
            owner=req.owner,
            repo=req.repo,
            branch_name=req.branch_name,
            artifact_type=req.artifact_type,
            query=req.query,
            limit=req.limit,
        )
        return {"status": "ok", "data": result}
    except Exception as e:
        get_logger().exception("dev_tracking_knowledge_query_endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.get("/api/dev-tracking/analyses")
async def dev_tracking_analyses_endpoint(
    team_id: Optional[str] = None,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    pr_number: Optional[int] = None,
    limit: int = 20,
    shared_db: Session = Depends(get_shared_db),
):
    """저장된 Dev Tracking PR 분석 이력을 조회한다."""
    try:
        from auth.shared_models import DevGapItem, DevPrAnalysis, ensure_dev_tracking_schema

        # author: xxrin
        # 운영 UI가 webhook으로 들어온 분석도 볼 수 있도록 shared.db의 분석 이력을 읽는다.
        ensure_dev_tracking_schema(shared_db)
        query = shared_db.query(DevPrAnalysis)
        if team_id:
            query = query.filter(DevPrAnalysis.team_id == team_id)
        if owner:
            query = query.filter(DevPrAnalysis.owner == owner)
        if repo:
            query = query.filter(DevPrAnalysis.repo == repo)
        if pr_number:
            query = query.filter(DevPrAnalysis.pr_number == pr_number)
        safe_limit = max(1, min(int(limit or 20), 100))
        rows = query.order_by(DevPrAnalysis.created_at.desc()).limit(safe_limit).all()
        return {
            "status": "ok",
            "data": [_serialize_dev_pr_analysis(row) for row in rows],
        }
    except Exception as e:
        get_logger().exception("dev_tracking_analyses_endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.get("/api/dev-tracking/analyses/{analysis_id}")
async def dev_tracking_analysis_detail_endpoint(
    analysis_id: str,
    shared_db: Session = Depends(get_shared_db),
):
    """저장된 Dev Tracking PR 분석 상세를 조회한다."""
    try:
        from auth.shared_models import DevGapItem, DevPrAnalysis, ensure_dev_tracking_schema

        # author: xxrin
        # 운영 UI에서 저장된 GAP 상세를 다시 열람할 수 있도록 단건 상세 조회를 제공한다.
        ensure_dev_tracking_schema(shared_db)
        row = shared_db.query(DevPrAnalysis).filter(DevPrAnalysis.id == analysis_id).first()
        if not row:
            return {"status": "error", "error": "Dev Tracking analysis not found"}
        return {"status": "ok", "data": _serialize_dev_pr_analysis_detail(row)}
    except Exception as e:
        get_logger().exception("dev_tracking_analysis_detail_endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/dev-tracking/gaps/{gap_item_id}/approve")
async def dev_gap_item_approve_endpoint(
    gap_item_id: str,
    req: DevGapDecisionRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
    shared_db: Session = Depends(get_shared_db),
):
    """Dev GAP 항목 단위 승인 endpoint."""
    allowed, error = _can_review_dev_gap(current_user)
    if not allowed:
        return {"status": "error", "error": error}
    if not _dev_gap_item_owned(shared_db, gap_item_id, current_user):
        raise HTTPException(status_code=403, detail="현재 팀의 GAP만 검토할 수 있습니다.")
    reviewed_by = str(getattr(current_user, "id", "") or "")
    return _update_dev_gap_item_decision(
        shared_db,
        gap_item_id,
        "APPROVED_INTENTIONAL_CHANGE",
        reviewed_by,
        req.reason,
    )


@rest_router.post("/api/dev-tracking/gaps/{gap_item_id}/reject")
async def dev_gap_item_reject_endpoint(
    gap_item_id: str,
    req: DevGapDecisionRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
    shared_db: Session = Depends(get_shared_db),
):
    """Dev GAP 항목 단위 거절 endpoint."""
    allowed, error = _can_review_dev_gap(current_user)
    if not allowed:
        return {"status": "error", "error": error}
    if not _dev_gap_item_owned(shared_db, gap_item_id, current_user):
        raise HTTPException(status_code=403, detail="현재 팀의 GAP만 검토할 수 있습니다.")
    reviewed_by = str(getattr(current_user, "id", "") or "")
    return _update_dev_gap_item_decision(
        shared_db,
        gap_item_id,
        "REJECTED_UNINTENTIONAL_CHANGE",
        reviewed_by,
        req.reason,
    )


@rest_router.post("/api/dev-tracking/tasks/{task_id}/approve")
async def dev_gap_approve_endpoint(
    task_id: str,
    req: DevGapDecisionRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Dev GAP 승인 전용 endpoint."""
    allowed, error = _can_review_dev_gap(current_user)
    if not allowed:
        return {"status": "error", "error": error}
    if not _dev_gap_task_owned(task_id, current_user):
        raise HTTPException(status_code=403, detail="현재 팀의 Dev GAP 태스크만 검토할 수 있습니다.")
    reviewed_by = str(getattr(current_user, "id", "") or "")
    # author: xxrin
    # 전용 승인 API는 PATCH status 재해석 없이 명시적인 승인 상태를 helper에 전달한다.
    return _run_dev_gap_decision(
        task_id,
        "APPROVED_INTENTIONAL_CHANGE",
        reviewed_by,
        {
            "approval_status": "APPROVED_INTENTIONAL_CHANGE",
            "reason": req.reason,
            **(req.result or {}),
        },
    )


@rest_router.post("/api/dev-tracking/tasks/{task_id}/reject")
async def dev_gap_reject_endpoint(
    task_id: str,
    req: DevGapDecisionRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Dev GAP 거절 전용 endpoint."""
    allowed, error = _can_review_dev_gap(current_user)
    if not allowed:
        return {"status": "error", "error": error}
    if not _dev_gap_task_owned(task_id, current_user):
        raise HTTPException(status_code=403, detail="현재 팀의 Dev GAP 태스크만 검토할 수 있습니다.")
    reviewed_by = str(getattr(current_user, "id", "") or "")
    # author: xxrin
    # 전용 거절 API는 PM 판단 사유를 명시적으로 후속 처리 payload에 남긴다.
    return _run_dev_gap_decision(
        task_id,
        "REJECTED_UNINTENTIONAL_CHANGE",
        reviewed_by,
        {
            "approval_status": "REJECTED_UNINTENTIONAL_CHANGE",
            "reason": req.reason,
            **(req.result or {}),
        },
    )


@rest_router.post("/api/dev-tracking/tasks/{task_id}/sa-review")
async def dev_gap_sa_review_request_endpoint(
    task_id: str,
    req: DevGapSaReviewRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Dev GAP SA 재검토 task 생성 endpoint."""
    allowed, error = _can_review_dev_gap(current_user)
    if not allowed:
        return {"status": "error", "error": error}
    if not _dev_gap_task_owned(task_id, current_user):
        raise HTTPException(status_code=403, detail="현재 팀의 Dev GAP 태스크만 검토할 수 있습니다.")
    try:
        from pipeline.domain.agile.task_coordinator import get_task, init_tasks_db

        init_tasks_db()
        task = get_task(task_id)
        if not task:
            return {"status": "error", "error": "Task not found"}
        if task.get("task_type") != "dev_gap_approval":
            return {"status": "error", "error": "Task is not a Dev GAP approval task"}
        reviewed_by = str(getattr(current_user, "id", "") or "")
        # author: xxrin
        # PM이 즉시 거절 처리하지 않고도 SA에게 설계 재검토를 별도로 요청할 수 있도록 명시 endpoint를 둔다.
        result = _create_sa_review_task(
            task,
            reviewed_by,
            {
                "approval_status": "REJECTED_UNINTENTIONAL_CHANGE",
                "reason": req.reason,
                **(req.result or {}),
            },
        )
        return {"status": "ok", "data": result}
    except Exception as e:
        get_logger().exception("dev_gap_sa_review_request_endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.get("/api/tasks")
async def list_tasks_endpoint(
    status: Optional[str] = None,
    team_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """태스크 목록 조회."""
    _require_team(current_user, team_id or current_user.team_id)
    try:
        from pipeline.domain.agile.task_coordinator import list_tasks, init_tasks_db
        init_tasks_db()
        tasks = list_tasks(status=status, team_id=current_user.team_id)
        return {"status": "ok", "data": tasks}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@rest_router.patch("/api/tasks/{task_id}")
async def update_task_endpoint(
    task_id: str,
    req: TaskUpdateRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """태스크 상태 업데이트 (승인/거절/완료)."""
    allowed_statuses = {"unassigned", "pending_approval", "in_progress", "pr_pending", "completed", "rejected"}
    if req.status not in allowed_statuses:
        return {"status": "error", "error": f"Invalid status. Allowed: {allowed_statuses}"}
    try:
        from pipeline.domain.agile.task_coordinator import get_task, update_task_status, init_tasks_db
        init_tasks_db()
        existing_task = get_task(task_id)
        if not existing_task:
            return {"status": "error", "error": "Task not found"}
        if not current_user:
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")
        _require_team(current_user, existing_task.get("team_id", ""))

        # author: xxrin
        # 기존 PATCH 경로도 호환하되 Dev GAP 승인/거절은 전용 helper로 위임한다.
        is_dev_gap_decision = (
            existing_task.get("task_type") == "dev_gap_approval"
            and req.status in {"in_progress", "rejected"}
        )
        if is_dev_gap_decision:
            allowed, error = _can_review_dev_gap(current_user)
            if not allowed:
                return {"status": "error", "error": error}
            if not _dev_gap_task_owned(task_id, current_user):
                raise HTTPException(status_code=403, detail="현재 팀의 Dev GAP 태스크만 검토할 수 있습니다.")
            reviewed_by = str(getattr(current_user, "id", "") or "")
            decision_status = (
                "APPROVED_INTENTIONAL_CHANGE"
                if req.status == "in_progress"
                else "REJECTED_UNINTENTIONAL_CHANGE"
            )
            result_payload = _parse_json_object(req.result)
            result_payload.setdefault("approval_status", decision_status)
            return _run_dev_gap_decision(
                task_id,
                decision_status,
                reviewed_by,
                result_payload,
            )

        task = update_task_status(task_id, req.status, current_user.id, req.result)
        if not task:
            return {"status": "error", "error": "Task not found"}

        return {"status": "ok", "data": task}
    except HTTPException:
        raise
    except Exception as e:
        get_logger().exception(f"update_task endpoint failed for {task_id}")
        return {"status": "error", "error": str(e)}


@rest_router.delete("/api/tasks/{task_id}")
async def delete_task_endpoint(task_id: str, current_user: User = Depends(get_current_user)):
    """완료/거절된 태스크 삭제."""
    from pipeline.domain.agile.task_coordinator import get_task, delete_task, init_tasks_db

    init_tasks_db()
    existing = get_task(task_id)
    if not existing:
        return {"status": "error", "error": "Task not found"}
    _require_team(current_user, existing.get("team_id", ""))
    try:
        deleted = delete_task(task_id)
        if not deleted:
            return {"status": "error", "error": "Task not found"}
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        get_logger().exception(f"delete_task endpoint failed for {task_id}")
        return {"status": "error", "error": str(e)}


class GenerateTasksRequest(BaseModel):
    run_id: str
    team_id: str
    auth_token: str = ""
    api_key: str = ""
    model: str = ""
    created_by: str = ""


class DistributeTasksRequest(BaseModel):
    team_id: str
    auth_token: str = ""
    api_key: str = ""
    model: str = ""
    distributed_by: str = ""
    members: list = []


class TaskProposalApprovalRequest(BaseModel):
    token: str


def _task_proposal_secret() -> str:
    secret = os.environ.get("NAVIGATOR_JWT_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="태스크 제안 승인 비밀키가 설정되지 않았습니다.")
    return secret + ":task-proposal"


def _sign_task_proposal(proposal: dict, kind: str, run_id: str, user: User) -> str:
    from jose import jwt

    return jwt.encode({
        "purpose": "task_proposal",
        "kind": kind,
        "proposal": proposal,
        "run_id": run_id,
        "team_id": user.team_id,
        "actor_id": user.id,
        "exp": datetime.utcnow() + timedelta(minutes=30),
    }, _task_proposal_secret(), algorithm="HS256")


@rest_router.post("/api/agile/task-proposals/approve")
async def approve_task_proposal_endpoint(
    req: TaskProposalApprovalRequest,
    current_user: User = Depends(get_current_user),
):
    from jose import JWTError, jwt
    from pipeline.domain.agile.task_coordinator import create_task, apply_task_update_proposal

    if current_user.role != "pm" or not current_user.team_id:
        raise HTTPException(status_code=403, detail="현재 팀 PM 권한이 필요합니다.")
    try:
        claims = jwt.decode(req.token, _task_proposal_secret(), algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=400, detail="유효하지 않거나 만료된 제안입니다.")
    if claims.get("purpose") != "task_proposal" or claims.get("actor_id") != current_user.id:
        raise HTTPException(status_code=403, detail="이 사용자의 제안이 아닙니다.")
    _require_team(current_user, claims.get("team_id", ""))
    proposal = claims.get("proposal")
    if not isinstance(proposal, dict) or not claims.get("run_id"):
        raise HTTPException(status_code=400, detail="제안 내용이 올바르지 않습니다.")

    if claims.get("kind") == "create":
        if not proposal.get("title", "").strip() or proposal.get("task_type") not in {
            "feature", "test", "infra", "doc_sync", "bugfix", "refactor"
        }:
            raise HTTPException(status_code=400, detail="제안 태스크가 올바르지 않습니다.")
        task = create_task(
            task_type=proposal["task_type"], title=proposal["title"],
            description=proposal.get("description", ""), area=proposal.get("area", ""),
            feature_ref=proposal.get("feature_ref", ""), effort=proposal.get("effort", ""),
            team_id=current_user.team_id, created_by=current_user.id, status="unassigned",
            payload={"priority": proposal.get("priority", "medium"), "approved_from_run": claims["run_id"]},
        )
    elif claims.get("kind") == "update":
        task = apply_task_update_proposal(current_user.team_id, proposal, current_user.id)
    else:
        raise HTTPException(status_code=400, detail="알 수 없는 제안 종류입니다.")
    if not task:
        raise HTTPException(status_code=409, detail="중복되었거나 검토 이후 태스크가 변경됐습니다.")
    return {"status": "ok", "data": task}


@rest_router.post("/api/agile/generate-tasks")
async def generate_tasks_endpoint(
    req: GenerateTasksRequest, current_user: User = Depends(get_current_user),
):
    """SA/PM 산출물 → 검토할 태스크 제안."""
    _require_team(current_user, req.team_id)
    if current_user.role != "pm":
        raise HTTPException(status_code=403, detail="PM 권한이 필요합니다.")
    active_jwt_token.set(req.auth_token)
    try:
        from auth.database import SessionLocal
        from auth.models import AnalysisResult, AnalysisSession
        from pipeline.domain.agile.nodes.task_generator import run_task_generator
        from version import DEFAULT_MODEL
        import json

        db = SessionLocal()
        try:
            record = db.query(AnalysisResult).join(
                AnalysisSession, AnalysisSession.run_id == AnalysisResult.run_id
            ).filter(
                AnalysisResult.run_id == req.run_id,
                AnalysisSession.team_id == current_user.team_id,
            ).first()
            if not record:
                return {"status": "error", "error": f"run_id '{req.run_id}' 결과 없음"}
            shaped = json.loads(record.shaped_result)
        finally:
            db.close()

        sa_bundle = shaped.get("sa_arch_bundle") or {}
        pm_bundle = shaped.get("pm_bundle") or {}

        result = run_task_generator(
            sa_bundle=sa_bundle,
            pm_bundle=pm_bundle,
            team_id=req.team_id,
            api_key=req.api_key,
            model=req.model or DEFAULT_MODEL,
            created_by=current_user.id,
        )
        for item in result["task_proposals"]:
            item["approval_token"] = _sign_task_proposal(item, "create", req.run_id, current_user)
        for item in result["update_proposals"]:
            item["approval_token"] = _sign_task_proposal(item, "update", req.run_id, current_user)
        return {"status": "ok", "data": result}
    except HTTPException:
        raise
    except Exception as e:
        get_logger().exception("generate_tasks endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/agile/distribute-tasks")
async def distribute_tasks_endpoint(
    req: DistributeTasksRequest, current_user: User = Depends(get_current_user),
):
    """unassigned 태스크를 팀 멤버에게 배분 (PM이 '배분' 버튼 클릭 시 호출)."""
    _require_team(current_user, req.team_id)
    if current_user.role != "pm":
        raise HTTPException(status_code=403, detail="PM 권한이 필요합니다.")
    active_jwt_token.set(req.auth_token)
    try:
        from pipeline.domain.agile.nodes.task_distributor import run_task_distributor
        from version import DEFAULT_MODEL

        result = run_task_distributor(
            team_id=req.team_id,
            api_key=req.api_key,
            model=req.model or DEFAULT_MODEL,
            distributed_by=current_user.id,
            members=req.members or [],
        )
        return {"status": "ok", "data": result}
    except Exception as e:
        get_logger().exception("distribute_tasks endpoint failed")
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/doc-sync")
async def doc_sync_endpoint(req: DocSyncRequest, current_user: User = Depends(get_current_user)):
    """SA 결과물을 GitHub에 자동 동기화."""
    _require_team(current_user, current_user.team_id)
    try:
        from pipeline.domain.agile.nodes.doc_sync import sync_docs
        result = sync_docs(
            result_data=req.result_data,
            github_token=req.github_token,
            owner=req.owner,
            repo=req.repo,
            previous_hash=req.previous_hash,
            page_title=req.page_title,
            project_name=req.project_name,
        )
        return {"status": "ok", "data": result}
    except Exception as e:
        get_logger().exception("doc_sync endpoint failed")
        return {"status": "error", "error": str(e)}


# ── Publish / Shared Snapshots ────────────────────────────────

class PublishRequest(BaseModel):
    run_id: str
    title: str
    description: str = ""
    team_id: Optional[str] = None


class ShareArtifactRequest(BaseModel):
    run_id: str = Field(max_length=255)
    kind: Literal["components", "apis"]


@rest_router.post("/api/share-artifact")
async def share_artifact_endpoint(
    req: ShareArtifactRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    shared_db: Session = Depends(get_shared_db),
):
    _require_team(current_user, current_user.team_id)
    from storage.publish_service import publish_snapshot

    try:
        snapshot = publish_snapshot(
            db, shared_db, req.run_id,
            "컴포넌트" if req.kind == "components" else "API Spec",
            team_id=current_user.team_id, user_id=current_user.id, kind=req.kind,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": "ok", "data": snapshot}


@rest_router.get("/api/local-results")
async def list_local_results_endpoint(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    shared_db: Session = Depends(get_shared_db),
):
    """Publish 대상 선택을 위한 현재 팀 분석 결과 목록."""
    _require_team(current_user, current_user.team_id)
    try:
        from storage.publish_service import list_local_results
        return {"status": "ok", "data": list_local_results(db, shared_db, limit=limit, team_id=current_user.team_id)}
    except Exception as e:
        get_logger().exception("list_local_results failed")
        return {"status": "error", "error": str(e)}


@rest_router.post("/api/publish")
async def publish_snapshot_endpoint(
    req: PublishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    shared_db: Session = Depends(get_shared_db),
):
    """로컬 분석 결과를 공유 DB에 Publish."""
    _require_team(current_user, req.team_id or current_user.team_id)
    try:
        from storage.publish_service import publish_snapshot
        team_id = current_user.team_id
        user_id = current_user.id
        snap = publish_snapshot(
            db, shared_db,
            run_id=req.run_id,
            title=req.title,
            description=req.description,
            team_id=team_id,
            user_id=user_id,
        )
        return {"status": "ok", "data": snap}
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        get_logger().exception("publish_snapshot failed")
        return {"status": "error", "error": str(e)}


@rest_router.get("/api/snapshots")
async def list_snapshots_endpoint(
    team_id: Optional[str] = None,
    limit: int = 30,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    shared_db: Session = Depends(get_shared_db),
):
    """팀 공유 스냅샷 목록."""
    _require_team(current_user, team_id or current_user.team_id)
    try:
        from storage.publish_service import list_snapshots
        resolved_team = current_user.team_id
        snaps = list_snapshots(shared_db, team_id=resolved_team, limit=limit, offset=offset)
        return {"status": "ok", "data": snaps}
    except Exception as e:
        get_logger().exception("list_snapshots failed")
        return {"status": "error", "error": str(e)}


@rest_router.get("/api/snapshots/{snapshot_id}")
async def get_snapshot_endpoint(
    snapshot_id: str,
    current_user: User = Depends(get_current_user),
    shared_db: Session = Depends(get_shared_db),
):
    """스냅샷 상세 조회 (데이터 포함)."""
    try:
        from storage.publish_service import get_snapshot
        snap = get_snapshot(shared_db, snapshot_id)
        if not snap:
            return {"status": "error", "error": "스냅샷을 찾을 수 없습니다."}
        _require_team(current_user, snap.get("team_id"))
        return {"status": "ok", "data": snap}
    except HTTPException:
        raise
    except Exception as e:
        get_logger().exception("get_snapshot failed")
        return {"status": "error", "error": str(e)}


@rest_router.delete("/api/snapshots/{snapshot_id}")
async def delete_snapshot_endpoint(
    snapshot_id: str,
    current_user: User = Depends(get_current_user),
    shared_db: Session = Depends(get_shared_db),
):
    """스냅샷 삭제 (게시자 본인 또는 PM만)."""
    try:
        from storage.publish_service import get_snapshot, delete_snapshot
        snap = get_snapshot(shared_db, snapshot_id)
        if not snap:
            return {"status": "error", "error": "스냅샷을 찾을 수 없습니다."}
        _require_team(current_user, snap.get("team_id"))
        if snap["published_by"] != current_user.id and current_user.role != "pm":
            raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")
        delete_snapshot(shared_db, snapshot_id)
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        get_logger().exception("delete_snapshot failed")
        return {"status": "error", "error": str(e)}


class PullRequest(BaseModel):
    run_id: str


@rest_router.post("/api/snapshots/{snapshot_id}/pull")
async def pull_snapshot_endpoint(
    snapshot_id: str,
    req: PullRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    shared_db: Session = Depends(get_shared_db),
):
    """공유 스냅샷 데이터를 지정된 로컬 run_id 세션에 덮어써 저장(Pull)."""
    try:
        from storage.publish_service import pull_snapshot
        from auth.shared_models import PublishedSnapshot
        snap = shared_db.query(PublishedSnapshot).filter(PublishedSnapshot.id == snapshot_id).first()
        if not snap:
            return {"status": "error", "error": "스냅샷을 찾을 수 없습니다."}
        _require_team(current_user, snap.team_id)
        from auth.models import AnalysisSession
        target = db.query(AnalysisSession).filter(AnalysisSession.run_id == req.run_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="현재 팀의 로컬 세션이 없습니다.")
        _require_team(current_user, target.team_id)
        result = pull_snapshot(shared_db, db, snapshot_id=snapshot_id, run_id=req.run_id)
        return {"status": "ok", "data": result}
    except HTTPException:
        raise
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        get_logger().exception("pull_snapshot failed")
        return {"status": "error", "error": str(e)}



@rest_router.post("/api/github/issues/import")
async def github_issues_import(req: GitHubIssuesImportRequest):
    """GitHub Issues를 requirements로 변환하는 태스크 생성."""
    try:
        from connectors.github_connector import GitHubConnector
        from pipeline.domain.agile.task_coordinator import create_task, init_tasks_db
        init_tasks_db()
        connector = GitHubConnector(req.token)
        issues = connector.get_issues(req.owner, req.repo, state="open")
        issue_list = [i.__dict__ for i in issues]

        task = create_task(
            task_type="import_issues",
            title=f"GitHub Issues Import ({req.owner}/{req.repo})",
            description=f"{len(issue_list)}개 이슈를 요구사항으로 변환",
            payload={
                "issues": issue_list,
                "owner": req.owner,
                "repo": req.repo,
                "api_key": req.api_key,
            },
        )
        return {
            "status": "ok",
            "task_id": task["id"],
            "issue_count": len(issue_list),
            "message": "태스크가 생성되었습니다. PM 승인 후 실행됩니다.",
        }
    except Exception as e:
        get_logger().exception("github_issues_import endpoint failed")
        return {"status": "error", "error": str(e)}
