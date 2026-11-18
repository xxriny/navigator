"""
Gemini Model Manager — Google Generative AI & LangChain Integration
`core/utils.py`에서 분리된 Gemini API 관리 도메인입니다.
"""

import os
import threading
from collections import OrderedDict
from typing import Any, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from google import genai
from version import DEFAULT_MODEL, DEFAULT_TEMPERATURE
from observability.logger import get_logger

logger = get_logger()

# ── 상수 및 캐시 설정 ────────────────────────────────
_CACHE_LIMIT = 32
_llm_cache: OrderedDict[str, BaseChatModel] = OrderedDict()
_raw_cache: OrderedDict[str, genai.Client] = OrderedDict()
_llm_cache_lock = threading.Lock()
_raw_cache_lock = threading.Lock()


def _remember_cache_entry(cache: OrderedDict, key: str, value: Any):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _CACHE_LIMIT:
        cache.popitem(last=False)


def get_gemini_client(api_key: str, model: str = DEFAULT_MODEL, temperature: float = DEFAULT_TEMPERATURE) -> BaseChatModel:
    """배포 시 Vertex AI, 로컬 개발 시 Gemini API 클라이언트를 반환한다."""
    from pipeline.core.utils import get_effective_key
    effective_key = get_effective_key(api_key)
    use_vertex = os.environ.get("USE_VERTEX_AI", "false").lower() == "true"
    cache_key = f"{effective_key}:{os.environ.get('GCP_PROJECT_ID')}:{os.environ.get('GCP_LOCATION')}:{model}:{temperature}"

    with _llm_cache_lock:
        if cache_key in _llm_cache:
            _llm_cache.move_to_end(cache_key)
            return _llm_cache[cache_key]

        if use_vertex:
            from langchain_google_vertexai import ChatVertexAI
            llm = ChatVertexAI(
                model_name=model, temperature=temperature,
                project=os.environ["GCP_PROJECT_ID"], location=os.environ["GCP_LOCATION"],
            )
        else:
            llm = ChatGoogleGenerativeAI(
                model=model,
                google_api_key=effective_key,
                temperature=temperature,
            )

        _remember_cache_entry(_llm_cache, cache_key, llm)
    return llm


def get_raw_genai_client(api_key: str) -> genai.Client:
    """배포 시 Vertex AI, 로컬 개발 시 Gemini API 클라이언트를 반환한다."""
    from pipeline.core.utils import get_effective_key
    effective_key = get_effective_key(api_key)
    use_vertex = os.environ.get("USE_VERTEX_AI", "false").lower() == "true"
    cache_key = f"{effective_key}:{os.environ.get('GCP_PROJECT_ID')}:{os.environ.get('GCP_LOCATION')}"
    with _raw_cache_lock:
        if cache_key in _raw_cache:
            _raw_cache.move_to_end(cache_key)
            return _raw_cache[cache_key]

        if use_vertex:
            client = genai.Client(
                vertexai=True, project=os.environ["GCP_PROJECT_ID"],
                location=os.environ["GCP_LOCATION"],
            )
        else:
            client = genai.Client(api_key=effective_key)

        _remember_cache_entry(_raw_cache, cache_key, client)
    return client
