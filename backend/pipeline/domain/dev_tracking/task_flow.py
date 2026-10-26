from __future__ import annotations

from typing import Any, Callable

from .evidence import build_security_gate
from .context_compaction import _changed_file_set, _compact_inventory_for_approval_task
from .utils import _as_dict


CreateTask = Callable[..., dict[str, Any]]


def task_coordinator(
    state: dict[str, Any],
    *,
    create_task: CreateTask,
) -> dict[str, Any]:
    # author:xxrin
    # PM 승인 대기 작업을 기존 agile_tasks 큐에 저장한다.
    if state.get("approval_status") == "NO_GAP_DETECTED" and build_security_gate(state)["success_eligible"]:
        return {
            "status": "SKIPPED",
            "approval_task": None,
            "dev_tracking_next_action": "develop_embedding",
            "current_step": "task_coordinator_done",
        }
    pr_context = _as_dict(state.get("pr_context"))
    pm_report = _as_dict(state.get("pm_report"))
    changed_files = _changed_file_set(state)
    pr_number = pr_context.get("pr_number") or ""
    team_id = str(state.get("team_id") or "")
    task = create_task(
        task_type="dev_gap_approval",
        title=f"PR #{pr_number} GAP 승인 요청",
        description=pm_report.get("summary", "Dev Tracking PM approval requested."),
        area="pm",
        payload={
            "approval_status": state.get("approval_status", "PENDING_PM_APPROVAL"),
            "pr_context": pr_context,
            "pm_report": pm_report,
            "gap_report": state.get("gap_report") or [],
            "intent_classification": state.get("intent_classification") or [],
            "milestone_status": state.get("milestone_status") or {},
            "source_dir": state.get("source_dir") or "",
            "spec_outdated": bool(state.get("spec_outdated")),
            # author: xxrin
            # PM이 승인한 뒤에만 코드 청크 artifact를 만들 수 있도록 변경 파일 중심의 축약 inventory를 task에 보존한다.
            "changed_files": sorted(changed_files),
            "code_inventory": _compact_inventory_for_approval_task(
                _as_dict(state.get("code_inventory")),
                changed_files,
            ),
            "implementation_profile": _as_dict(state.get("implementation_profile")),
        },
        created_by=str(_as_dict(state.get("actor")).get("github_id") or state.get("created_by") or ""),
        team_id=team_id,
        status="pending_approval",
        pr_number=pr_number,
    )
    # create_task returns None when a duplicate is detected — look up the existing task
    if task is None:
        from pipeline.domain.agile.task_coordinator import list_tasks
        existing = [
            t for t in list_tasks(team_id=team_id)
            if t.get("task_type") == "dev_gap_approval"
            and str(t.get("pr_number") or "") == str(pr_number)
        ]
        task = existing[0] if existing else {"id": None, "task_type": "dev_gap_approval", "status": "pending_approval"}
    return {
        "status": "PASS",
        "approval_task": {
            "task_id": task.get("id"),
            "task_type": task.get("task_type"),
            "status": task.get("status"),
        },
        "dev_tracking_next_action": "develop_embedding",
        "current_step": "task_coordinator_done",
    }
