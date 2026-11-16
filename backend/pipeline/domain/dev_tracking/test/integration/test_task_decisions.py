import os
import sys
import asyncio
import hashlib
import hmac
import json
import subprocess
import types

import pytest
from fastapi import HTTPException


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

def test_dev_gap_approval_endpoint_updates_success_status(monkeypatch, pm_user):
    import pipeline.domain.agile.task_coordinator as agile_task_coordinator
    import pipeline.domain.dev_tracking.nodes as dev_nodes
    from transport.rest_handler import TaskUpdateRequest, update_task_endpoint

    calls = {}
    stored_results = []
    task = {
        "id": "task-1",
        "team_id": "team-1",
        "task_type": "dev_gap_approval",
        "status": "in_progress",
        "payload": {
            "pr_context": {
                "owner": "xxrin",
                "repo": "navigator",
                "head_sha": "abc123",
            }
        },
    }

    monkeypatch.setattr(agile_task_coordinator, "init_tasks_db", lambda: None)
    monkeypatch.setattr(agile_task_coordinator, "execute_approved_task", lambda task: json.dumps({"approval_status": "APPROVED_INTENTIONAL_CHANGE"}))

    def fake_update_task_status(task_id, status, reviewed_by="", result=""):
        if result:
            stored_results.append(json.loads(result))
        updated = dict(task)
        updated["status"] = status
        updated["result"] = result
        updated["reviewed_by"] = reviewed_by
        return updated

    def fake_update_pr_status_check(state, status_state, description):
        calls["state"] = state
        calls["status_state"] = status_state
        calls["description"] = description
        return {"status": "PASS", "status_updated": True, "state": status_state}

    def fake_run_dev_gap_decision_followup(task, decision_status, reviewed_by="", result_payload=None):
        calls["followup_decision_status"] = decision_status
        calls["followup_reviewed_by"] = reviewed_by
        return {"status": "PASS", "rag_metadata": {"write_enabled": False}}

    monkeypatch.setattr(agile_task_coordinator, "update_task_status", fake_update_task_status)
    monkeypatch.setattr(agile_task_coordinator, "get_task", lambda task_id: task)
    monkeypatch.setattr(dev_nodes, "update_pr_status_check", fake_update_pr_status_check)
    monkeypatch.setattr(dev_nodes, "run_dev_gap_decision_followup", fake_run_dev_gap_decision_followup)

    result = asyncio.run(
        update_task_endpoint(
            "task-1",
            TaskUpdateRequest(status="in_progress", reviewed_by="pm"),
            current_user=pm_user,
        )
    )

    assert result["status"] == "ok"
    assert result["data"]["status"] == "completed"
    assert calls["status_state"] == "success"
    assert calls["followup_decision_status"] == "APPROVED_INTENTIONAL_CHANGE"
    assert calls["followup_reviewed_by"] == "pm-1"
    assert stored_results[-1]["status_check"]["status_updated"] is True
    assert stored_results[-1]["followup"]["status"] == "PASS"
    assert stored_results[-1]["followup"]["doc_sync"]["updater"] == "doc_updater"
    assert stored_results[-1]["followup"]["doc_sync"]["decision_status"] == "APPROVED_INTENTIONAL_CHANGE"
    assert "pr_comment" in stored_results[-1]["followup"]
    assert result["data"]["reviewed_by"] == "pm-1"

def test_dev_gap_rejection_endpoint_updates_failure_status(monkeypatch, admin_user):
    import pipeline.domain.agile.task_coordinator as agile_task_coordinator
    import pipeline.domain.dev_tracking.nodes as dev_nodes
    from transport.rest_handler import TaskUpdateRequest, update_task_endpoint

    calls = {}
    stored_results = []
    task = {
        "id": "task-1",
        "team_id": "team-1",
        "task_type": "dev_gap_approval",
        "status": "rejected",
        "payload": {
            "pr_context": {
                "owner": "xxrin",
                "repo": "navigator",
                "head_sha": "abc123",
            }
        },
    }

    monkeypatch.setattr(agile_task_coordinator, "init_tasks_db", lambda: None)

    def fake_update_task_status(task_id, status, reviewed_by="", result=""):
        if result:
            stored_results.append(json.loads(result))
        updated = dict(task)
        updated["status"] = status
        updated["result"] = result
        updated["reviewed_by"] = reviewed_by
        return updated

    def fake_update_pr_status_check(state, status_state, description):
        calls["state"] = state
        calls["status_state"] = status_state
        calls["description"] = description
        return {"status": "PASS", "status_updated": True, "state": status_state}

    def fake_run_dev_gap_decision_followup(task, decision_status, reviewed_by="", result_payload=None):
        calls["followup_decision_status"] = decision_status
        calls["followup_reviewed_by"] = reviewed_by
        return {"status": "PASS", "pr_comment": {"comment_created": True}}

    def fake_create_task(**kwargs):
        calls["sa_review_task"] = kwargs
        return {
            "id": "sa-task-1",
            "task_type": kwargs["task_type"],
            "status": kwargs["status"],
            "payload": kwargs["payload"],
        }

    monkeypatch.setattr(agile_task_coordinator, "update_task_status", fake_update_task_status)
    monkeypatch.setattr(agile_task_coordinator, "get_task", lambda task_id: task)
    monkeypatch.setattr(agile_task_coordinator, "create_task", fake_create_task)
    monkeypatch.setattr(dev_nodes, "update_pr_status_check", fake_update_pr_status_check)
    monkeypatch.setattr(dev_nodes, "run_dev_gap_decision_followup", fake_run_dev_gap_decision_followup)

    result = asyncio.run(
        update_task_endpoint(
            "task-1",
            TaskUpdateRequest(status="rejected", reviewed_by="pm"),
            current_user=admin_user,
        )
    )

    assert result["status"] == "ok"
    assert result["data"]["status"] == "rejected"
    assert calls["status_state"] == "failure"
    assert calls["followup_decision_status"] == "REJECTED_UNINTENTIONAL_CHANGE"
    assert calls["followup_reviewed_by"] == "admin-1"
    assert stored_results[-1]["approval_status"] == "REJECTED_UNINTENTIONAL_CHANGE"
    assert stored_results[-1]["status_check"]["status_updated"] is True
    assert stored_results[-1]["followup"]["pr_comment"]["comment_created"] is True
    assert stored_results[-1]["followup"]["doc_sync"]["updater"] == "doc_updater"
    assert stored_results[-1]["followup"]["doc_sync"]["decision_status"] == "REJECTED_UNINTENTIONAL_CHANGE"
    assert stored_results[-1]["sa_review_task"]["task_id"] == "sa-task-1"
    assert calls["sa_review_task"]["task_type"] == "sa_re_review"
    assert calls["sa_review_task"]["area"] == "sa"
    assert calls["sa_review_task"]["payload"]["parent_task_id"] == "task-1"

def test_dev_gap_approve_endpoint_uses_explicit_contract(monkeypatch, pm_user):
    import pipeline.domain.agile.task_coordinator as agile_task_coordinator
    import pipeline.domain.dev_tracking.nodes as dev_nodes
    from transport.rest_handler import DevGapDecisionRequest, dev_gap_approve_endpoint

    stored_results = []
    task = {
        "id": "task-1",
        "team_id": "team-1",
        "task_type": "dev_gap_approval",
        "status": "pending_approval",
        "payload": {"pr_context": {"owner": "xxrin", "repo": "navigator", "head_sha": "abc123"}},
    }

    monkeypatch.setattr(agile_task_coordinator, "init_tasks_db", lambda: None)
    monkeypatch.setattr(agile_task_coordinator, "get_task", lambda task_id: task)
    monkeypatch.setattr(agile_task_coordinator, "execute_approved_task", lambda task: json.dumps({"approval_status": "APPROVED_INTENTIONAL_CHANGE"}))

    def fake_update_task_status(task_id, status, reviewed_by="", result=""):
        if result:
            stored_results.append(json.loads(result))
        updated = dict(task)
        updated["status"] = status
        updated["reviewed_by"] = reviewed_by
        updated["result"] = result
        return updated

    monkeypatch.setattr(agile_task_coordinator, "update_task_status", fake_update_task_status)
    monkeypatch.setattr(dev_nodes, "update_pr_status_check", lambda state, status_state, description: {"status": "PASS", "status_updated": True, "state": status_state})
    monkeypatch.setattr(dev_nodes, "run_dev_gap_decision_followup", lambda *args, **kwargs: {"status": "PASS"})

    result = asyncio.run(
        dev_gap_approve_endpoint(
            "task-1",
            DevGapDecisionRequest(reason="요구사항 의도 반영"),
            current_user=pm_user,
        )
    )

    assert result["status"] == "ok"
    assert result["data"]["status"] == "completed"
    assert result["data"]["reviewed_by"] == "pm-1"
    assert stored_results[-1]["approval_status"] == "APPROVED_INTENTIONAL_CHANGE"
    assert stored_results[-1]["reason"] == "요구사항 의도 반영"

def test_dev_gap_reject_endpoint_rejects_engineer_role(monkeypatch, engineer_user):
    from transport.rest_handler import DevGapDecisionRequest, dev_gap_reject_endpoint

    result = asyncio.run(
        dev_gap_reject_endpoint(
            "task-1",
            DevGapDecisionRequest(reason="불일치"),
            current_user=engineer_user,
        )
    )

    assert result["status"] == "error"
    assert "PM or admin" in result["error"]

def test_dev_gap_sa_review_request_endpoint_creates_sa_task(monkeypatch, pm_user):
    import pipeline.domain.agile.task_coordinator as agile_task_coordinator
    from transport.rest_handler import DevGapSaReviewRequest, dev_gap_sa_review_request_endpoint

    captured = {}
    task = {
        "id": "task-1",
        "task_type": "dev_gap_approval",
        "status": "rejected",
        "team_id": "team-1",
        "payload": {
            "pr_context": {"owner": "xxrin", "repo": "navigator", "pr_number": 17},
            "gap_report": [{"gap_id": "GAP_001", "severity": "HIGH"}],
            "source_dir": "E:/navigator_v2/KNU-PROJECT",
        },
    }

    monkeypatch.setattr(agile_task_coordinator, "init_tasks_db", lambda: None)
    monkeypatch.setattr(agile_task_coordinator, "get_task", lambda task_id: task)

    def fake_create_task(**kwargs):
        captured.update(kwargs)
        return {
            "id": "sa-task-1",
            "task_type": kwargs["task_type"],
            "status": kwargs["status"],
            "payload": kwargs["payload"],
        }

    monkeypatch.setattr(agile_task_coordinator, "create_task", fake_create_task)

    result = asyncio.run(
        dev_gap_sa_review_request_endpoint(
            "task-1",
            DevGapSaReviewRequest(reason="spec impact needs review"),
            current_user=pm_user,
        )
    )

    assert result["status"] == "ok"
    assert result["data"]["task_id"] == "sa-task-1"
    assert captured["task_type"] == "sa_re_review"
    assert captured["status"] == "unassigned"
    assert captured["team_id"] == "team-1"
    assert captured["payload"]["requested_by"] == "pm-1"
    assert captured["payload"]["gap_report"][0]["gap_id"] == "GAP_001"

def test_dev_gap_approval_requires_authenticated_pm(monkeypatch):
    import pipeline.domain.agile.task_coordinator as agile_task_coordinator
    from transport.rest_handler import TaskUpdateRequest, update_task_endpoint

    task = {"id": "task-1", "team_id": "team-1", "task_type": "dev_gap_approval", "status": "pending_approval", "payload": {}}
    called = {"updated": False}

    monkeypatch.setattr(agile_task_coordinator, "init_tasks_db", lambda: None)
    monkeypatch.setattr(agile_task_coordinator, "get_task", lambda task_id: task)

    def fake_update_task_status(*args, **kwargs):
        called["updated"] = True
        return task

    monkeypatch.setattr(agile_task_coordinator, "update_task_status", fake_update_task_status)

    with pytest.raises(HTTPException) as denied:
        asyncio.run(update_task_endpoint(
            "task-1", TaskUpdateRequest(status="in_progress"), current_user=None,
        ))
    assert denied.value.status_code == 401
    assert called["updated"] is False

def test_dev_gap_approval_rejects_engineer_role(monkeypatch, engineer_user):
    import pipeline.domain.agile.task_coordinator as agile_task_coordinator
    from transport.rest_handler import TaskUpdateRequest, update_task_endpoint

    task = {"id": "task-1", "team_id": "team-1", "task_type": "dev_gap_approval", "status": "pending_approval", "payload": {}}
    called = {"updated": False}

    monkeypatch.setattr(agile_task_coordinator, "init_tasks_db", lambda: None)
    monkeypatch.setattr(agile_task_coordinator, "get_task", lambda task_id: task)

    def fake_update_task_status(*args, **kwargs):
        called["updated"] = True
        return task

    monkeypatch.setattr(agile_task_coordinator, "update_task_status", fake_update_task_status)

    result = asyncio.run(
        update_task_endpoint(
            "task-1",
            TaskUpdateRequest(status="rejected"),
            current_user=engineer_user,
        )
    )

    assert result["status"] == "error"
    assert "PM or admin" in result["error"]
    assert called["updated"] is False

def test_regular_task_update_requires_authentication(monkeypatch):
    import pipeline.domain.agile.task_coordinator as agile_task_coordinator
    from transport.rest_handler import TaskUpdateRequest, update_task_endpoint

    task = {"id": "task-1", "team_id": "team-1", "task_type": "feature", "status": "pending_approval", "payload": {}}

    monkeypatch.setattr(agile_task_coordinator, "init_tasks_db", lambda: None)
    monkeypatch.setattr(agile_task_coordinator, "get_task", lambda task_id: task)

    def fake_update_task_status(task_id, status, reviewed_by="", result=""):
        updated = dict(task)
        updated["status"] = status
        updated["reviewed_by"] = reviewed_by
        return updated

    monkeypatch.setattr(agile_task_coordinator, "update_task_status", fake_update_task_status)

    with pytest.raises(HTTPException) as denied:
        asyncio.run(update_task_endpoint(
            "task-1", TaskUpdateRequest(status="in_progress", reviewed_by="legacy-user"),
            current_user=None,
        ))
    assert denied.value.status_code == 401
