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

class _FakeWebhookUser:
    id = "user-1"
    team_id = "team-user"
    role = "software_engineer"


class _FakeQuery:
    def __init__(self, first_value=None):
        self.first_value = first_value

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.first_value


_DEFAULT_WEBHOOK_USER = object()


class _FakeWebhookSharedDB:
    # author: xxrin
    # webhook RBAC와 duplicate check가 같은 shared_db에서 순서대로 실행되는 상황을 재현한다.
    def __init__(self, *, user=_DEFAULT_WEBHOOK_USER, duplicate=None):
        self.user = _FakeWebhookUser() if user is _DEFAULT_WEBHOOK_USER else user
        self.duplicate = duplicate

    def query(self, model):
        if getattr(model, "__name__", "") == "User":
            return _FakeQuery(self.user)
        return _FakeQuery(self.duplicate)


def test_github_webhook_signature_validation_accepts_valid_signature():
    from transport.rest_handler import _verify_github_webhook_signature

    body = b'{"action":"opened"}'
    secret = "webhook-secret"
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    verified, error = _verify_github_webhook_signature(body, signature, secret)

    assert verified is True
    assert error == ""

def test_github_webhook_signature_validation_rejects_invalid_signature():
    from transport.rest_handler import _verify_github_webhook_signature

    verified, error = _verify_github_webhook_signature(
        b'{"action":"opened"}',
        "sha256=bad",
        "webhook-secret",
    )

    assert verified is False
    assert "invalid" in error.lower()

def test_normalize_github_pr_webhook_maps_to_dev_tracking_shape():
    from transport.rest_handler import _normalize_github_pr_webhook

    normalized = _normalize_github_pr_webhook(_github_pr_payload())

    assert normalized["trigger"] == "GITHUB_PR_WEBHOOK"
    assert normalized["repository"] == {"owner": "xxrin", "repo": "navigator"}
    assert normalized["pull_request"]["pr_number"] == 17
    assert normalized["pull_request"]["branch_name"] == "feature/dev-tracking"
    assert normalized["pull_request"]["base_branch"] == "main"
    assert normalized["pull_request"]["head_sha"] == "abc123"
    assert normalized["actor"]["github_id"] == "xxrin"

def test_github_pulls_endpoint_returns_open_pr_shape(monkeypatch, github_oauth_user):
    import connectors.github_connector as github_connector
    from transport.rest_handler import GitHubPullsRequest, github_pulls

    captured = {}

    class FakeGitHubConnector:
        def __init__(self, token):
            captured["token"] = token

        def list_pull_requests(self, owner, repo, state, limit):
            captured["args"] = (owner, repo, state, limit)
            # author: xxrin
            # UI가 PR 선택만으로 Dev Tracking 실행값을 채울 수 있는 응답 shape를 보장한다.
            return [
                types.SimpleNamespace(
                    number=17,
                    title="Dev tracking webhook",
                    state="open",
                    author="xxrin",
                    head_branch="feature/dev-tracking",
                    base_branch="main",
                    head_sha="abc123",
                    updated_at="2026-05-24T00:00:00",
                    url="https://github.com/xxrin/navigator/pull/17",
                )
            ]

    monkeypatch.setattr(github_connector, "GitHubConnector", FakeGitHubConnector)

    result = asyncio.run(github_pulls(
        GitHubPullsRequest(owner="xxrin", repo="navigator", state="open", limit=10),
        current_user=github_oauth_user,
    ))

    assert result["status"] == "ok"
    assert captured["token"] == "token-123"
    assert captured["args"] == ("xxrin", "navigator", "open", 10)
    assert result["data"][0]["number"] == 17
    assert result["data"][0]["head_branch"] == "feature/dev-tracking"
    assert result["data"][0]["base_branch"] == "main"
    assert result["data"][0]["head_sha"] == "abc123"

def test_github_branches_endpoint_keeps_head_sha_for_ui(monkeypatch, github_oauth_user):
    import connectors.github_connector as github_connector
    from transport.rest_handler import GitHubAnalyticsRequest, github_branches

    class FakeGitHubConnector:
        def __init__(self, token):
            self.token = token

        def list_branches(self, owner, repo):
            # author: xxrin
            # Branch picker가 선택값만으로 head_sha를 채울 수 있는 응답을 유지한다.
            return [{"name": "feature/dev-tracking", "protected": False, "sha": "abc123"}]

    monkeypatch.setattr(github_connector, "GitHubConnector", FakeGitHubConnector)

    result = asyncio.run(github_branches(
        GitHubAnalyticsRequest(owner="xxrin", repo="navigator", branch="main", limit=100),
        current_user=github_oauth_user,
    ))

    assert result["status"] == "ok"
    assert result["data"][0]["name"] == "feature/dev-tracking"
    assert result["data"][0]["sha"] == "abc123"

def test_github_webhook_endpoint_ignores_non_pr_event():
    from transport.rest_handler import github_webhook_endpoint

    class FakeRequest:
        headers = {"X-GitHub-Event": "push"}

        async def body(self):
            return json.dumps({"ref": "refs/heads/main"}).encode("utf-8")

    result = asyncio.run(github_webhook_endpoint(FakeRequest(), shared_db=None))

    assert result["status"] == "ok"
    assert result["handled"] is False
    assert "ignored event" in result["reason"]

def test_github_webhook_endpoint_ignores_unsupported_pr_action():
    from transport.rest_handler import github_webhook_endpoint

    class FakeRequest:
        headers = {"X-GitHub-Event": "pull_request"}

        async def body(self):
            return json.dumps(_github_pr_payload(action="closed")).encode("utf-8")

    result = asyncio.run(github_webhook_endpoint(FakeRequest(), shared_db=None))

    assert result["status"] == "ok"
    assert result["handled"] is False
    assert "ignored pull_request action" in result["reason"]

def test_github_webhook_endpoint_runs_dev_tracking_for_opened_pr(monkeypatch):
    import pipeline.domain.dev_tracking as dev_tracking
    from transport.rest_handler import github_webhook_endpoint

    captured = {}

    def fake_run_dev_tracking_analysis(payload, *, shared_db=None):
        captured.update(payload)
        return {"status": "pending_pm_approval", "timeline": [], "data": {}}

    monkeypatch.setattr(dev_tracking, "run_dev_tracking_analysis", fake_run_dev_tracking_analysis)
    monkeypatch.setenv("NAVIGATOR_GITHUB_TOKEN", "token-123")
    monkeypatch.setenv("NAVIGATOR_DEFAULT_TEAM_ID", "team-1")
    monkeypatch.delenv("NAVIGATOR_GITHUB_WEBHOOK_SECRET", raising=False)

    class FakeRequest:
        headers = {"X-GitHub-Event": "pull_request"}

        async def body(self):
            return json.dumps(_github_pr_payload(action="opened")).encode("utf-8")

    result = asyncio.run(github_webhook_endpoint(FakeRequest(), shared_db=_FakeWebhookSharedDB()))

    assert result["status"] == "ok"
    assert result["handled"] is True
    assert result["signature_verified"] is False
    assert captured["repository"] == {"owner": "xxrin", "repo": "navigator"}
    assert captured["pull_request"]["pr_number"] == 17
    assert captured["source_dir"] == ""
    assert captured["github_oauth_token"] == "token-123"
    assert captured["notify_pr"] is True
    assert captured["team_id"] == "team-1"
    assert captured["created_by"] == "user-1"

def test_github_webhook_endpoint_rejects_unlinked_actor(monkeypatch):
    import pipeline.domain.dev_tracking as dev_tracking
    from transport.rest_handler import github_webhook_endpoint

    called = {"run": False}

    def fake_run_dev_tracking_analysis(payload, *, shared_db=None):
        called["run"] = True
        return {"status": "pending_pm_approval", "timeline": [], "data": {}}

    monkeypatch.setattr(dev_tracking, "run_dev_tracking_analysis", fake_run_dev_tracking_analysis)
    monkeypatch.delenv("NAVIGATOR_GITHUB_WEBHOOK_SECRET", raising=False)

    class FakeRequest:
        headers = {"X-GitHub-Event": "pull_request"}

        async def body(self):
            return json.dumps(_github_pr_payload(action="opened")).encode("utf-8")

    result = asyncio.run(github_webhook_endpoint(FakeRequest(), shared_db=_FakeWebhookSharedDB(user=None)))

    assert result["status"] == "error"
    assert result["handled"] is False
    assert "linked" in result["error"]
    assert called["run"] is False

def test_github_webhook_endpoint_rejects_bad_signature(monkeypatch):
    from transport.rest_handler import github_webhook_endpoint

    monkeypatch.setenv("NAVIGATOR_GITHUB_WEBHOOK_SECRET", "webhook-secret")

    class FakeRequest:
        headers = {
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=bad",
        }

        async def body(self):
            return json.dumps(_github_pr_payload(action="opened")).encode("utf-8")

    result = asyncio.run(github_webhook_endpoint(FakeRequest(), shared_db=None))

    assert result["status"] == "error"
    assert result["handled"] is False
    assert result["signature_verified"] is False

def test_github_webhook_endpoint_skips_duplicate_head_sha(monkeypatch):
    import pipeline.domain.dev_tracking as dev_tracking
    from transport.rest_handler import github_webhook_endpoint

    class FakeAnalysis:
        def __init__(self):
            self.created_at = None

    called = {"run": False}

    def fake_run_dev_tracking_analysis(payload, *, shared_db=None):
        called["run"] = True
        return {"status": "pending_pm_approval", "timeline": [], "data": {}}

    monkeypatch.setattr(dev_tracking, "run_dev_tracking_analysis", fake_run_dev_tracking_analysis)

    class FakeRequest:
        headers = {"X-GitHub-Event": "pull_request"}

        async def body(self):
            return json.dumps(_github_pr_payload(action="opened")).encode("utf-8")

    result = asyncio.run(github_webhook_endpoint(
        FakeRequest(),
        shared_db=_FakeWebhookSharedDB(duplicate=FakeAnalysis()),
    ))

    assert result["status"] == "ok"
    assert result["handled"] is False
    assert "duplicate head_sha" in result["reason"]
    assert called["run"] is False

def test_pr_status_check_updater_sets_pending_for_pm_approval(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    captured = {}

    def fake_run_gh(args, cwd, input_text=None):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["payload"] = json.loads(input_text)
        return 0, "created", ""

    monkeypatch.setattr(nodes, "_run_gh", fake_run_gh)

    result = nodes.pr_status_check_updater(
        {
            "notify_pr": True,
            "source_dir": "E:/navigator_v2/KNU-PROJECT",
            "approval_status": "PENDING_PM_APPROVAL",
            "pr_context": {
                "owner": "xxrin",
                "repo": "navigator",
                "head_sha": "abc123",
            },
        }
    )

    assert result["status"] == "PASS"
    assert captured["args"][0] == "api"
    assert captured["args"][1] == "repos/xxrin/navigator/statuses/abc123"
    assert captured["payload"]["state"] == "pending"
    assert captured["payload"]["context"] == "NAVIGATOR Dev Tracking"

def test_pr_status_check_updater_sets_success_for_no_gap(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    captured = {}

    def fake_run_gh(args, cwd, input_text=None):
        captured["payload"] = json.loads(input_text)
        return 0, "created", ""

    monkeypatch.setattr(nodes, "_run_gh", fake_run_gh)

    result = nodes.pr_status_check_updater(
        {
            "notify_pr": True,
            "approval_status": "NO_GAP_DETECTED",
            "implementation_evidence": {"complete": True, "head_sha": "abc123", "files": [{"file": "app/api/health.py", "symbols": [{"name": "health", "start_line": 1}]}]},
            "published_spec_snapshot": {"api_contracts": [{"name": "health"}]},
            "code_inventory": {"files": [{"file": "app/api/health.py"}], "symbols_by_file": {"app/api/health.py": [{"name": "health"}]}},
            "implementation_profile": {"detected_apis": [{"name": "health", "file": "app/api/health.py"}]},
            "checkout": {"head_sha_matched": True},
            "forensic_profiler_meta": {"preliminary": False},
            "gap_analyzer_meta": {"preliminary": False},
            "pr_context": {
                "owner": "xxrin",
                "repo": "navigator",
                "head_sha": "abc123",
            },
        }
    )

    assert result["status"] == "PASS"
    assert captured["payload"]["state"] == "success"


def test_pr_status_check_updater_does_not_publish_success_without_evidence(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    captured = {}
    def fake_run_gh(args, cwd, input_text=None):
        captured["state"] = json.loads(input_text)["state"]
        return 0, "created", ""

    monkeypatch.setattr(nodes, "_run_gh", fake_run_gh)
    nodes.pr_status_check_updater({
        "notify_pr": True,
        "approval_status": "NO_GAP_DETECTED",
        "pr_context": {"owner": "xxrin", "repo": "navigator", "head_sha": "abc123"},
    })
    assert captured["state"] == "pending"

def test_pr_status_check_updater_warns_when_gh_fails(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    def fake_run_gh(args, cwd, input_text=None):
        return 1, "", "gh auth required"

    monkeypatch.setattr(nodes, "_run_gh", fake_run_gh)

    result = nodes.pr_status_check_updater(
        {
            "notify_pr": True,
            "approval_status": "PENDING_PM_APPROVAL",
            "pr_context": {
                "owner": "xxrin",
                "repo": "navigator",
                "head_sha": "abc123",
            },
        }
    )

    assert result["status"] == "WARN"
    assert result["pr_status_check"]["status_updated"] is False
    assert result["pr_status_check"]["error"] == "gh auth required"

def test_run_gh_degrades_when_cli_missing(monkeypatch):
    import subprocess
    from pipeline.domain.dev_tracking import nodes

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", fake_run)

    code, out, err = nodes._run_gh(["pr", "comment", "1"], ".")

    assert code == 127
    assert out == ""
    assert "gh CLI" in err
