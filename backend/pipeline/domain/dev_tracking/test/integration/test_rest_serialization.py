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

def test_serialize_dev_pr_analysis_returns_ui_history_shape():
    from transport.rest_handler import _serialize_dev_pr_analysis

    row = types.SimpleNamespace(
        id="analysis-1",
        team_id="team-1",
        owner="xxrin",
        repo="navigator",
        pr_number=17,
        branch_name="feature/dev-tracking",
        base_branch="main",
        head_sha="abc123",
        source_dir="/repo",
        spec_snapshot_id="snapshot-1",
        approval_status="PENDING_PM_APPROVAL",
        analysis_status="pm_approval_pending",
        task_id="task-1",
        pm_report=json.dumps({"summary": "GAP 검토 필요"}, ensure_ascii=False),
        timeline=json.dumps([{"node": "gap_analyzer", "status": "PASS"}], ensure_ascii=False),
        created_at=types.SimpleNamespace(isoformat=lambda: "2026-05-24T00:00:00"),
        gap_items=[
            types.SimpleNamespace(severity="HIGH"),
            types.SimpleNamespace(severity="LOW"),
        ],
    )

    serialized = _serialize_dev_pr_analysis(row)

    assert serialized["id"] == "analysis-1"
    assert serialized["pr_number"] == 17
    assert serialized["gap_count"] == 2
    assert serialized["high_gap_count"] == 1
    assert serialized["pm_report_summary"] == "GAP 검토 필요"
    assert serialized["timeline"][0]["node"] == "gap_analyzer"

def test_serialize_dev_pr_analysis_detail_includes_gap_items():
    from transport.rest_handler import _serialize_dev_pr_analysis_detail

    row = types.SimpleNamespace(
        id="analysis-1",
        team_id="team-1",
        owner="xxrin",
        repo="navigator",
        pr_number=17,
        branch_name="feature/dev-tracking",
        base_branch="main",
        head_sha="abc123",
        source_dir="/repo",
        spec_snapshot_id="snapshot-1",
        approval_status="PENDING_PM_APPROVAL",
        analysis_status="pm_approval_pending",
        task_id="task-1",
        pm_report=json.dumps({"summary": "GAP 검토 필요"}, ensure_ascii=False),
        timeline="[]",
        created_at=None,
        gap_items=[
            types.SimpleNamespace(
                id="gap-row-1",
                gap_id="GAP_001",
                severity="HIGH",
                type="MISSING_API",
                spec_target="GET /api/dev",
                implementation_target="",
                intent="UNCERTAIN",
                recommended_action="PM_REVIEW",
                description="API가 구현되지 않음",
                created_at=None,
            )
        ],
    )

    serialized = _serialize_dev_pr_analysis_detail(row)

    assert serialized["gap_count"] == 1
    assert serialized["pm_report"]["summary"] == "GAP 검토 필요"
    assert serialized["gap_items"][0]["gap_id"] == "GAP_001"
    assert serialized["gap_items"][0]["description"] == "API가 구현되지 않음"

def test_dev_gap_item_decision_endpoints_update_item_and_analysis_status(dev_analysis_db_session, pm_user):
    from auth.shared_models import DevGapItem, DevPrAnalysis
    from transport.rest_handler import (
        DevGapDecisionRequest,
        dev_gap_item_approve_endpoint,
        dev_gap_item_reject_endpoint,
    )

    analysis = DevPrAnalysis(
        team_id="team-1",
        owner="xxrin",
        repo="navigator",
        pr_number=17,
        branch_name="feature/dev-tracking",
        head_sha="abc123",
        approval_status="PENDING_PM_APPROVAL",
        gap_count=2,
        has_high_gap=True,
    )
    dev_analysis_db_session.add(analysis)
    dev_analysis_db_session.flush()
    gap_1 = DevGapItem(
        analysis_id=analysis.id,
        gap_id="GAP_001",
        severity="HIGH",
        type="MISSING_API",
        approval_status="PENDING_PM_APPROVAL",
    )
    gap_2 = DevGapItem(
        analysis_id=analysis.id,
        gap_id="GAP_002",
        severity="LOW",
        type="COMPONENT_DRIFT",
        approval_status="PENDING_PM_APPROVAL",
    )
    dev_analysis_db_session.add_all([gap_1, gap_2])
    dev_analysis_db_session.commit()

    approved = asyncio.run(dev_gap_item_approve_endpoint(
        gap_1.id,
        DevGapDecisionRequest(reason="intentional change"),
        current_user=pm_user,
        shared_db=dev_analysis_db_session,
    ))
    rejected = asyncio.run(dev_gap_item_reject_endpoint(
        gap_2.id,
        DevGapDecisionRequest(reason="spec mismatch"),
        current_user=pm_user,
        shared_db=dev_analysis_db_session,
    ))

    refreshed = dev_analysis_db_session.query(DevPrAnalysis).filter(DevPrAnalysis.id == analysis.id).first()
    refreshed_gap_1 = dev_analysis_db_session.query(DevGapItem).filter(DevGapItem.id == gap_1.id).first()
    refreshed_gap_2 = dev_analysis_db_session.query(DevGapItem).filter(DevGapItem.id == gap_2.id).first()

    assert approved["status"] == "ok"
    assert approved["data"]["analysis_approval_status"] == "PENDING_PM_APPROVAL"
    assert rejected["status"] == "ok"
    assert rejected["data"]["analysis_approval_status"] == "REJECTED_UNINTENTIONAL_CHANGE"
    assert refreshed.approval_status == "REJECTED_UNINTENTIONAL_CHANGE"
    assert refreshed_gap_1.approval_status == "APPROVED_INTENTIONAL_CHANGE"
    assert refreshed_gap_1.approved_by == "pm-1"
    assert refreshed_gap_2.approval_status == "REJECTED_UNINTENTIONAL_CHANGE"
    assert "spec mismatch" in refreshed_gap_2.description

def test_dev_gap_item_final_approval_triggers_task_level_followup(monkeypatch, dev_analysis_db_session, pm_user):
    from auth.shared_models import DevGapItem, DevPrAnalysis
    from pipeline.domain.agile import task_coordinator
    import transport.rest_handler as rest_handler
    from transport.rest_handler import DevGapDecisionRequest, dev_gap_item_approve_endpoint

    captured = {}

    def fake_run_dev_gap_decision(task_id, decision_status, reviewed_by, result_payload=None):
        captured["task_id"] = task_id
        captured["decision_status"] = decision_status
        captured["reviewed_by"] = reviewed_by
        captured["result_payload"] = result_payload or {}
        return {"status": "ok", "data": {"id": task_id, "status": "completed"}}

    monkeypatch.setattr(rest_handler, "_run_dev_gap_decision", fake_run_dev_gap_decision)
    monkeypatch.setattr(task_coordinator, "get_task", lambda task_id: {"id": task_id, "team_id": pm_user.team_id})

    analysis = DevPrAnalysis(
        team_id="team-1",
        owner="xxrin",
        repo="navigator",
        pr_number=17,
        branch_name="feature/dev-tracking",
        head_sha="abc123",
        approval_status="PENDING_PM_APPROVAL",
        task_id="task-1",
        gap_count=1,
    )
    dev_analysis_db_session.add(analysis)
    dev_analysis_db_session.flush()
    gap = DevGapItem(
        analysis_id=analysis.id,
        gap_id="GAP_001",
        severity="HIGH",
        type="MISSING_API",
        approval_status="PENDING_PM_APPROVAL",
    )
    dev_analysis_db_session.add(gap)
    dev_analysis_db_session.commit()

    result = asyncio.run(dev_gap_item_approve_endpoint(
        gap.id,
        DevGapDecisionRequest(reason="all gaps approved"),
        current_user=pm_user,
        shared_db=dev_analysis_db_session,
    ))

    assert result["status"] == "ok"
    assert result["data"]["analysis_approval_status"] == "APPROVED_INTENTIONAL_CHANGE"
    assert result["data"]["task_decision"]["status"] == "ok"
    assert captured["task_id"] == "task-1"
    assert captured["decision_status"] == "APPROVED_INTENTIONAL_CHANGE"
    assert captured["reviewed_by"] == "pm-1"
    assert captured["result_payload"]["approved_gaps"][0]["gap_id"] == "GAP_001"
    assert captured["result_payload"]["analysis_id"]
