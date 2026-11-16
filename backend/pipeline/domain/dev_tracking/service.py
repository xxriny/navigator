from __future__ import annotations

from typing import Any

from .evidence import build_security_gate

# author:xxrin
# navi_v3 Dev Tracking 실행 어댑터. 실제 노드 실행은 LangGraph graph builder가 담당한다.


def run_dev_tracking_analysis(
    payload: dict[str, Any],
    *,
    shared_db: Any = None,
) -> dict[str, Any]:
    """navi_v3 PR 기반 Dev Tracking MVP 흐름을 실행한다.

    author:xxrin

    기존 PM/SA/Agile/RAG 노드를 직접 고치지 않기 위해 의도적으로 분리했다.
    생성된 분석 결과를 반환 state와 Agile 승인 태스크에 보관한다.
    """

    from pipeline.orchestration.dev_tracking_graphs import get_dev_tracking_pipeline

    initial_state = dict(payload)
    initial_state["timeline"] = []
    initial_state["_shared_db"] = shared_db

    state = get_dev_tracking_pipeline().invoke(
        initial_state,
        config={"recursion_limit": 150},
    )
    state = dict(state)
    state.pop("_shared_db", None)
    timeline = list(state.get("timeline") or [])

    finished_graph = state.get("current_step") == "develop_loop_controller_done"
    if not finished_graph and state.get("dev_tracking_next_action") in {"blocked", "pm_approval_pending"}:
        return {"status": "error", "timeline": timeline, "data": state}

    final_status = "pending_pm_approval"
    if state.get("approval_status") == "NO_GAP_DETECTED" and build_security_gate(state)["success_eligible"]:
        final_status = "complete"
    return {
        "status": final_status,
        "timeline": timeline,
        "data": state,
    }
