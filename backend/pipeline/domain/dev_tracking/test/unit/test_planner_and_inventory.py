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

def test_dev_task_planner_valid_payload_creates_pr_context():
    from pipeline.domain.dev_tracking.nodes import dev_task_planner

    result = dev_task_planner(_valid_payload())

    assert result["status"] == "PASS"
    assert result["dev_tracking_next_action"] == "branch_fetcher"
    assert result["pr_context"]["owner"] == "xxrin"
    assert result["pr_context"]["repo"] == "navigator"
    assert result["pr_context"]["pr_number"] == 1
    assert result["pr_context"]["branch_name"] == "feature/dev-tracking"

def test_dev_task_planner_invalid_payload_reports_missing_head_sha():
    from pipeline.domain.dev_tracking.nodes import dev_task_planner

    payload = _valid_payload()
    payload["pull_request"]["head_sha"] = ""

    result = dev_task_planner(payload)

    assert result["status"] == "FAIL"
    assert result["error_type"] == "INVALID_WEBHOOK_PAYLOAD"
    assert result["dev_tracking_next_action"] == "blocked"
    assert any("pull_request.head_sha" in item for item in result["errors"])

def test_code_inventory_builder_scans_temp_project(tmp_path):
    from pipeline.domain.dev_tracking.nodes import code_inventory_builder

    api_dir = tmp_path / "app" / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "users.py").write_text(
        "def list_users():\n"
        "    return []\n",
        encoding="utf-8",
    )
    component_dir = tmp_path / "src" / "components"
    component_dir.mkdir(parents=True)
    (component_dir / "UserList.jsx").write_text(
        "export function UserList() {\n"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )

    result = code_inventory_builder({"source_dir": str(tmp_path)})

    assert result["status"] == "PASS"
    assert result["dev_tracking_next_action"] == "forensic_profiler"
    assert result["code_inventory"]["summary"]["file_count"] == 2
    assert result["code_inventory"]["summary"]["symbol_count"] >= 2

def test_branch_fetcher_uses_worktree_at_webhook_head_sha(monkeypatch, tmp_path):
    from pipeline.domain.dev_tracking import nodes
    import connectors.repo_cache as repo_cache

    repo_dir = tmp_path / "repo"
    (repo_dir / ".git").mkdir(parents=True)
    calls = []

    def fake_get_local_repo_path(owner, repo, token=None):
        return str(repo_dir)

    def fake_run_git(args, cwd):
        calls.append(args)
        if args[:2] == ["rev-parse", "HEAD"]:
            return 0, "abc123def456\n", ""
        return 0, "", ""

    monkeypatch.setattr(repo_cache, "get_local_repo_path", fake_get_local_repo_path)
    monkeypatch.setattr(nodes, "_run_git", fake_run_git)

    result = nodes.branch_fetcher({
        "pr_context": {
            "owner": "xxrin",
            "repo": "navigator",
            "branch_name": "feature/dev-tracking",
            "head_sha": "abc123",
            "changed_files": ["backend/app.py"],
        }
    })

    assert result["status"] == "PASS"
    assert any(args[:3] == ["worktree", "add", "--detach"] and args[-1] == "abc123" for args in calls)
    assert result["checkout"]["worktree_used"] is True
    assert result["checkout"]["head_sha_matched"] is True


def test_branch_fetcher_blocks_mismatched_provided_git_head(monkeypatch, tmp_path):
    from pipeline.domain.dev_tracking import nodes

    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(nodes, "_run_git", lambda args, cwd: (0, "different-sha\n", ""))

    result = nodes.branch_fetcher({
        "source_dir": str(tmp_path),
        "pr_context": {"head_sha": "abc123"},
    })

    assert result["status"] == "FAIL"
    assert result["error_type"] == "HEAD_SHA_MISMATCH"
    assert result["dev_tracking_next_action"] == "blocked"
    assert result["checkout"]["head_sha_matched"] is False


def test_branch_fetcher_verifies_provided_worktree_git_file(monkeypatch, tmp_path):
    from pipeline.domain.dev_tracking import nodes

    (tmp_path / ".git").write_text("gitdir: /unused", encoding="utf-8")
    monkeypatch.setattr(nodes, "_run_git", lambda args, cwd: (0, "abc123def\n", ""))
    monkeypatch.setattr(nodes, "_changed_files_from_git", lambda repo_path, base_branch: [])

    result = nodes.branch_fetcher({
        "source_dir": str(tmp_path),
        "pr_context": {"head_sha": "abc123"},
    })

    assert result["status"] == "PASS"
    assert result["checkout"]["head_sha_matched"] is True

def test_reverse_analyzer_returns_inventory_without_separate_builder(monkeypatch, tmp_path):
    import orchestration.pipeline_runner as pipeline_runner
    from pipeline.domain.dev_tracking import nodes

    def fake_build_reverse_context(source_dir):
        return (
            "ctx",
            {
                "app/api/auth.py": [
                    {"name": "login", "summary": "login endpoint"},
                ]
            },
        )

    monkeypatch.setattr(pipeline_runner, "build_reverse_context", fake_build_reverse_context)

    result = nodes.reverse_analyzer({"source_dir": str(tmp_path)})

    assert result["status"] == "PASS"
    assert result["dev_tracking_next_action"] == "forensic_profiler"
    assert result["code_inventory"]["summary"]["file_count"] == 1
    assert result["code_inventory"]["symbols_by_file"]["app/api/auth.py"][0]["name"] == "login"

def test_pr_inventory_prioritizes_changed_files():
    from pipeline.domain.dev_tracking.nodes import _prioritize_inventory_for_pr

    inventory = {
        "files": [
            {"file": "src/unchanged.py", "internal_imports": []},
            {"file": "src/changed.py", "internal_imports": ["src/helper.py"]},
            {"file": "src/helper.py", "internal_imports": []},
        ],
        "symbols_by_file": {
            "src/changed.py": [{"name": "changed"}],
            "src/helper.py": [{"name": "helper"}],
            "src/unchanged.py": [{"name": "unchanged"}],
        },
        "summary": {"file_count": 3},
    }

    prioritized = _prioritize_inventory_for_pr(inventory, {"src/changed.py"}, max_files=3)

    assert prioritized["files"][0]["file"] == "src/changed.py"
    assert prioritized["files"][1]["file"] == "src/helper.py"
    assert prioritized["summary"]["changed_file_count"] == 1
    assert "src/changed.py" in prioritized["symbols_by_file"]

def test_split_text_chunks_keeps_chunks_under_limit():
    from pipeline.domain.dev_tracking.nodes import _split_text_chunks

    chunks = _split_text_chunks("\n".join(["x" * 20 for _ in range(10)]), 50)

    assert len(chunks) > 1
    assert all(len(chunk) <= 50 for chunk in chunks)
