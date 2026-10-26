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

def test_intent_classifier_uses_mocked_llm(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        classifications = [
            nodes.DevGapIntentItem(
                gap_id="GAP_001",
                intent="INTENTIONAL",
                confidence=0.91,
                reason="PR description explicitly mentions this contract change.",
                recommended_action="PM_REVIEW",
            )
        ]

    class Result:
        parsed = Parsed()
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        cost = 0.0
        retry_count = 0

    def fake_call_structured(**kwargs):
        assert kwargs["schema"] is nodes.DevGapIntentResponse
        assert "GAP_001" in kwargs["user_msg"]
        return Result()

    monkeypatch.setattr(nodes, "_call_structured_for_intent", fake_call_structured)

    result = nodes.intent_classifier(
        {
            "api_key": "test-key",
            "model": "test-model",
            "pr_context": {
                "branch_name": "feature/auth",
                "title": "Intentional auth contract change",
                "description": "Auth contract change is intentional.",
            },
            "gap_report": [
                {
                    "gap_id": "GAP_001",
                    "severity": "HIGH",
                    "type": "MISSING_API",
                    "spec_target": "get /api/auth",
                }
            ],
            "implementation_profile": {"implementation_summary": "Auth code changed."},
            "compress_prompt": False,
        }
    )

    assert result["status"] == "PASS"
    assert result["intent_classifier_meta"]["mode"] == "llm"
    assert result["intent_classification"][0]["intent"] == "INTENTIONAL"
    assert result["intent_classification"][0]["recommended_action"] == "PM_REVIEW"

def test_intent_classifier_falls_back_when_llm_fails(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    def fake_call_structured(**kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(nodes, "_call_structured_for_intent", fake_call_structured)

    result = nodes.intent_classifier(
        {
            "api_key": "test-key",
            "pr_context": {"title": "No mention", "description": ""},
            "gap_report": [
                {
                    "gap_id": "GAP_001",
                    "severity": "HIGH",
                    "type": "MISSING_API",
                    "spec_target": "get /api/auth",
                }
            ],
            "compress_prompt": False,
        }
    )

    assert result["status"] == "PASS"
    assert result["intent_classifier_meta"]["mode"] == "rule_based_fallback"
    assert result["intent_classifier_meta"]["preliminary"] is True
    assert result["intent_classification"][0]["intent"] == "UNCERTAIN"
    assert result["intent_classification"][0]["confidence"] <= 0.5
    assert result["intent_classification"][0]["recommended_action"] == "PM_REVIEW"
    assert result["intent_classification"][0]["preliminary"] is True

def test_intent_classifier_explicit_llm_flag_false_skips_llm(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    def fake_call_structured(**kwargs):
        raise AssertionError("LLM should not be called when use_llm_intent_classifier is false")

    monkeypatch.setattr(nodes, "_call_structured_for_intent", fake_call_structured)

    result = nodes.intent_classifier(
        {
            "api_key": "test-key",
            "use_llm_intent_classifier": False,
            "gap_report": [
                {
                    "gap_id": "GAP_001",
                    "severity": "HIGH",
                    "type": "MISSING_API",
                    "spec_target": "get /api/auth",
                }
            ],
        }
    )

    assert result["intent_classifier_meta"]["mode"] == "rule_based"
    assert result["intent_classifier_meta"]["llm_attempted"] is False
    assert result["intent_classification"][0]["intent"] == "UNCERTAIN"
    assert result["intent_classification"][0]["recommended_action"] == "PM_REVIEW"

def test_intent_classifier_falls_back_on_duplicate_llm_gap_id(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        classifications = [
            nodes.DevGapIntentItem(
                gap_id="GAP_001",
                intent="UNINTENTIONAL",
                confidence=0.8,
                reason="Missing API.",
                recommended_action="REQUEST_FIX",
            ),
            nodes.DevGapIntentItem(
                gap_id="GAP_001",
                intent="UNCERTAIN",
                confidence=0.5,
                reason="Duplicate.",
                recommended_action="PM_REVIEW",
            ),
        ]

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_intent", lambda **kwargs: Result())

    result = nodes.intent_classifier(
        {
            "api_key": "test-key",
            "gap_report": [
                {"gap_id": "GAP_001", "severity": "HIGH", "type": "MISSING_API", "spec_target": "get /api/auth"}
            ],
            "compress_prompt": False,
        }
    )

    assert result["intent_classifier_meta"]["mode"] == "rule_based_fallback"
    assert result["intent_classification"][0]["intent"] == "UNCERTAIN"
    assert result["intent_classification"][0]["recommended_action"] == "PM_REVIEW"
    assert "duplicate" in result["intent_classifier_meta"]["llm_error_message"].lower()

def test_intent_classifier_falls_back_on_unknown_llm_gap_id(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        classifications = [
            nodes.DevGapIntentItem(
                gap_id="GAP_UNKNOWN",
                intent="UNINTENTIONAL",
                confidence=0.8,
                reason="Unknown gap.",
                recommended_action="REQUEST_FIX",
            )
        ]

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_intent", lambda **kwargs: Result())

    result = nodes.intent_classifier(
        {
            "api_key": "test-key",
            "gap_report": [
                {"gap_id": "GAP_001", "severity": "HIGH", "type": "MISSING_API", "spec_target": "get /api/auth"}
            ],
            "compress_prompt": False,
        }
    )

    assert result["intent_classifier_meta"]["mode"] == "rule_based_fallback"
    assert result["intent_classification"][0]["recommended_action"] == "PM_REVIEW"
    assert "unknown" in result["intent_classifier_meta"]["llm_error_message"].lower()

def test_intent_classifier_falls_back_when_llm_misses_gap_id(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        classifications = [
            nodes.DevGapIntentItem(
                gap_id="GAP_001",
                intent="UNINTENTIONAL",
                confidence=0.8,
                reason="Missing first API.",
                recommended_action="REQUEST_FIX",
            )
        ]

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_intent", lambda **kwargs: Result())

    result = nodes.intent_classifier(
        {
            "api_key": "test-key",
            "gap_report": [
                {"gap_id": "GAP_001", "severity": "HIGH", "type": "MISSING_API", "spec_target": "get /api/auth"},
                {"gap_id": "GAP_002", "severity": "HIGH", "type": "MISSING_API", "spec_target": "get /api/users"},
            ],
            "compress_prompt": False,
        }
    )

    assert result["intent_classifier_meta"]["mode"] == "rule_based_fallback"
    assert {item["gap_id"] for item in result["intent_classification"]} == {"GAP_001", "GAP_002"}
    assert all(item["recommended_action"] == "PM_REVIEW" for item in result["intent_classification"])
    assert "missed" in result["intent_classifier_meta"]["llm_error_message"].lower()

def test_intent_classifier_falls_back_when_llm_approves_preliminary_gap(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        classifications = [
            nodes.DevGapIntentItem(
                gap_id="GAP_001",
                intent="INTENTIONAL",
                confidence=0.9,
                reason="Claims this is intentional.",
                recommended_action="APPROVE_AS_INTENTIONAL",
            )
        ]

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_intent", lambda **kwargs: Result())

    result = nodes.intent_classifier(
        {
            "api_key": "test-key",
            "gap_report": [
                {
                    "gap_id": "GAP_001",
                    "severity": "HIGH",
                    "type": "MISSING_API",
                    "spec_target": "get /api/auth",
                    "preliminary": True,
                }
            ],
            "compress_prompt": False,
        }
    )

    assert result["intent_classifier_meta"]["mode"] == "rule_based_fallback"
    assert result["intent_classification"][0]["intent"] == "UNCERTAIN"
    assert result["intent_classification"][0]["recommended_action"] == "PM_REVIEW"
    assert "preliminary" in result["intent_classifier_meta"]["llm_error_message"].lower()

def test_intent_classifier_falls_back_when_llm_reason_is_empty(monkeypatch):
    from pipeline.domain.dev_tracking import nodes

    class Parsed:
        classifications = [
            nodes.DevGapIntentItem(
                gap_id="GAP_001",
                intent="UNINTENTIONAL",
                confidence=0.8,
                reason="",
                recommended_action="REQUEST_FIX",
            )
        ]

    class Result:
        parsed = Parsed()
        usage = {}
        cost = 0.0
        retry_count = 0

    monkeypatch.setattr(nodes, "_call_structured_for_intent", lambda **kwargs: Result())

    result = nodes.intent_classifier(
        {
            "api_key": "test-key",
            "gap_report": [
                {"gap_id": "GAP_001", "severity": "HIGH", "type": "MISSING_API", "spec_target": "get /api/auth"}
            ],
            "compress_prompt": False,
        }
    )

    assert result["intent_classifier_meta"]["mode"] == "rule_based_fallback"
    assert result["intent_classification"][0]["recommended_action"] == "PM_REVIEW"
    assert "empty reason" in result["intent_classifier_meta"]["llm_error_message"]

def test_intent_classifier_uses_dev_knowledge_for_rule_based_decision():
    from pipeline.domain.dev_tracking import nodes

    result = nodes.intent_classifier(
        {
            "pr_context": {"title": "Auth work", "description": ""},
            "dev_knowledge_context": (
                "[DEV_GAP_DECISION] xxrin/navigator PR #11 "
                "(APPROVED_INTENTIONAL_CHANGE)\n"
                "spec_target: get /api/auth\n"
                "decision_status: APPROVED_INTENTIONAL_CHANGE"
            ),
            "gap_report": [
                {
                    "gap_id": "GAP_001",
                    "severity": "HIGH",
                    "type": "MISSING_API",
                    "spec_target": "get /api/auth",
                }
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["intent_classifier_meta"]["mode"] == "rule_based"
    assert result["intent_classifier_meta"]["preliminary"] is True
    assert result["intent_classification"][0]["intent"] == "UNCERTAIN"
    assert result["intent_classification"][0]["recommended_action"] == "PM_REVIEW"
    assert "Existing PM decision" in result["intent_classification"][0]["reason"]
