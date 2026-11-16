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

def test_dev_tracking_graph_is_exported_without_legacy_develop_name():
    from pipeline.orchestration import facade

    assert callable(facade.get_dev_tracking_pipeline)
    assert callable(facade.get_dev_tracking_routing_map)
    assert not hasattr(facade, "get_develop_pipeline")
    assert not hasattr(facade, "get_develop_routing_map")

    graph = facade.get_dev_tracking_pipeline()
    routing = facade.get_dev_tracking_routing_map()

    assert graph is not None
    assert routing["first_node"] == "dev_task_planner"
    assert "gap_analyzer" in routing["next_nodes"]
    assert "code_inventory_builder" not in routing["next_nodes"]
    assert routing["next_nodes"]["reverse_analyzer"] == ["forensic_profiler"]

def test_dev_tracking_graph_blocks_invalid_payload_before_task_creation(monkeypatch):
    from pipeline.domain.dev_tracking.service import run_dev_tracking_analysis
    import pipeline.domain.dev_tracking.nodes as dev_nodes

    called = {"task": False}

    def fake_task_coordinator(state):
        called["task"] = True
        return {"status": "PASS"}

    monkeypatch.setattr(dev_nodes, "task_coordinator", fake_task_coordinator)

    result = run_dev_tracking_analysis(
        {
            "trigger": "GITHUB_PR_WEBHOOK",
            "repository": {"owner": "xxrin", "repo": "navigator"},
            "pull_request": {
                "pr_number": 1,
                "branch_name": "feature/dev-tracking",
                "head_sha": "",
            },
            "actor": {"github_id": "xxrin"},
        }
    )

    assert result["status"] == "error"
    assert result["timeline"][0]["node"] == "dev_task_planner"
    assert result["timeline"][0]["error_type"] == "INVALID_WEBHOOK_PAYLOAD"
    assert called["task"] is False
    assert "_shared_db" not in result["data"]

def test_dev_tracking_graph_preserves_ast_evidence_for_success_gate(monkeypatch, tmp_path):
    import pipeline.orchestration.dev_tracking_graphs as graphs
    from pipeline.domain.dev_tracking.service import run_dev_tracking_analysis
    import pipeline.domain.dev_tracking.nodes as dev_nodes

    graphs._PipelineRegistry._cache.clear()
    source = tmp_path / "app" / "api"
    source.mkdir(parents=True)
    (source / "health.py").write_text("def health():\n    return True\n", encoding="utf-8")
    monkeypatch.setattr(dev_nodes, "branch_fetcher", lambda state: {
        "status": "PASS",
        "source_dir": str(tmp_path),
        "checkout": {"head_sha_matched": True, "actual_sha": "abc123def"},
        "dev_tracking_next_action": "reverse_analyzer",
    })
    monkeypatch.setattr(dev_nodes, "spec_loader", lambda state, shared_db=None: {
        "status": "PASS",
        "published_spec_snapshot": {"api_contracts": [{"name": "health"}]},
        "dev_tracking_next_action": "gap_analyzer",
    })

    result = run_dev_tracking_analysis({
        **_valid_payload(source_dir=str(tmp_path)),
        "use_llm_forensic_profiler": False,
        "use_llm_gap_analyzer": False,
    })

    assert result["status"] == "complete"
    assert result["data"]["code_inventory"]["implementation_evidence"]["complete"] is True
    assert result["data"]["pm_report"]["security_gate"]["success_eligible"] is True
    assert result["data"].get("approval_task") is None

def test_dev_tracking_graph_calls_three_llm_paths_for_high_gap(monkeypatch, tmp_path):
    # author:xxrin
    # Graph 전체 실행에서 세 LLM 노드가 실제 state 흐름 안에서 호출되는지 고정한다.
    import pipeline.orchestration.dev_tracking_graphs as graphs
    from pipeline.domain.dev_tracking.service import run_dev_tracking_analysis
    import pipeline.domain.dev_tracking.nodes as dev_nodes
    import pipeline.domain.agile.task_coordinator as agile_task_coordinator

    graphs._PipelineRegistry._cache.clear()
    calls = {"forensic": 0, "gap": 0, "intent": 0}

    class ForensicParsed:
        implementation_profile = dev_nodes.DevImplementationProfile(
            detected_apis=[],
            detected_components=[],
            file_role_map={"app/api/auth.py": "api"},
            implementation_summary="LLM profile from graph.",
        )

    class GapParsed:
        gaps = [
            dev_nodes.DevGapItem(
                gap_id="GAP_001",
                severity="HIGH",
                type="MISSING_API",
                spec_target="get /api/auth",
                implementation_target=None,
                description="LLM graph gap.",
                spec_outdated_related=False,
            )
        ]

    class IntentParsed:
        classifications = [
            dev_nodes.DevGapIntentItem(
                gap_id="GAP_001",
                intent="UNINTENTIONAL",
                confidence=0.88,
                reason="LLM graph intent.",
                recommended_action="REQUEST_FIX",
            )
        ]

    class Result:
        usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
        cost = 0.0
        retry_count = 0

        def __init__(self, parsed):
            self.parsed = parsed

    def fake_branch_fetcher(state):
        return {
            "status": "PASS",
            "source_dir": str(tmp_path),
            "checkout": {"head_sha_matched": True},
            "dev_tracking_next_action": "reverse_analyzer",
        }

    def fake_reverse_analyzer(state):
        return {
            "status": "PASS",
            "project_context": "ctx",
            "code_inventory": {
                "files": [{"file": "app/api/auth.py"}],
                "symbols_by_file": {"app/api/auth.py": [{"name": "login"}]},
                "summary": {"file_count": 1},
            },
            "dev_tracking_next_action": "forensic_profiler",
        }

    def fake_spec_loader(state, shared_db=None):
        return {
            "status": "PASS",
            "published_spec_snapshot": {
                "api_contracts": [{"method": "GET", "url": "/api/auth"}],
                "component_contracts": [],
            },
            "spec_outdated": False,
            "latest_snapshot": {},
            "dev_tracking_next_action": "gap_analyzer",
        }

    def fake_forensic_llm(**kwargs):
        calls["forensic"] += 1
        return Result(ForensicParsed())

    def fake_gap_llm(**kwargs):
        calls["gap"] += 1
        return Result(GapParsed())

    def fake_intent_llm(**kwargs):
        calls["intent"] += 1
        return Result(IntentParsed())

    monkeypatch.setattr(dev_nodes, "branch_fetcher", fake_branch_fetcher)
    monkeypatch.setattr(dev_nodes, "reverse_analyzer", fake_reverse_analyzer)
    monkeypatch.setattr(dev_nodes, "spec_loader", fake_spec_loader)
    monkeypatch.setattr(dev_nodes, "_call_structured_for_forensic", fake_forensic_llm)
    monkeypatch.setattr(dev_nodes, "_call_structured_for_gap", fake_gap_llm)
    monkeypatch.setattr(dev_nodes, "_call_structured_for_intent", fake_intent_llm)
    monkeypatch.setattr(agile_task_coordinator, "create_task", lambda **kwargs: {"id": "task-llm", "task_type": kwargs["task_type"], "status": kwargs["status"]})
    monkeypatch.setattr(dev_nodes, "analysis_persister", lambda state, shared_db=None: {"status": "SKIPPED", "dev_tracking_next_action": "develop_embedding"})
    monkeypatch.setattr(dev_nodes, "develop_embedding", lambda state, shared_db=None: {"status": "SKIPPED", "dev_tracking_next_action": "develop_loop_controller"})

    result = run_dev_tracking_analysis({**_valid_payload(source_dir=str(tmp_path)), "api_key": "test-key"})
    node_names = [item["node"] for item in result["timeline"]]

    assert calls == {"forensic": 1, "gap": 1, "intent": 1}
    assert "intent_classifier" in node_names
    assert result["data"]["forensic_profiler_meta"]["mode"] == "llm"
    assert result["data"]["gap_analyzer_meta"]["mode"] == "llm"
    assert result["data"]["intent_classifier_meta"]["mode"] == "llm"
    assert result["data"]["pm_report"]["recommended_pm_actions"] == ["REQUEST_FIX"]

    graphs._PipelineRegistry._cache.clear()

def test_dev_tracking_graph_accumulates_llm_warning_when_gap_llm_fails(monkeypatch, tmp_path):
    # author:xxrin
    # 한 LLM 노드가 실패해도 graph를 중단하지 않고 PM Report까지 warning을 전달한다.
    import pipeline.orchestration.dev_tracking_graphs as graphs
    from pipeline.domain.dev_tracking.service import run_dev_tracking_analysis
    import pipeline.domain.dev_tracking.nodes as dev_nodes
    import pipeline.domain.agile.task_coordinator as agile_task_coordinator

    graphs._PipelineRegistry._cache.clear()

    class ForensicParsed:
        implementation_profile = dev_nodes.DevImplementationProfile(
            detected_apis=[],
            detected_components=[],
            file_role_map={"app/service/auth.py": "module"},
            implementation_summary="LLM profile from graph.",
        )

    class IntentParsed:
        classifications = [
            dev_nodes.DevGapIntentItem(
                gap_id="GAP_001",
                intent="UNCERTAIN",
                confidence=0.7,
                reason="LLM saw fallback GAP and requires review.",
                recommended_action="PM_REVIEW",
            )
        ]

    class Result:
        usage = {}
        cost = 0.0
        retry_count = 0

        def __init__(self, parsed):
            self.parsed = parsed

    monkeypatch.setattr(dev_nodes, "branch_fetcher", lambda state: {"status": "PASS", "source_dir": str(tmp_path), "checkout": {}, "dev_tracking_next_action": "reverse_analyzer"})
    monkeypatch.setattr(dev_nodes, "reverse_analyzer", lambda state: {
        "status": "PASS",
        "project_context": "ctx",
        "code_inventory": {
            "files": [{"file": "app/service/auth.py"}],
            "symbols_by_file": {"app/service/auth.py": [{"name": "helper"}]},
        },
        "dev_tracking_next_action": "forensic_profiler",
    })
    monkeypatch.setattr(dev_nodes, "spec_loader", lambda state, shared_db=None: {
        "status": "PASS",
        "published_spec_snapshot": {
            "api_contracts": [{"method": "GET", "url": "/api/auth"}],
            "component_contracts": [],
        },
        "spec_outdated": False,
        "latest_snapshot": {},
        "dev_tracking_next_action": "gap_analyzer",
    })
    monkeypatch.setattr(dev_nodes, "_call_structured_for_forensic", lambda **kwargs: Result(ForensicParsed()))
    monkeypatch.setattr(dev_nodes, "_call_structured_for_gap", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("gap llm unavailable")))
    monkeypatch.setattr(dev_nodes, "_call_structured_for_intent", lambda **kwargs: Result(IntentParsed()))
    monkeypatch.setattr(agile_task_coordinator, "create_task", lambda **kwargs: {"id": "task-warning", "task_type": kwargs["task_type"], "status": kwargs["status"]})
    monkeypatch.setattr(dev_nodes, "analysis_persister", lambda state, shared_db=None: {"status": "SKIPPED", "dev_tracking_next_action": "develop_embedding"})
    monkeypatch.setattr(dev_nodes, "develop_embedding", lambda state, shared_db=None: {"status": "SKIPPED", "dev_tracking_next_action": "develop_loop_controller"})

    result = run_dev_tracking_analysis({**_valid_payload(source_dir=str(tmp_path)), "api_key": "test-key"})
    node_names = [item["node"] for item in result["timeline"]]

    assert result["status"] == "pending_pm_approval"
    assert "intent_classifier" in node_names
    assert result["data"]["gap_analyzer_meta"]["mode"] == "rule_based_fallback"
    assert result["data"]["gap_report"][0]["preliminary"] is True
    assert result["data"]["pm_report"]["llm_warnings"][0]["node"] == "gap_analyzer"
    assert result["data"]["pm_report"]["recommended_pm_actions"] == ["PM_REVIEW"]

    graphs._PipelineRegistry._cache.clear()

def test_dev_tracking_graph_respects_llm_node_flags(monkeypatch, tmp_path):
    # author:xxrin
    # 명시 false 플래그가 graph 실행에서도 해당 노드의 LLM 호출을 막는지 검증한다.
    import pipeline.orchestration.dev_tracking_graphs as graphs
    from pipeline.domain.dev_tracking.service import run_dev_tracking_analysis
    import pipeline.domain.dev_tracking.nodes as dev_nodes
    import pipeline.domain.agile.task_coordinator as agile_task_coordinator

    graphs._PipelineRegistry._cache.clear()
    calls = {"forensic": 0, "gap": 0, "intent": 0}

    def fail_forensic(**kwargs):
        calls["forensic"] += 1
        raise AssertionError("forensic LLM should be skipped")

    def fail_gap(**kwargs):
        calls["gap"] += 1
        raise AssertionError("gap LLM should be skipped")

    def fail_intent(**kwargs):
        calls["intent"] += 1
        raise AssertionError("intent LLM should be skipped")

    monkeypatch.setattr(dev_nodes, "branch_fetcher", lambda state: {"status": "PASS", "source_dir": str(tmp_path), "checkout": {}, "dev_tracking_next_action": "reverse_analyzer"})
    monkeypatch.setattr(dev_nodes, "reverse_analyzer", lambda state: {
        "status": "PASS",
        "project_context": "ctx",
        "code_inventory": {
            "files": [{"file": "app/service/auth.py"}],
            "symbols_by_file": {"app/service/auth.py": [{"name": "helper"}]},
        },
        "dev_tracking_next_action": "forensic_profiler",
    })
    monkeypatch.setattr(dev_nodes, "spec_loader", lambda state, shared_db=None: {
        "status": "PASS",
        "published_spec_snapshot": {
            "api_contracts": [{"method": "GET", "url": "/api/auth"}],
            "component_contracts": [],
        },
        "spec_outdated": False,
        "latest_snapshot": {},
        "dev_tracking_next_action": "gap_analyzer",
    })
    monkeypatch.setattr(dev_nodes, "_call_structured_for_forensic", fail_forensic)
    monkeypatch.setattr(dev_nodes, "_call_structured_for_gap", fail_gap)
    monkeypatch.setattr(dev_nodes, "_call_structured_for_intent", fail_intent)
    monkeypatch.setattr(agile_task_coordinator, "create_task", lambda **kwargs: {"id": "task-flags", "task_type": kwargs["task_type"], "status": kwargs["status"]})
    monkeypatch.setattr(dev_nodes, "analysis_persister", lambda state, shared_db=None: {"status": "SKIPPED", "dev_tracking_next_action": "develop_embedding"})
    monkeypatch.setattr(dev_nodes, "develop_embedding", lambda state, shared_db=None: {"status": "SKIPPED", "dev_tracking_next_action": "develop_loop_controller"})

    payload = {
        **_valid_payload(source_dir=str(tmp_path)),
        "api_key": "test-key",
        "use_llm_forensic_profiler": False,
        "use_llm_gap_analyzer": False,
        "use_llm_intent_classifier": False,
    }
    result = run_dev_tracking_analysis(payload)

    assert calls == {"forensic": 0, "gap": 0, "intent": 0}
    assert result["data"]["forensic_profiler_meta"]["mode"] == "rule_based"
    assert result["data"]["gap_analyzer_meta"]["mode"] == "rule_based"
    assert result["data"]["intent_classifier_meta"]["mode"] == "rule_based"
    assert result["data"]["intent_classification"][0]["recommended_action"] == "PM_REVIEW"

    graphs._PipelineRegistry._cache.clear()
