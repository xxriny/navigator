import os
import sys
import asyncio
import hashlib
import hmac
import json
import subprocess
import types


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../../"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from pipeline.domain.dev_tracking.test.dev_tracking_test_utils import (
    _fake_user,
    _github_pr_payload,
    _valid_payload,
)

# author: xxrin
# 원본 대형 Dev Tracking 테스트 파일에서 기능 단위로 분리한 테스트 모듈이다.

def test_agile_impact_injects_dev_tracking_knowledge(monkeypatch):
    from pipeline.domain.agile.schemas import ImpactResult
    from transport.rest_handler import AgileImpactRequest, agile_impact

    captured = {}

    def fake_query(shared_db, **kwargs):
        captured["knowledge"] = kwargs
        return {
            "count": 1,
            "artifacts": [],
            "context_text": "DEV_GAP_DECISION auth endpoint approved",
        }

    def fake_run_impact_analyzer(**kwargs):
        captured["impact"] = kwargs
        return ImpactResult(
            change_description=kwargs["change_description"],
            impacted_components=[],
            risk_level="medium",
            summary="ok",
        )

    import pipeline.domain.agile.nodes.impact as impact_node
    import pipeline.domain.dev_tracking.knowledge as knowledge

    monkeypatch.setattr(knowledge, "query_dev_knowledge_artifacts", fake_query)
    monkeypatch.setattr(impact_node, "run_impact_analyzer", fake_run_impact_analyzer)

    result = asyncio.run(
        agile_impact(
            AgileImpactRequest(
                change_description="auth endpoint change",
                sa_data={"components": []},
                use_llm=False,
                team_id="team-1",
                owner="xxrin",
                repo="navigator",
                branch_name="feature/dev-tracking",
            ),
            shared_db="shared",
            current_user=_fake_user(),
            auth_token="test-token",
        )
    )

    assert result["status"] == "ok"
    assert captured["knowledge"]["team_id"] == "team-1"
    assert captured["knowledge"]["query"] == "auth endpoint change"
    assert captured["impact"]["dev_knowledge_context"] == "DEV_GAP_DECISION auth endpoint approved"

def test_sa_merge_project_user_message_includes_dev_tracking_knowledge():
    from pipeline.domain.sa.nodes.merge_project import _build_user_message

    message = _build_user_message(
        "UPDATE",
        "Add auth callback",
        "FastAPI detected",
        [{"feature_id": "F-1", "description": "Auth callback", "priority": "HIGH"}],
        project_context="{\"components\": []}",
        dev_knowledge_context="DEV_GAP_DECISION get /api/auth approved",
    )

    assert "[Dev Tracking Knowledge]" in message
    assert "DEV_GAP_DECISION get /api/auth approved" in message

def test_downstream_sa_user_messages_include_dev_tracking_knowledge():
    from pipeline.domain.sa.nodes.component_scheduler import _build_user_message as build_components_msg
    from pipeline.domain.sa.nodes.sa_project_structure import _build_user_msg as build_structure_msg
    from pipeline.domain.sa.nodes.sa_test_analysis import _build_user_msg as build_test_msg
    from pipeline.domain.sa.nodes.sa_unified_modeler import _build_user_message as build_unified_msg

    knowledge = "DEV_GAP_DECISION get /api/auth approved"
    merged_project = {
        "merge_strategy": "Preserve auth flow",
        "dev_knowledge_context": knowledge,
        "plan": {"requirements_rtm": [{"feature_id": "F-1", "description": "Auth"}]},
    }
    sa_bundle = {
        "data": {
            "components": [{"component_name": "AuthService"}],
            "apis": [{"endpoint": "GET /api/auth"}],
            "tables": [{"table_name": "users"}],
        }
    }

    assert knowledge in build_components_msg(merged_project, {}, "UPDATE")
    assert knowledge in build_unified_msg([], [], {}, "UPDATE", dev_knowledge_context=knowledge)
    assert knowledge in build_test_msg(sa_bundle, [], "UPDATE", dev_knowledge_context=knowledge)
    assert knowledge in build_structure_msg(sa_bundle, {}, [], "UPDATE", dev_knowledge_context=knowledge)

def test_analyze_update_injects_dev_tracking_knowledge(monkeypatch):
    import transport.rest_handler as rest_handler
    from transport.rest_handler import AnalysisRequest, analyze

    captured = {}

    def fake_query(shared_db, **kwargs):
        captured["knowledge"] = kwargs
        return {"context_text": "DEV_GAP_DECISION auth approved", "count": 1, "artifacts": []}

    def fake_execute_pipeline(pipeline, state, pipeline_type):
        captured["state"] = state
        captured["pipeline_type"] = pipeline_type
        return types.SimpleNamespace(success=True, data=state, error="")

    import pipeline.domain.dev_tracking.knowledge as knowledge

    monkeypatch.setattr(rest_handler, "_ensure_pipeline", lambda: None)
    monkeypatch.setattr(rest_handler, "normalize_action_type", lambda value: value, raising=False)
    monkeypatch.setattr(rest_handler, "validate_analysis_inputs", lambda action_type, idea, source_dir: None, raising=False)
    monkeypatch.setattr(rest_handler, "get_analysis_pipeline", lambda action_type: "pipeline", raising=False)
    monkeypatch.setattr(rest_handler, "analysis_pipeline_type", lambda action_type: "analysis_update", raising=False)
    monkeypatch.setattr(rest_handler, "execute_pipeline", fake_execute_pipeline, raising=False)
    monkeypatch.setattr(knowledge, "query_dev_knowledge_artifacts", fake_query)

    result = asyncio.run(
        analyze(
            AnalysisRequest(
                idea="auth callback",
                context="{\"components\": []}",
                action_type="UPDATE",
                team_id="team-1",
                owner="xxrin",
                repo="navigator",
            ),
            shared_db="shared",
            current_user=_fake_user(),
        )
    )

    assert result["status"] == "ok"
    assert captured["knowledge"]["query"] == "auth callback"
    assert captured["state"]["dev_knowledge_context"] == "DEV_GAP_DECISION auth approved"
    assert captured["pipeline_type"] == "analysis_update"
