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

def test_dev_tracking_service_returns_pending_after_completed_graph(monkeypatch, tmp_path):
    import pipeline.orchestration.dev_tracking_graphs as graphs
    from pipeline.domain.dev_tracking.service import run_dev_tracking_analysis
    import pipeline.domain.dev_tracking.nodes as dev_nodes
    import pipeline.domain.agile.task_coordinator as agile_task_coordinator

    graphs._PipelineRegistry._cache.clear()

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
            "code_inventory": {"files": [], "symbols_by_file": {}, "summary": {}},
            "dev_tracking_next_action": "forensic_profiler",
        }

    def fake_forensic_profiler(state):
        return {
            "status": "PASS",
            "implementation_profile": {"detected_apis": [], "detected_components": []},
            "dev_tracking_next_action": "spec_loader",
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

    def fake_create_task(**kwargs):
        return {"id": "task-pending", "task_type": kwargs["task_type"], "status": kwargs["status"]}

    def fake_analysis_persister(state, shared_db=None):
        return {
            "status": "SKIPPED",
            "analysis_persistence": {"stored": False},
            "dev_tracking_next_action": "develop_embedding",
        }

    def fake_develop_embedding(state, shared_db=None):
        return {
            "status": "SKIPPED",
            "embedding_result": {"status": "skipped"},
            "dev_tracking_next_action": "develop_loop_controller",
        }

    monkeypatch.setattr(dev_nodes, "branch_fetcher", fake_branch_fetcher)
    monkeypatch.setattr(dev_nodes, "reverse_analyzer", fake_reverse_analyzer)
    monkeypatch.setattr(dev_nodes, "forensic_profiler", fake_forensic_profiler)
    monkeypatch.setattr(dev_nodes, "spec_loader", fake_spec_loader)
    monkeypatch.setattr(dev_nodes, "analysis_persister", fake_analysis_persister)
    monkeypatch.setattr(dev_nodes, "develop_embedding", fake_develop_embedding)
    monkeypatch.setattr(agile_task_coordinator, "create_task", fake_create_task)

    result = run_dev_tracking_analysis(_valid_payload(source_dir=str(tmp_path)))

    assert result["status"] == "pending_pm_approval"
    assert result["data"]["approval_status"] == "PENDING_PM_APPROVAL"
    assert result["data"]["dev_tracking_next_action"] == "pm_approval_pending"
    assert result["data"]["current_step"] == "develop_loop_controller_done"

    graphs._PipelineRegistry._cache.clear()
    called = {"intent": False, "shared_db_seen": False}

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
            "code_inventory": {"files": [], "symbols_by_file": {}, "summary": {}},
            "dev_tracking_next_action": "forensic_profiler",
        }

    def fake_forensic_profiler(state):
        return {
            "status": "PASS",
            "implementation_profile": {
                "detected_apis": [],
                "detected_components": [],
                "implementation_summary": "No code.",
            },
            "dev_tracking_next_action": "spec_loader",
        }

    def fake_spec_loader(state, shared_db=None):
        called["shared_db_seen"] = shared_db == "shared-session"
        return {
            "status": "PASS",
            "published_spec_snapshot": {"api_contracts": [], "component_contracts": []},
            "spec_outdated": False,
            "latest_snapshot": {},
            "dev_tracking_next_action": "gap_analyzer",
        }

    def fake_intent_classifier(state):
        called["intent"] = True
        return {"status": "PASS", "intent_classification": []}

    def fake_create_task(**kwargs):
        return {"id": "task-graph", "task_type": kwargs["task_type"], "status": kwargs["status"]}

    def fake_analysis_persister(state, shared_db=None):
        return {
            "status": "SKIPPED",
            "analysis_persistence": {"stored": False, "reason": "test"},
            "dev_tracking_next_action": "develop_embedding",
        }

    def fake_develop_embedding(state, shared_db=None):
        return {
            "status": "SKIPPED",
            "embedding_result": {"status": "skipped"},
            "dev_tracking_next_action": "develop_loop_controller",
        }

    monkeypatch.setattr(dev_nodes, "branch_fetcher", fake_branch_fetcher)
    monkeypatch.setattr(dev_nodes, "reverse_analyzer", fake_reverse_analyzer)
    monkeypatch.setattr(dev_nodes, "forensic_profiler", fake_forensic_profiler)
    monkeypatch.setattr(dev_nodes, "spec_loader", fake_spec_loader)
    monkeypatch.setattr(dev_nodes, "intent_classifier", fake_intent_classifier)
    monkeypatch.setattr(dev_nodes, "analysis_persister", fake_analysis_persister)
    monkeypatch.setattr(dev_nodes, "develop_embedding", fake_develop_embedding)
    monkeypatch.setattr(agile_task_coordinator, "create_task", fake_create_task)

    result = run_dev_tracking_analysis(
        _valid_payload(source_dir=str(tmp_path)),
        shared_db="shared-session",
    )
    node_names = [item["node"] for item in result["timeline"]]

    assert result["status"] == "error"
    assert result["data"]["current_step"] == "gap_analyzer_failed"
    assert called["shared_db_seen"] is True
    assert called["intent"] is False
    assert "intent_classifier" not in node_names
    assert "milestone_tracker" not in node_names
    assert "_shared_db" not in result["data"]

    graphs._PipelineRegistry._cache.clear()
