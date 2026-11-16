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

def test_gap_analyzer_creates_high_missing_api_gap():
    from pipeline.domain.dev_tracking.nodes import gap_analyzer

    state = {
        "published_spec_snapshot": {
            "api_contracts": [{"method": "GET", "url": "/api/missing"}],
            "component_contracts": [],
        },
        "implementation_profile": {
            "detected_apis": [{"name": "list_users", "file": "app/api/users.py"}],
            "detected_components": [],
        },
        "spec_outdated": False,
    }

    result = gap_analyzer(state)

    assert result["status"] == "PASS"
    assert result["has_high_gap"] is True
    assert result["dev_tracking_next_action"] == "intent_classifier"
    assert result["gap_report"][0]["type"] == "MISSING_API"
    assert result["gap_report"][0]["severity"] == "HIGH"
    assert result["gap_report"][0]["preliminary"] is True

def test_forensic_profiler_uses_mocked_llm(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        implementation_profile = nodes.DevImplementationProfile(
            detected_apis=[{"name": "login", "file": "app/api/auth.py"}],
            detected_components=[],
            file_role_map={"app/api/auth.py": "api"},
            implementation_summary="LLM detected auth API.",
        )

    class Result:
        parsed = Parsed()
        usage = {"input_tokens": 10}
        cost = 0.0
        retry_count = 0

    def fake_call_structured(**kwargs):
        assert kwargs["schema"] is nodes.DevImplementationProfileResponse
        assert "code_inventory" in kwargs["user_msg"]
        return Result()

    monkeypatch.setattr(nodes, "_call_structured_for_forensic", fake_call_structured)

    result = nodes.forensic_profiler(
        {
            "api_key": "test-key",
            "model": "test-model",
            "code_inventory": {"files": [{"file": "app/api/auth.py"}], "symbols_by_file": {"app/api/auth.py": [{"name": "login"}]}},
            "compress_prompt": False,
        }
    )

    assert result["status"] == "PASS"
    assert result["forensic_profiler_meta"]["mode"] == "llm"
    assert result["forensic_profiler_meta"]["preliminary"] is False
    assert result["implementation_profile"]["implementation_summary"] == "LLM detected auth API."

def test_forensic_profiler_fallback_is_preliminary(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    def fake_call_structured(**kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(nodes, "_call_structured_for_forensic", fake_call_structured)

    result = nodes.forensic_profiler(
        {
            "api_key": "test-key",
            "code_inventory": {
                "files": [{"file": "app/api/auth.py"}],
                "symbols_by_file": {"app/api/auth.py": [{"name": "login"}]},
            },
            "compress_prompt": False,
        }
    )

    assert result["forensic_profiler_meta"]["mode"] == "rule_based_fallback"
    assert result["forensic_profiler_meta"]["preliminary"] is True
    assert result["forensic_profiler_meta"]["fallback_used"] is True
    assert result["llm_warnings"][0]["node"] == "forensic_profiler"

def test_forensic_profiler_explicit_llm_flag_false_skips_llm(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    def fake_call_structured(**kwargs):
        raise AssertionError("LLM should not be called when use_llm_forensic_profiler is false")

    monkeypatch.setattr(nodes, "_call_structured_for_forensic", fake_call_structured)

    result = nodes.forensic_profiler(
        {
            "api_key": "test-key",
            "use_llm_forensic_profiler": False,
            "code_inventory": {
                "files": [{"file": "app/api/auth.py"}],
                "symbols_by_file": {"app/api/auth.py": [{"name": "login"}]},
            },
        }
    )

    assert result["forensic_profiler_meta"]["mode"] == "rule_based"
    assert result["forensic_profiler_meta"]["llm_attempted"] is False
    assert result["forensic_profiler_meta"]["preliminary"] is True

def test_forensic_profiler_uses_env_key_when_request_key_missing(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        implementation_profile = nodes.DevImplementationProfile(
            detected_apis=[],
            detected_components=[],
            file_role_map={},
            implementation_summary="LLM used env key.",
        )

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    calls = {"forensic": 0}

    def fake_call_structured(**kwargs):
        calls["forensic"] += 1
        assert kwargs["api_key"] == ""
        return Result()

    monkeypatch.setenv("GEMINI_API_KEY", "env-test-key")
    monkeypatch.setattr(nodes, "_call_structured_for_forensic", fake_call_structured)

    result = nodes.forensic_profiler(
        {
            "code_inventory": {"files": [], "symbols_by_file": {}},
            "compress_prompt": False,
        }
    )

    assert calls["forensic"] == 1
    assert result["forensic_profiler_meta"]["mode"] == "llm"
    assert result["implementation_profile"]["implementation_summary"] == "LLM used env key."

def test_forensic_profiler_falls_back_when_llm_returns_unknown_file(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        implementation_profile = nodes.DevImplementationProfile(
            detected_apis=[{"name": "ghost_api", "file": "ghost/api.py"}],
            detected_components=[],
            file_role_map={"ghost/api.py": "api"},
            implementation_summary="Invalid invented file.",
        )

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_forensic", lambda **kwargs: Result())

    result = nodes.forensic_profiler(
        {
            "api_key": "test-key",
            "code_inventory": {
                "files": [{"file": "app/api/auth.py"}],
                "symbols_by_file": {"app/api/auth.py": [{"name": "login"}]},
            },
            "compress_prompt": False,
        }
    )

    assert result["forensic_profiler_meta"]["mode"] == "rule_based_fallback"
    assert result["forensic_profiler_meta"]["preliminary"] is True
    assert "unknown" in result["forensic_profiler_meta"]["llm_error_message"].lower()
    assert result["implementation_profile"]["detected_apis"][0]["file"] == "app/api/auth.py"

def test_forensic_profiler_falls_back_when_llm_detected_item_missing_name(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        implementation_profile = nodes.DevImplementationProfile(
            detected_apis=[{"name": "", "file": "app/api/auth.py"}],
            detected_components=[],
            file_role_map={"app/api/auth.py": "api"},
            implementation_summary="Invalid empty name.",
        )

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_forensic", lambda **kwargs: Result())

    result = nodes.forensic_profiler(
        {
            "api_key": "test-key",
            "code_inventory": {
                "files": [{"file": "app/api/auth.py"}],
                "symbols_by_file": {"app/api/auth.py": [{"name": "login"}]},
            },
            "compress_prompt": False,
        }
    )

    assert result["forensic_profiler_meta"]["mode"] == "rule_based_fallback"
    assert "without name/file" in result["forensic_profiler_meta"]["llm_error_message"]

def test_gap_analyzer_uses_mocked_llm(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        gaps = [
            nodes.DevGapItem(
                gap_id="GAP_LLM_001",
                severity="HIGH",
                type="MISSING_API",
                spec_target="get /api/auth",
                implementation_target=None,
                description="LLM detected missing API.",
                spec_outdated_related=False,
            )
        ]

    class Result:
        parsed = Parsed()
        usage = {"input_tokens": 10}
        cost = 0.0
        retry_count = 0

    def fake_call_structured(**kwargs):
        assert kwargs["schema"] is nodes.DevGapReportResponse
        assert "published_spec_snapshot" in kwargs["user_msg"]
        return Result()

    monkeypatch.setattr(nodes, "_call_structured_for_gap", fake_call_structured)

    result = nodes.gap_analyzer(
        {
            "api_key": "test-key",
            "published_spec_snapshot": {"api_contracts": [{"method": "GET", "url": "/api/auth"}]},
            "implementation_profile": {"detected_apis": []},
            "compress_prompt": False,
        }
    )

    assert result["gap_analyzer_meta"]["mode"] == "llm"
    assert result["gap_report"][0]["gap_id"] == "GAP_LLM_001"
    assert result["gap_report"][0]["preliminary"] is False

def test_gap_analyzer_fallback_marks_gaps_preliminary(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    def fake_call_structured(**kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(nodes, "_call_structured_for_gap", fake_call_structured)

    result = nodes.gap_analyzer(
        {
            "api_key": "test-key",
            "published_spec_snapshot": {"api_contracts": [{"method": "GET", "url": "/api/missing"}]},
            "implementation_profile": {"detected_apis": []},
            "compress_prompt": False,
        }
    )

    assert result["gap_analyzer_meta"]["mode"] == "rule_based_fallback"
    assert result["gap_report"][0]["preliminary"] is True
    assert result["llm_warnings"][0]["node"] == "gap_analyzer"

def test_gap_analyzer_explicit_llm_flag_false_skips_llm(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    def fake_call_structured(**kwargs):
        raise AssertionError("LLM should not be called when use_llm_gap_analyzer is false")

    monkeypatch.setattr(nodes, "_call_structured_for_gap", fake_call_structured)

    result = nodes.gap_analyzer(
        {
            "api_key": "test-key",
            "use_llm_gap_analyzer": False,
            "published_spec_snapshot": {"api_contracts": [{"method": "GET", "url": "/api/missing"}]},
            "implementation_profile": {"detected_apis": []},
        }
    )

    assert result["gap_analyzer_meta"]["mode"] == "rule_based"
    assert result["gap_analyzer_meta"]["llm_attempted"] is False
    assert result["gap_report"][0]["preliminary"] is True

def test_gap_analyzer_falls_back_when_llm_returns_duplicate_gap_ids(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        gaps = [
            nodes.DevGapItem(
                gap_id="GAP_DUP",
                severity="HIGH",
                type="MISSING_API",
                spec_target="get /api/auth",
                implementation_target=None,
                description="Missing auth API.",
            ),
            nodes.DevGapItem(
                gap_id="GAP_DUP",
                severity="MED",
                type="MISSING_COMPONENT",
                spec_target="AuthPanel",
                implementation_target=None,
                description="Missing auth panel.",
            ),
        ]

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_gap", lambda **kwargs: Result())

    result = nodes.gap_analyzer(
        {
            "api_key": "test-key",
            "published_spec_snapshot": {"api_contracts": [{"method": "GET", "url": "/api/auth"}]},
            "implementation_profile": {"detected_apis": []},
            "compress_prompt": False,
        }
    )

    assert result["gap_analyzer_meta"]["mode"] == "rule_based_fallback"
    assert result["gap_report"][0]["preliminary"] is True
    assert "duplicate" in result["gap_analyzer_meta"]["llm_error_message"].lower()

def test_gap_analyzer_falls_back_when_llm_returns_empty_but_rules_find_gap(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        gaps = []

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_gap", lambda **kwargs: Result())

    result = nodes.gap_analyzer(
        {
            "api_key": "test-key",
            "published_spec_snapshot": {"api_contracts": [{"method": "GET", "url": "/api/missing"}]},
            "implementation_profile": {"detected_apis": []},
            "compress_prompt": False,
        }
    )

    assert result["gap_analyzer_meta"]["mode"] == "rule_based_fallback"
    assert result["gap_report"][0]["type"] == "MISSING_API"
    assert result["gap_report"][0]["preliminary"] is True

def test_gap_analyzer_falls_back_when_llm_returns_empty_description(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        gaps = [
            nodes.DevGapItem(
                gap_id="GAP_001",
                severity="HIGH",
                type="MISSING_API",
                spec_target="get /api/auth",
                implementation_target=None,
                description="",
            )
        ]

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_gap", lambda **kwargs: Result())

    result = nodes.gap_analyzer(
        {
            "api_key": "test-key",
            "published_spec_snapshot": {"api_contracts": [{"method": "GET", "url": "/api/auth"}]},
            "implementation_profile": {"detected_apis": []},
            "compress_prompt": False,
        }
    )

    assert result["gap_analyzer_meta"]["mode"] == "rule_based_fallback"
    assert "required non-empty" in result["gap_analyzer_meta"]["llm_error_message"]

def test_gap_analyzer_blocks_empty_snapshot_even_when_llm_would_find_no_gap(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        gaps = []

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_gap", lambda **kwargs: Result())

    result = nodes.gap_analyzer(
        {
            "api_key": "test-key",
            "published_spec_snapshot": {"api_contracts": []},
            "implementation_profile": {"detected_apis": []},
            "compress_prompt": False,
        }
    )

    assert result["status"] == "FAIL"
    assert result["error_type"] == "SPEC_SNAPSHOT_UNVERIFIED"
    assert result["dev_tracking_next_action"] == "blocked"


def test_gap_analyzer_blocks_when_published_spec_has_no_contracts(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    monkeypatch.setattr(nodes, "_call_structured_for_gap", lambda **kwargs: (_ for _ in ()).throw(AssertionError("LLM must not run")))
    result = nodes.gap_analyzer({"api_key": "test-key", "published_spec_snapshot": {}})

    assert result["status"] == "FAIL"
    assert result["dev_tracking_next_action"] == "blocked"
    assert result["error_type"] == "SPEC_SNAPSHOT_UNVERIFIED"


def test_pm_report_does_not_clear_pr_without_code_evidence():
    from pipeline.domain.dev_tracking.nodes import pm_report_generator

    result = pm_report_generator({
        "gap_report": [],
        "published_spec_snapshot": {"api_contracts": [{"method": "GET", "url": "/health"}]},
        "code_inventory": {"files": [], "symbols_by_file": {}},
        "checkout": {"head_sha_matched": True},
        "forensic_profiler_meta": {"preliminary": False},
        "gap_analyzer_meta": {"preliminary": False},
    })

    assert result["approval_status"] == "PENDING_PM_APPROVAL"
    assert "PM_REVIEW" in result["pm_report"]["recommended_pm_actions"]


def test_forensic_profiler_rejects_symbol_absent_from_inventory(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        implementation_profile = nodes.DevImplementationProfile(
            detected_apis=[{"name": "ghost_api", "file": "app/api/auth.py"}],
            detected_components=[],
            file_role_map={"app/api/auth.py": "api"},
            implementation_summary="Claimed API.",
        )

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_forensic", lambda **kwargs: Result())
    result = nodes.forensic_profiler({
        "api_key": "test-key",
        "code_inventory": {
            "files": [{"file": "app/api/auth.py"}],
            "symbols_by_file": {"app/api/auth.py": [{"name": "login"}]},
        },
    })

    assert result["forensic_profiler_meta"]["mode"] == "rule_based_fallback"
    assert "symbol" in result["forensic_profiler_meta"]["llm_error_message"].lower()


def test_gap_analyzer_rejects_target_absent_from_snapshot(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        gaps = [nodes.DevGapItem(
            gap_id="GAP_FAKE", severity="HIGH", type="MISSING_API",
            spec_target="get /admin", description="Invented contract.",
        )]

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_gap", lambda **kwargs: Result())
    result = nodes.gap_analyzer({
        "api_key": "test-key",
        "published_spec_snapshot": {"api_contracts": [{"method": "GET", "url": "/health"}]},
        "implementation_profile": {"detected_apis": []},
    })

    assert result["gap_analyzer_meta"]["mode"] == "rule_based_fallback"
    assert result["gap_report"][0]["spec_target"] == "get /health"


def test_pm_report_clears_only_matching_verified_evidence():
    from pipeline.domain.dev_tracking.nodes import pm_report_generator

    result = pm_report_generator({
        "gap_report": [],
        "pr_context": {"owner": "xxrin", "repo": "navigator", "head_sha": "abc123"},
        "implementation_evidence": {"complete": True, "head_sha": "abc123", "files": [{"file": "app/api/health.py", "symbols": [{"name": "health", "start_line": 1}]}]},
        "published_spec_snapshot": {"api_contracts": [{"name": "health"}]},
        "code_inventory": {"files": [{"file": "app/api/health.py"}], "symbols_by_file": {"app/api/health.py": [{"name": "health"}]}},
        "implementation_profile": {"detected_apis": [{"name": "health", "file": "app/api/health.py"}]},
        "checkout": {"head_sha_matched": True},
        "forensic_profiler_meta": {"preliminary": False},
        "gap_analyzer_meta": {"preliminary": False},
    })

    assert result["approval_status"] == "NO_GAP_DETECTED"


def test_verified_no_gap_does_not_create_approval_task():
    from pipeline.domain.dev_tracking.task_flow import task_coordinator

    def unexpected_create_task(**kwargs):
        raise AssertionError("No PM approval task should be created")

    result = task_coordinator({
        "approval_status": "NO_GAP_DETECTED",
        "gap_report": [],
        "pr_context": {"owner": "xxrin", "repo": "navigator", "head_sha": "abc123"},
        "implementation_evidence": {"complete": True, "head_sha": "abc123", "files": [{"file": "app/api/health.py", "symbols": [{"name": "health", "start_line": 1}]}]},
        "published_spec_snapshot": {"api_contracts": [{"name": "health"}]},
        "code_inventory": {"files": [{"file": "app/api/health.py"}], "symbols_by_file": {"app/api/health.py": [{"name": "health"}]}},
        "implementation_profile": {"detected_apis": [{"name": "health", "file": "app/api/health.py"}]},
        "checkout": {"head_sha_matched": True},
        "forensic_profiler_meta": {"preliminary": False},
        "gap_analyzer_meta": {"preliminary": False},
    }, create_task=unexpected_create_task)

    assert result["status"] == "SKIPPED"
    assert result["approval_task"] is None
