"""
PM Agent Pipeline — 유틸리티 v6.3 (FastAPI 구조 적용)

변경 사항:
- api_key 빈 문자열 시 환경변수 GEMINI_API_KEY 자동 폴백
- 모델 기본값 gemini-2.5-flash 통일
- _get_effective_key() 헬퍼 추가
- 재시도 로직에 지수 백오프 및 Jitter 추가 (Rate Limit 대응)
"""

import json, re, os, threading, time, random, urllib.request
from pathlib import Path
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Generic, Type, TypeVar
from pydantic import BaseModel, ValidationError
from observability.logger import get_logger
from pipeline.core.models.gemini_model import get_gemini_client, get_raw_genai_client
from pipeline.core.cost_manager import calculate_cost
from pipeline.core.compressor import get_compressor
from version import DEFAULT_MODEL, DEFAULT_TEMPERATURE, MAX_LLM_RETRIES
# langchain imports are deferred to first LLM call to avoid startup cost

T = TypeVar("T", bound=BaseModel)
_CACHE_LIMIT = 32
logger = get_logger()

# Pre-compiled Regex for performance
_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
_JSON_FALLBACK_RE = re.compile(r"(\{.*)", re.DOTALL)
_THINKING_RE = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL)

def create_context_cache(api_key: str, model: str, system_instruction: str, contents: list, ttl_seconds: int = 3600) -> str:
    """Vertex AI Context Cache 리소스 생성 후 cache_name 반환"""
    client = get_raw_genai_client(api_key)
    try:
        from google.genai import types
        # 32k 토큰 미만이면 생성이 실패할 수 있음 (Flash 기준)
        cache = client.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                system_instruction=system_instruction,
                contents=contents,
                ttl=f"{ttl_seconds}s",
            )
        )
        logger.info(f"[ContextCache] Resource created: {cache.name}")
        return cache.name
    except Exception as e:
        logger.warning(f"[ContextCache] Creation failed: {e}")
        return ""

# ── 비용 및 토큰 추적용 전역 컨텍스트 (Phase 0) ──────
# 각 노드 실행 중 발생하는 모든 LLM 호출의 사용량을 임시 저장합니다.
from pipeline.core.context import active_usage_log, active_session_id, active_jwt_token  # noqa: E402


@dataclass
class LLMResult(Generic[T]):
    parsed: T
    usage: dict
    cost: float
    thinking: str
    retry_count: int = 0


def _make_llm_cache_key(api_key: str, model: str, temperature: float) -> str:
    return f"{api_key}:{model}:{temperature}"


def _remember_cache_entry(cache: OrderedDict[str, Any], key: str, value: Any):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _CACHE_LIMIT:
        cache.popitem(last=False)

# ── API 키 해석 ────────────────────────────────
_CLOUD_RUN_SERVER = "https://navigator-server-640700885251.asia-northeast3.run.app"

def _fetch_key_from_server() -> str:
    """Cloud Run /keys/active 엔드포인트에서 Gemini 키를 JWT로 인증하여 가져온다."""
    server_url = os.environ.get("NAVIGATOR_SERVER_URL", _CLOUD_RUN_SERVER)
    jwt = active_jwt_token.get("")
    if not jwt:
        logger.warning("[Gemini] JWT 없음 — Cloud Run 키 fetch 스킵")
        return ""
    try:
        req = urllib.request.Request(
            f"{server_url}/keys/active",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            key = data.get("api_key", "")
            if key:
                logger.info("[Gemini] Cloud Run에서 키 fetch 성공")
            return key
    except Exception as e:
        logger.warning(f"[Gemini] Cloud Run 키 fetch 실패: {e}")
        return ""


def get_effective_key(api_key: str) -> str:
    """
    키 우선순위:
    1. 사용자가 직접 전달한 키
    2. 서버(8001) /keys/active 에서 가져온 키
    3. 백엔드 .env의 GEMINI_API_KEY 환경변수
    """
    if os.environ.get("USE_VERTEX_AI", "false").lower() == "true":
        if not os.environ.get("GCP_PROJECT_ID") or not os.environ.get("GCP_LOCATION"):
            raise ValueError("Vertex AI project와 location이 설정되지 않았습니다.")
        return "[vertex]"
    key = (api_key or "").strip()
    if key in ("", "[.env]", "[env]"):
        key = _fetch_key_from_server()
    if not key:
        key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise ValueError(
            "Gemini API Key가 설정되지 않았습니다. "
            "서버에 키를 등록하거나 백엔드 .env에 GEMINI_API_KEY를 추가해 주세요."
        )
    if not key.isascii():
        raise ValueError("API 키에 한글 등 비ASCII 문자가 포함되어 있습니다.")
    return key


def get_llm(api_key: str, model: str = DEFAULT_MODEL, temperature: float = DEFAULT_TEMPERATURE):
    """ChatGoogleGenerativeAI 위임 (core/models/gemini_model 사용)"""
    return get_gemini_client(api_key, model, temperature)


def _retry_loop(structured_llm, messages: list, max_retries: int, label: str):
    """공통 Self-Correction 및 Rate-Limit 대응 재시도 루프."""
    from langchain_core.messages import AIMessage, HumanMessage
    messages = list(messages)
    last_error = None

    for attempt in range(max_retries):
        result = None
        try:
            result = structured_llm.invoke(messages)
            return result, attempt # 결과와 시도 횟수(0-based) 반환
        except Exception as e:
            last_error = e
            error_msg = str(e)

            if attempt < max_retries - 1:
                # Rate Limit 대응 지수 백오프
                if "429" in error_msg:
                    wait_time = (10 * (attempt + 1)) + random.uniform(2, 5)
                else:
                    wait_time = (2 ** (attempt + 1)) + random.uniform(0, 1)

                logger.warning(f"[{label}] Attempt {attempt+1} failed: {error_msg[:100]}. Retrying in {wait_time:.2f}s...")
                time.sleep(wait_time)

                bad_output = str(result) if result is not None else "Unknown output format or invocation error"
                messages.append(AIMessage(content=bad_output))
                messages.append(HumanMessage(content=(
                    f"Your previous response caused an error:\n"
                    f"```\n{error_msg}\n```\n\n"
                    f"Please fix the output to strictly match the required JSON schema. "
                    f"All required fields must be present and no extra fields are allowed.\n\n"
                    f"Retry attempt {attempt + 2}/{max_retries}. "
                    f"Return ONLY the corrected JSON output."
                )))
                continue

    raise RuntimeError(
        f"{label} failed after {max_retries} attempts. Last error: {last_error}"
    )


_ZERO_USAGE: dict = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def call_structured(
    api_key: str,
    model: str,
    schema: Type[T],
    system_prompt: str,
    user_msg: str,
    max_retries: int = MAX_LLM_RETRIES,
    temperature: float = DEFAULT_TEMPERATURE,
    compress_prompt: bool = False,
    compression_rate: float = 0.5,
    context_cache: str = None # 추가
) -> LLMResult[T]:
    """Unified structured output: parsed result, usage metadata, and thinking."""
    if compress_prompt:
        user_msg = get_compressor().compress_with_preservation(user_msg, target_token_rate=compression_rate)
        logger.info(f"[PromptCompressor] Compressed with rate {compression_rate}")

    # 캐시가 있는 경우 원시 SDK 호출 (캐싱 지원)
    if context_cache:
        try:
            from google.genai import types
            client = get_raw_genai_client(api_key)
            # 구조적 출력을 위해 response_mime_type 및 response_schema 설정
            res = client.models.generate_content(
                model=model,
                contents=user_msg,
                config=types.GenerateContentConfig(
                    cached_content=context_cache,
                    temperature=temperature,
                    response_mime_type="application/json",
                    response_schema=schema.model_json_schema() if hasattr(schema, "model_json_schema") else None,
                )
            )
            parsed_dict = json.loads(res.text)
            parsed = schema.model_validate(parsed_dict)
            
            usage_meta = res.usage_metadata
            usage = {
                "input_tokens": usage_meta.prompt_token_count or 0,
                "output_tokens": usage_meta.candidates_token_count or 0,
                "total_tokens": usage_meta.total_token_count or 0,
                "cached_tokens": usage_meta.cached_content_token_count or 0
            }
            cost = calculate_cost(model, usage["input_tokens"], usage["output_tokens"])
            return LLMResult(parsed=parsed, usage=usage, cost=cost, thinking=getattr(parsed, "thinking", ""), retry_count=0)
        except Exception as e:
            logger.error(f"[ContextCache] Call failed, falling back to normal: {e}")

    from langchain_core.messages import SystemMessage, HumanMessage
    llm = get_llm(api_key, model, temperature)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_msg)]

    try:
        structured_llm = llm.with_structured_output(schema, include_raw=True)
    except TypeError:
        structured_llm = llm.with_structured_output(schema)
        parsed, retries = _retry_loop(structured_llm, messages, max_retries, "Structured output")
        return LLMResult(
            parsed=parsed,
            usage={**_ZERO_USAGE},
            thinking=getattr(parsed, "thinking", ""),
            retry_count=retries,
        )

    raw_result, retries = _retry_loop(structured_llm, messages, max_retries, "Structured output")

    if isinstance(raw_result, dict):
        parsed = raw_result.get("parsed")
        raw = raw_result.get("raw")
        parsing_error = raw_result.get("parsing_error")
        if parsing_error:
            raise RuntimeError(f"Structured parsing error: {parsing_error}")
    else:
        parsed = raw_result
        raw = None

    if parsed is None:
        raise RuntimeError("Structured output parsed result is None")

    usage_meta = getattr(raw, "usage_metadata", None) or {}
    usage = {
        "input_tokens": usage_meta.get("input_tokens", usage_meta.get("prompt_token_count", 0)) or 0,
        "output_tokens": usage_meta.get("output_tokens", usage_meta.get("candidates_token_count", 0)) or 0,
        "total_tokens": usage_meta.get("total_tokens", usage_meta.get("total_token_count", 0)) or 0,
    }
    
    cost = calculate_cost(model, usage["input_tokens"], usage["output_tokens"])

    # 중앙 집중형 추적을 위해 컨텍스트에 기록
    current_log = active_usage_log.get().copy()
    
    # 캐싱 통계 계산
    from pipeline.core.cache_manager import cache_manager
    session_id = active_session_id.get()
    cache_stats = {"cache_hit": False, "savings_usd": 0.0}
    
    if session_id and session_id in cache_manager.session_pool:
        cache_stats = cache_manager.get_cache_stats(session_id, model, usage)

    log_entry = {
        "model": model,
        "input": usage["input_tokens"],
        "output": usage["output_tokens"],
        "cost": cost,
        "cache_hit": cache_stats["cache_hit"],
        "savings": cache_stats["savings_usd"]
    }
    current_log.append(log_entry)
    active_usage_log.set(current_log)


    return LLMResult(
        parsed=parsed,
        usage=usage,
        cost=cost,
        thinking=getattr(parsed, "thinking", ""),
        retry_count=retries,
    )


def call_structured_with_usage(
    api_key: str,
    model: str,
    schema: Type[T],
    system_prompt: str,
    user_msg: str,
    max_retries: int = MAX_LLM_RETRIES,
    temperature: float = DEFAULT_TEMPERATURE,
) -> tuple[T, dict]:
    """Backward-compat wrapper: returns (parsed, usage)."""
    result = call_structured(
        api_key=api_key, model=model, schema=schema,
        system_prompt=system_prompt, user_msg=user_msg,
        max_retries=max_retries, temperature=temperature,
    )
    return result.parsed, result.usage


def call_structured_with_thinking(
    api_key: str,
    model: str,
    schema: Type[T],
    system_prompt: str,
    user_msg: str,
    max_retries: int = MAX_LLM_RETRIES,
    temperature: float = DEFAULT_TEMPERATURE,
) -> tuple[T, str]:
    """Backward-compat wrapper: returns (parsed, thinking)."""
    result = call_structured(
        api_key=api_key, model=model, schema=schema,
        system_prompt=system_prompt, user_msg=user_msg,
        max_retries=max_retries, temperature=temperature,
    )
    return result.parsed, result.thinking


# ── 하위 호환: 기존 call_gemini (비구조화 호출) ────────
from google import genai
from google.genai import types

def _get_raw_client(api_key: str):
    """원시 클라이언트 위임 (core/models/gemini_model 사용)"""
    return get_raw_genai_client(api_key)


def call_gemini(api_key: str, model: str = DEFAULT_MODEL, system: str = "",
                user_msg: str = "", temperature: float = DEFAULT_TEMPERATURE, max_tokens: int = 8192) -> str:
    """비구조화 Gemini 호출"""
    try:
        client = _get_raw_client(api_key)
        resp = client.models.generate_content(
            model=model,
            contents=user_msg,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        return resp.text or ""
    except Exception as e:
        # API 키 유효성 오류(400 INVALID_ARGUMENT) 등 구체적 에러 메시지 추출
        err_msg = str(e)
        if "API_KEY_INVALID" in err_msg or "400" in err_msg:
            raise RuntimeError(f"Gemini API 키가 유효하지 않습니다. (400 INVALID_ARGUMENT) 올바른 키를 입력해 주세요.")
        raise RuntimeError(f"Gemini 호출 중 오류 발생: {err_msg}")


# ── JSON 유틸 ────────────────────────────────────

def extract_json_block(text: str) -> str:
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return m.group(1)
    b = _JSON_FALLBACK_RE.search(text)
    return b.group(1) if b else ""


def parse_json_safe(text: str):
    js = extract_json_block(text)
    if not js:
        return None
    try:
        return json.loads(js)
    except Exception:
        return None


def extract_thinking(text: str) -> str:
    m = _THINKING_RE.search(text)
    return m.group(1).strip() if m else ""


def safe_get(obj: Any, keys: list[str], default: Any = "") -> Any:
    """객체나 딕셔너리에서 여러 키 후보 중 첫 번째로 유효한 값을 추출."""
    for k in keys:
        if isinstance(obj, dict):
            val = obj.get(k)
        else:
            val = getattr(obj, k, None)
        if val:
            return val
    return default
def sget(state: Any, key: str, default: Any = None) -> Any:
    """Shared helper to read values from dict-like/object-like State objects."""
    if hasattr(state, "get"):
        value = state.get(key, default)
    else:
        value = getattr(state, key, default)
    return default if value is None else value


def make_sget(state: Any):
    """Curried sget — used at the top of node functions."""
    def _sget(key: str, default: Any = None):
        return sget(state, key, default)
    return _sget


# ── DB 및 저장소 유틸 ────────────────────────────

def get_backend_root() -> Path:
    """백엔드 루트 디렉토리 경로 반환."""
    return Path(__file__).parent.parent.parent


def get_storage_path(sub_dir: str) -> str:
    """백엔드 storage 하위 경로를 안전하게 생성하고 반환."""
    path = get_backend_root() / "storage" / sub_dir
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


# ── 직렬화 유틸 ──────────────────────────────────

def to_serializable(obj: Any, seen=None) -> Any:
    """Pydantic 모델, dict, list를 재귀적으로 JSON 직렬화 가능하게 변환 (순환 참조 방지)."""
    if seen is None:
        seen = set()

    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    # 순환 참조 방지: 현재 탐색 경로(Path)에 동일 객체가 있는지 확인
    obj_id = id(obj)
    if obj_id in seen:
        return f"<Circular Reference: {type(obj).__name__}>"
    
    # 새로운 경로 세트 생성 (자식들에게만 전달하여 형제 노드 간 공유 허용)
    new_seen = seen | {obj_id}

    if hasattr(obj, "model_dump"):
        return to_serializable(obj.model_dump(), new_seen)
    if hasattr(obj, "dict"):
        return to_serializable(obj.dict(), new_seen)
    if isinstance(obj, dict):
        return {k: to_serializable(v, new_seen) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_serializable(item, new_seen) for item in obj]
    
    return str(obj)
