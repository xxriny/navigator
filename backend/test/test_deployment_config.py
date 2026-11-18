"""Deployment-only Vertex and database wiring checks."""

import sys
from types import SimpleNamespace
from unittest.mock import patch

from auth import database
from pipeline.core import utils
from pipeline.core.models import gemini_model
from pipeline.domain.agile import task_coordinator
from pipeline.core.node_base import pipeline_node


def test_vertex_mode_uses_project_identity_without_api_key(monkeypatch):
    monkeypatch.setenv("USE_VERTEX_AI", "true")
    monkeypatch.setenv("GCP_PROJECT_ID", "dev-project")
    monkeypatch.setenv("GCP_LOCATION", "global")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(utils, "_fetch_key_from_server", lambda: (_ for _ in ()).throw(AssertionError("key fetch")))
    monkeypatch.setattr(gemini_model.genai, "Client", lambda **kwargs: kwargs)
    fake_vertex = SimpleNamespace(ChatVertexAI=lambda **kwargs: kwargs)
    gemini_model._llm_cache.clear()
    gemini_model._raw_cache.clear()

    with patch.dict(sys.modules, {"langchain_google_vertexai": fake_vertex}):
        assert gemini_model.get_gemini_client("")["project"] == "dev-project"
        assert gemini_model.get_raw_genai_client("") == {
            "vertexai": True, "project": "dev-project", "location": "global",
        }


def test_agile_tasks_use_primary_database_engine():
    assert task_coordinator._engine is database.engine


def test_optional_logger_fallback_keeps_pipeline_api():
    @pipeline_node("logger_smoke")
    def node(ctx):
        return {"ok": True}

    assert node({"run_id": "test", "session_id": "test"})["ok"] is True
