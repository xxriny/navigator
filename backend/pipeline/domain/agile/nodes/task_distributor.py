"""
Task Distributor Node
unassigned 태스크를 팀 멤버의 역할(role)과 현재 업무량(workload)에 따라 자동 배분.
PM이 "배분" 버튼을 누를 때 호출된다.

배분 규칙:
- pm: 배분 제외 (배분하는 주체)
- engineer: fullstack — 모든 area 배분 가능
- backend: area=backend, fullstack 태스크 배분
- frontend: area=frontend, fullstack 태스크 배분
- devops: area=devops 태스크 배분
- 업무량 적은 멤버 우선
"""
from __future__ import annotations

import json

from pipeline.core.utils import call_structured
from pipeline.domain.agile.schemas import TaskDistributorOutput
from pipeline.domain.agile.task_coordinator import assign_task, list_tasks
from observability.logger import get_logger

logger = get_logger()

SYSTEM_PROMPT = """# Role: Agile Workload Balancing Specialist

## Goal
Assign unassigned development tasks to team members based on:
1. Role-area compatibility
2. Current workload (prefer members with fewer active tasks)

## Role → Area Compatibility
- engineer: can receive ANY area (backend, frontend, devops, fullstack)
- backend: backend, fullstack
- frontend: frontend, fullstack
- devops: devops
- pm: EXCLUDED from assignment

## Workload Balancing Rules
- Always prefer the member with the LOWEST current task count among compatible members
- When counts are equal, distribute evenly (round-robin by area)
- Never assign to pm role members

## Output Rules
- thinking (th): Brief reasoning about balancing decisions (Korean)
- assignments (as_): List of {task_id, assignee_name, reason} for each assigned task
- workload_summary (ws): {member_name: number_of_newly_assigned_tasks}
- balancing_note (bn): Overall balancing summary (Korean)

## Important
- Every unassigned task MUST appear in assignments
- reason should briefly explain why this member was chosen (role match + workload)
- Use exact member names as provided
"""


def _get_team_members(team_id: str) -> list[dict]:
    """팀 멤버 목록 조회 (pm 제외)."""
    from auth.database import SharedSessionLocal
    from auth.shared_models import TeamMember, User
    db = SharedSessionLocal()
    try:
        members = db.query(TeamMember, User).join(
            User, User.id == TeamMember.user_id,
        ).filter(
            TeamMember.team_id == team_id,
            TeamMember.role != "pm",
        ).all()
        return [
            {"id": user.id, "name": user.name, "role": membership.role}
            for membership, user in members
        ]
    finally:
        db.close()


def _get_current_workload(team_id: str, members: list[dict]) -> dict[str, int]:
    """멤버별 활성 태스크 수 (pending_approval + in_progress + pr_pending)."""
    active_statuses = {"pending_approval", "in_progress", "pr_pending"}
    tasks = list_tasks(team_id=team_id)
    counts: dict[str, int] = {m["name"]: 0 for m in members}
    for t in tasks:
        if t.get("status") in active_statuses and t.get("assignee") in counts:
            counts[t["assignee"]] += 1
    return counts


def _build_user_msg(
    unassigned_tasks: list[dict],
    members: list[dict],
    workload: dict[str, int],
) -> str:
    member_info = [
        {
            "name": m["name"],
            "role": m["role"],
            "current_tasks": workload.get(m["name"], 0),
        }
        for m in members
    ]
    task_summary = [
        {
            "id": t["id"],
            "title": t["title"],
            "area": t["area"],
            "priority": (t.get("payload") or {}).get("priority", "medium"),
            "effort": t["effort"],
            "feature_ref": t["feature_ref"],
        }
        for t in unassigned_tasks
    ]

    return (
        f"## Team Members ({len(members)}명)\n"
        f"```json\n{json.dumps(member_info, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## Unassigned Tasks ({len(unassigned_tasks)}개)\n"
        f"```json\n{json.dumps(task_summary, ensure_ascii=False, indent=2)[:4000]}\n```\n\n"
        "위 태스크를 팀 멤버에게 역할과 업무량을 고려해 배분하세요."
    )


def run_task_distributor(
    team_id: str,
    api_key: str,
    model: str,
    distributed_by: str = "",
    members: list | None = None,
) -> dict:
    """
    unassigned 태스크 → 팀 멤버에게 배분 → pending_approval 상태로 전환.
    members: 이전 REST 호출 계약을 위한 인자. 실제 팀원은 DB에서 조회한다.
    """
    # Client-provided members are display data, not assignment authority.
    members = _get_team_members(team_id)
    if not members:
        logger.warning("[task_distributor] 배분 가능한 팀 멤버 없음 (pm 외 멤버 없음)")
        return {"assigned": 0, "message": "배분 가능한 팀 멤버가 없습니다."}

    unassigned = [t for t in list_tasks(team_id=team_id) if t["status"] == "unassigned"]
    if not unassigned:
        logger.info("[task_distributor] 배분할 태스크 없음")
        return {"assigned": 0, "message": "배분할 태스크가 없습니다."}

    workload = _get_current_workload(team_id, members)
    user_msg = _build_user_msg(unassigned, members, workload)

    res = call_structured(
        api_key=api_key,
        model=model,
        schema=TaskDistributorOutput,
        system_prompt=SYSTEM_PROMPT,
        user_msg=user_msg,
        compress_prompt=False,
        temperature=0.0,
    )

    if not res.parsed:
        logger.warning("[task_distributor] LLM 파싱 실패")
        return {"assigned": 0, "message": "LLM 배분 실패"}

    output = res.parsed
    member_by_name = {}
    for member in members:
        name = member["name"]
        member_by_name[name] = None if name in member_by_name else member
    assigned_count = 0

    for assignment in output.assignments:
        member = member_by_name.get(assignment.assignee_name)
        if not member:
            continue

        result = assign_task(
            task_id=assignment.task_id,
            assignee=member["name"],
            reviewed_by=distributed_by,
            team_id=team_id,
            assignee_id=member["id"],
        )
        if result:
            assigned_count += 1

    logger.info(f"[task_distributor] 완료: {assigned_count}개 배분")
    return {
        "assigned": assigned_count,
        "workload_summary": output.workload_summary,
        "balancing_note": output.balancing_note,
        "thinking": output.thinking,
    }
