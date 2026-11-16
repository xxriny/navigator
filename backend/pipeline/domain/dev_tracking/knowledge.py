from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_

from .security import build_safe_knowledge_reference, is_verified_dev_knowledge_artifact


def _row_to_dict(row: Any) -> dict[str, Any]:
    # API 응답과 프롬프트 컨텍스트가 같은 구조를 쓰도록 DB row를 평탄화한다.
    try:
        content = json.loads(row.content_json or "{}")
    except Exception:
        content = {}
    return {
        "id": row.id,
        "team_id": row.team_id,
        "artifact_type": row.artifact_type,
        "source": row.source,
        "owner": row.owner,
        "repo": row.repo,
        "pr_number": row.pr_number,
        "branch_name": row.branch_name,
        "task_id": row.task_id,
        "decision_status": row.decision_status,
        "searchable_text": row.searchable_text,
        "content": content,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def format_dev_knowledge_context(
    artifacts: list[dict[str, Any]],
    max_chars: int = 6000,
    *,
    expected_team_id: str = "",
    expected_repository_owner: str = "",
    expected_repository_name: str = "",
) -> str:
    context = json.dumps(
        build_safe_knowledge_reference(
            artifacts,
            expected_team_id=expected_team_id,
            expected_repository_owner=expected_repository_owner,
            expected_repository_name=expected_repository_name,
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(context) > max_chars:
        return context[: max(0, max_chars - 20)].rstrip() + "... [truncated]"
    return context


def query_dev_knowledge_artifacts(
    shared_db: Any,
    *,
    team_id: str = "",
    owner: str = "",
    repo: str = "",
    branch_name: str = "",
    artifact_type: str = "",
    query: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    # shared.db에 저장된 Dev Tracking 지식을 필터링해서 PM/SA 단계가 재사용할 수 있게 반환한다.
    from auth.shared_models import DevKnowledgeArtifact

    safe_limit = max(1, min(int(limit or 10), 50))
    q = shared_db.query(DevKnowledgeArtifact)
    if team_id:
        q = q.filter(DevKnowledgeArtifact.team_id == team_id)
    if owner:
        q = q.filter(DevKnowledgeArtifact.owner == owner)
    if repo:
        q = q.filter(DevKnowledgeArtifact.repo == repo)
    if branch_name:
        q = q.filter(DevKnowledgeArtifact.branch_name == branch_name)
    if artifact_type:
        q = q.filter(DevKnowledgeArtifact.artifact_type == artifact_type)
    if query:
        terms = [term.strip() for term in query.split() if len(term.strip()) >= 2]
        if not terms:
            terms = [query]
        like_clauses = []
        for term in terms[:8]:
            like = f"%{term}%"
            like_clauses.extend(
                [
                    DevKnowledgeArtifact.searchable_text.like(like),
                    DevKnowledgeArtifact.content_json.like(like),
                ]
            )
        q = q.filter(
            or_(*like_clauses)
        )

    rows = q.order_by(DevKnowledgeArtifact.created_at.desc()).limit(safe_limit * 5).all()
    unfiltered_artifacts = [_row_to_dict(row) for row in rows]
    artifacts = [
        artifact
        for artifact in unfiltered_artifacts
        if is_verified_dev_knowledge_artifact(
            artifact,
            expected_team_id=team_id,
            expected_repository_owner=owner,
            expected_repository_name=repo,
        )
    ][:safe_limit]
    return {
        "artifacts": artifacts,
        "count": len(artifacts),
        "ignored_unverified_count": len(unfiltered_artifacts) - len(artifacts),
        "context_text": format_dev_knowledge_context(
            artifacts,
            expected_team_id=team_id,
            expected_repository_owner=owner,
            expected_repository_name=repo,
        ),
    }
