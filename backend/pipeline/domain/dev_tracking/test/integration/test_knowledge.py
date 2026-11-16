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

def test_query_dev_knowledge_artifacts_returns_prompt_context(dev_knowledge_db_session):
    from auth.shared_models import DevKnowledgeArtifact
    from pipeline.domain.dev_tracking.knowledge import query_dev_knowledge_artifacts

    dev_knowledge_db_session.add(
        DevKnowledgeArtifact(
            team_id="team-1",
            artifact_type="DEV_GAP_DECISION",
            owner="xxrin",
            repo="navigator",
            pr_number=11,
            branch_name="feature/dev-tracking",
            task_id="task-1",
            source="dev_tracking",
            decision_status="APPROVED_INTENTIONAL_CHANGE",
            content_json=json.dumps({"gap_report": [{"gap_id": "GAP_001"}]}),
            searchable_text="GAP_001 auth endpoint approved intentional change",
        )
    )
    dev_knowledge_db_session.add(
        DevKnowledgeArtifact(
            team_id="team-2",
            artifact_type="DEV_GAP_REPORT",
            owner="other",
            repo="repo",
            pr_number=3,
            content_json="{}",
            searchable_text="unrelated",
        )
    )
    dev_knowledge_db_session.commit()

    result = query_dev_knowledge_artifacts(
        dev_knowledge_db_session,
        team_id="team-1",
        owner="xxrin",
        repo="navigator",
        query="auth endpoint",
    )

    assert result["count"] == 1
    assert result["artifacts"][0]["artifact_type"] == "DEV_GAP_DECISION"
    assert "APPROVED_INTENTIONAL_CHANGE" in result["context_text"]
    assert "GAP_001" in result["context_text"]

def test_dev_knowledge_loader_fetches_context_for_intent_classifier(dev_knowledge_db_session):
    from auth.shared_models import DevKnowledgeArtifact
    from pipeline.domain.dev_tracking.nodes import dev_knowledge_loader

    dev_knowledge_db_session.add(
        DevKnowledgeArtifact(
            team_id="team-1",
            artifact_type="DEV_GAP_DECISION",
            owner="xxrin",
            repo="navigator",
            pr_number=11,
            task_id="task-1",
            source="dev_tracking",
            decision_status="APPROVED_INTENTIONAL_CHANGE",
            content_json=json.dumps({"gap_report": [{"gap_id": "GAP_001"}]}),
            searchable_text="get /api/auth APPROVED_INTENTIONAL_CHANGE",
        )
    )
    dev_knowledge_db_session.commit()

    result = dev_knowledge_loader(
        {
            "team_id": "team-1",
            "dev_tracking_next_action": "intent_classifier",
            "pr_context": {"owner": "xxrin", "repo": "navigator", "title": "Auth"},
            "gap_report": [{"gap_id": "GAP_001", "spec_target": "get /api/auth"}],
        },
        shared_db=dev_knowledge_db_session,
    )

    assert result["status"] == "PASS"
    assert result["dev_tracking_next_action"] == "intent_classifier"
    assert result["dev_knowledge"]["count"] == 1
    assert "APPROVED_INTENTIONAL_CHANGE" in result["dev_knowledge_context"]

def test_dev_tracking_knowledge_query_endpoint(monkeypatch):
    from transport.rest_handler import DevKnowledgeQueryRequest, dev_tracking_knowledge_query_endpoint

    captured = {}

    def fake_query(shared_db, **kwargs):
        captured.update(kwargs)
        return {"count": 1, "artifacts": [], "context_text": "ctx"}

    import pipeline.domain.dev_tracking.knowledge as knowledge

    monkeypatch.setattr(knowledge, "query_dev_knowledge_artifacts", fake_query)

    result = asyncio.run(
        dev_tracking_knowledge_query_endpoint(
            DevKnowledgeQueryRequest(
                team_id="team-1",
                owner="xxrin",
                repo="navigator",
                query="GAP_001",
                limit=5,
            ),
            shared_db="shared",
        )
    )

    assert result["status"] == "ok"
    assert result["data"]["context_text"] == "ctx"
    assert captured["team_id"] == "team-1"
    assert captured["query"] == "GAP_001"
    assert captured["limit"] == 5
