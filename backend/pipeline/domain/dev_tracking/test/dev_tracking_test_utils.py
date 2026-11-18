import os
import sys
import types


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

def _fake_user(role="pm", user_id="user-1"):
    return types.SimpleNamespace(id=user_id, role=role, team_id="team-1")

def _valid_payload(source_dir="E:/navigator_v2/KNU-PROJECT"):
    return {
        "trigger": "GITHUB_PR_WEBHOOK",
        "repository": {"owner": "xxrin", "repo": "navigator"},
        "pull_request": {
            "pr_number": 1,
            "branch_name": "feature/dev-tracking",
            "base_branch": "main",
            "head_sha": "abc123",
            "created_at": "2026-05-19T10:00:00+09:00",
            "title": "Implement auth endpoint",
            "description": "Adds auth endpoint and PM report",
        },
        "actor": {"github_id": "xxrin", "role": "developer"},
        "source_dir": source_dir,
        "notify_pr": False,
    }

def _github_pr_payload(action="opened"):
    return {
        "action": action,
        "repository": {
            "name": "navigator",
            "owner": {"login": "xxrin"},
        },
        "number": 17,
        "pull_request": {
            "number": 17,
            "head": {"ref": "feature/dev-tracking", "sha": "abc123"},
            "base": {"ref": "main"},
            "created_at": "2026-05-19T10:00:00Z",
            "title": "Dev tracking webhook",
            "body": "Webhook smoke test",
        },
        "sender": {"login": "xxrin"},
    }

# author: xxrin
# Dev Tracking 테스트 공통 payload/helper를 도메인 내부 테스트에서 재사용한다.
