from __future__ import annotations

import json
import os
from typing import Any, Callable

from .evidence import build_security_gate
from .security import validate_pr_comment_write
from .utils import _as_dict


RunGh = Callable[[list[str], str, str | None], tuple[int, str, str]]


def pr_comment_notifier(
    state: dict[str, Any],
    *,
    run_gh: RunGh,
) -> dict[str, Any]:
    # author:xxrin
    # 선택적 PR 알림 노드. 이미 존재하는 PR에 코멘트만 남긴다.
    if not state.get("notify_pr"):
        return {
            "status": "SKIPPED",
            "pr_comment": {
                "comment_created": False,
                "reason": "notify_pr is false",
            },
            "dev_tracking_next_action": "task_coordinator",
            "current_step": "pr_comment_notifier_done",
        }

    pr_context = _as_dict(state.get("pr_context"))
    source_dir = str(state.get("source_dir") or os.getcwd())
    pm_report = _as_dict(state.get("pm_report"))
    blockers = validate_pr_comment_write(state)
    if blockers:
        return {
            "status": "WARN",
            "pr_comment": {"comment_created": False, "blockers": blockers},
            "dev_tracking_next_action": "task_coordinator",
            "current_step": "pr_comment_notifier_done",
        }
    footer = (
        "No blocking GAPs were verified."
        if state.get("approval_status") == "NO_GAP_DETECTED" and build_security_gate(state)["success_eligible"]
        else "PM approval is required in TaskApprovalPanel."
    )
    body = (
        "## NAVIGATOR Dev Tracking Report\n\n"
        f"{pm_report.get('summary', 'GAP report is ready.')}\n\n"
        f"{footer}"
    )
    code, out, err = run_gh(["pr", "comment", str(pr_context.get("pr_number")), "--body", body], source_dir, None)
    return {
        "status": "PASS" if code == 0 else "WARN",
        "pr_comment": {
            "pr_number": pr_context.get("pr_number"),
            "comment_created": code == 0,
            "summary": pm_report.get("summary", ""),
            "error": err if code != 0 else "",
            "stdout": out if code == 0 else "",
        },
        "dev_tracking_next_action": "task_coordinator",
        "current_step": "pr_comment_notifier_done",
    }


def _desired_status_check_for_state(state: dict[str, Any]) -> tuple[str, str]:
    approval_status = str(state.get("approval_status") or "")
    if approval_status == "NO_GAP_DETECTED":
        if build_security_gate(state)["success_eligible"]:
            return "success", "NAVIGATOR Dev Tracking completed without blocking GAPs."
        return "pending", "NAVIGATOR Dev Tracking evidence requires PM review."
    if approval_status == "PENDING_PM_APPROVAL":
        return "pending", "NAVIGATOR Dev Tracking is waiting for PM approval."
    if approval_status == "APPROVED_INTENTIONAL_CHANGE":
        return "success", "PM approved the intentional implementation change."
    if approval_status == "REJECTED_UNINTENTIONAL_CHANGE":
        return "failure", "PM rejected the implementation change."
    return "pending", "NAVIGATOR Dev Tracking review is in progress."


def update_pr_status_check(
    state: dict[str, Any],
    status_state: str,
    description: str,
    *,
    run_gh: RunGh,
) -> dict[str, Any]:
    # author:xxrin
    # GitHub commit status는 병합 차단/허용 신호지만 실패해도 분석 결과 자체는 보존한다.
    pr_context = _as_dict(state.get("pr_context"))
    owner = str(pr_context.get("owner") or "")
    repo = str(pr_context.get("repo") or "")
    sha = str(pr_context.get("head_sha") or "")
    source_dir = str(state.get("source_dir") or os.getcwd())

    if not owner or not repo or not sha:
        return {
            "status": "SKIPPED",
            "status_updated": False,
            "state": status_state,
            "reason": "owner/repo/head_sha missing",
        }

    payload = {
        "state": status_state,
        "context": "NAVIGATOR Dev Tracking",
        "description": description[:140],
    }
    target_url = str(state.get("approval_url") or state.get("target_url") or "")
    if target_url:
        payload["target_url"] = target_url

    code, out, err = run_gh(
        [
            "api",
            f"repos/{owner}/{repo}/statuses/{sha}",
            "--method",
            "POST",
            "--input",
            "-",
        ],
        source_dir,
        json.dumps(payload, ensure_ascii=False),
    )
    return {
        "status": "PASS" if code == 0 else "WARN",
        "status_updated": code == 0,
        "state": status_state,
        "context": payload["context"],
        "description": payload["description"],
        "error": err if code != 0 else "",
        "stdout": out if code == 0 else "",
    }


def pr_status_check_updater(
    state: dict[str, Any],
    *,
    update_status_check: Callable[[dict[str, Any], str, str], dict[str, Any]],
) -> dict[str, Any]:
    # author:xxrin
    # 분석 직후의 상태를 GitHub commit status에 반영한다.
    if not state.get("notify_pr"):
        return {
            "status": "SKIPPED",
            "pr_status_check": {
                "status_updated": False,
                "reason": "notify_pr is false",
            },
            "dev_tracking_next_action": "task_coordinator",
            "current_step": "pr_status_check_updater_done",
        }

    status_state, description = _desired_status_check_for_state(state)
    result = update_status_check(state, status_state, description)
    result["security_gate_blocked"] = (
        state.get("approval_status") == "NO_GAP_DETECTED" and status_state != "success"
    )
    return {
        "status": result.get("status", "WARN"),
        "pr_status_check": result,
        "dev_tracking_next_action": "task_coordinator",
        "current_step": "pr_status_check_updater_done",
    }
