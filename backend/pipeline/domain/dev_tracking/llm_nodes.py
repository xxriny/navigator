from __future__ import annotations

import json
from typing import Any, Callable

from version import DEFAULT_MODEL

from .analysis_rules import _contract_name
from .context_compaction import (
    _changed_file_set,
    _compress_if_available,
    _prioritize_inventory_for_pr,
)
from .llm import _llm_meta
from .schemas import (
    DevGapIntentResponse,
    DevGapReportResponse,
    DevImplementationProfileResponse,
)
from .evidence import implementation_evidence_from_state
from .security import (
    UNTRUSTED_DATA_BOUNDARY,
    build_safe_code_reference,
    build_safe_knowledge_reference,
    build_safe_spec_reference,
    build_untrusted_pr_reference,
)
from .utils import _as_dict


StructuredCall = Callable[..., Any]


def _safe_dev_knowledge_reference(state: dict[str, Any]) -> list[dict[str, Any]]:
    pr_context = _as_dict(state.get("pr_context"))
    dev_knowledge = _as_dict(state.get("dev_knowledge"))
    if not all((state.get("team_id"), pr_context.get("owner"), pr_context.get("repo"))):
        return []
    return build_safe_knowledge_reference(
        dev_knowledge.get("artifacts"),
        expected_team_id=str(state.get("team_id") or ""),
        expected_repository_owner=str(pr_context.get("owner") or ""),
        expected_repository_name=str(pr_context.get("repo") or ""),
    )


FORENSIC_PROFILE_PROMPT = (
    "중요: 모든 텍스트 필드(설명, 요약 등)는 반드시 한국어로 작성하세요.\n"
    "\n"
    "당신은 PR 분석을 위한 포렌식 코드 프로파일러입니다.\n"
    "\n"
    "목표:\n"
    "- PR에 포함된 소스 파일들을 분석하여 각 파일의 구현상 역할을 분류합니다.\n"
    "- API 후보와 UI/기능 컴포넌트 후보를 식별합니다.\n"
    "- 분석 결과는 반드시 기존 implementation_profile 스키마에 맞춰 반환합니다.\n"
    "\n"
    "분석 기준:\n"
    "- 파일 경로(path)\n"
    "- 파일명 및 디렉터리 구조\n"
    "- 클래스, 함수, 메서드, 라우터, 컨트롤러, 훅, 컴포넌트 등의 심볼(symbol)\n"
    "- 프로젝트 전체 컨텍스트\n"
    "- PR에서 변경된 코드의 목적과 영향 범위\n"
    "\n"
    "분류 지침:\n"
    "- API 엔드포인트, 라우터, 컨트롤러, 서비스 진입점은 detected_apis에 포함합니다.\n"
    "- React/Vue/Svelte 컴포넌트, 화면 단위 모듈, 재사용 가능한 UI 구성요소는 detected_components에 포함합니다.\n"
    "- 각 파일은 구현 목적에 따라 file_role_map에 역할을 매핑합니다.\n"
    "- 구현 요약은 implementation_summary에 간결하게 정리합니다.\n"
    "\n"
    "주의사항:\n"
    "- 추측만으로 존재하지 않는 API나 컴포넌트를 생성하지 마세요.\n"
    "- 테스트 파일, 설정 파일, 문서 파일은 실제 구현 역할과 구분해서 판단하세요.\n"
    "- 파일 내용보다 경로만으로 단정하지 말고, 가능한 경우 심볼과 PR 맥락을 함께 사용하세요.\n"
    "- 불확실한 경우에는 가장 보수적인 역할로 분류하세요.\n"
    "\n"
    "출력 제약:\n"
    "- 반드시 기존 implementation_profile shape만 반환하세요.\n"
    "- 반환 가능한 최상위 키는 다음 네 개뿐입니다:\n"
    "  1. detected_apis\n"
    "  2. detected_components\n"
    "  3. file_role_map\n"
    "  4. implementation_summary\n"
    "- 추가 설명, 마크다운, 자연어 해설, 별도 메타데이터를 출력하지 마세요.\n"
    "추가 주의 사항:\n"
    "- code_inventory나 pr_context가 비어있거나 null인 경우, "
    "존재하지 않는 API나 컴포넌트를 추측하지 말고 "
    "detected_apis와 detected_components를 빈 리스트로 반환하세요.\n"
    "- 입력 컨텍스트가 불완전해도 implementation_summary에 '데이터 없음'을 명시하세요.\n"
)


GAP_REPORT_PROMPT = (
    "중요: 모든 텍스트 필드(설명, 요약, gap 내용 등)는 반드시 한국어로 작성하세요.\n"
    "\n"
    "당신은 PR 구현 결과가 제품/기획 스펙을 충족하는지 검증하는 Gap Analysis Agent입니다.\n"
    "\n"
    "당신의 임무는 published product/spec snapshot과 PR implementation_profile을 비교하여 "
    "구체적인 gap_report를 생성하는 것입니다.\n"
    "\n"
    "## 선행 검사 (반드시 먼저 수행)\n"
    "분석을 시작하기 전에 published_spec_snapshot의 유효성을 확인하세요:\n"
    "- api_contracts와 component_contracts가 모두 비어있거나 null이면:\n"
    "  → gap_report를 빈 리스트([])로 반환하고 분석을 중단하세요.\n"
    "  → 스펙 부재 자체를 GAP으로 기록하지 마세요.\n"
    "- spec_outdated가 true이고 스펙이 비어있으면:\n"
    "  → 마찬가지로 빈 리스트를 반환하세요.\n"
    "\n"
    "당신의 임무는 published product/spec snapshot과 PR implementation_profile을 비교하여 "
    "구체적인 gap_report를 생성하는 것입니다.\n"
    "\n"
    "반드시 반환해야 하는 최상위 필드:\n"
    "- gap_report\n"
    "\n"
    "gap_report 작성 규칙:\n"
    "- gap_report는 GAP item들의 리스트입니다.\n"
    "- 각 GAP item은 스펙 요구사항과 PR 구현 프로필 간의 구체적인 차이를 설명해야 합니다.\n"
    "- GAP item은 반드시 입력으로 제공된 스펙 또는 implementation_profile에서 확인 가능한 근거에 기반해야 합니다.\n"
    "- 추측성 문제, 일반적인 개선 제안, 코드 스타일 의견은 GAP으로 작성하지 마세요.\n"
    "\n"
    "비교 대상:\n"
    "1. API 요구사항\n"
    "   - 스펙에 요구된 endpoint, route, controller, request/response contract, auth requirement를 확인합니다.\n"
    "   - implementation_profile.detected_apis에 해당 API가 없거나 핵심 계약이 확인되지 않으면 GAP으로 기록합니다.\n"
    "\n"
    "2. Component 요구사항\n"
    "   - 스펙에 요구된 화면, UI 컴포넌트, 기능 컴포넌트, 사용자 상호작용 단위를 확인합니다.\n"
    "   - implementation_profile.detected_components 또는 file_role_map에서 확인되지 않으면 GAP으로 기록합니다.\n"
    "\n"
    "3. File role / architecture 요구사항\n"
    "   - 스펙이 특정 계층, 모듈, 책임 분리, 데이터 흐름을 요구하는 경우 file_role_map과 implementation_summary를 비교합니다.\n"
    "   - 핵심 구조가 누락되었거나 역할이 불일치하면 GAP으로 기록합니다.\n"
    "\n"
    "4. Implementation summary\n"
    "   - PR 구현 요약이 스펙의 핵심 사용자 흐름 또는 기능 목표를 충족하는지 확인합니다.\n"
    "   - 구현 범위가 스펙 요구사항보다 부족하면 GAP으로 기록합니다.\n"
    "\n"
    "severity 분류 기준:\n"
    "- HIGH:\n"
    "  - 스펙상 필수 API가 누락된 경우\n"
    "  - request/response contract, 인증, 권한, 데이터 저장, 상태 전이 등 critical contract가 누락 또는 위반된 경우\n"
    "  - 주요 사용자 흐름이 동작할 수 없을 정도의 구현 누락이 있는 경우\n"
    "\n"
    "- MED:\n"
    "  - 스펙상 필요한 UI/기능 컴포넌트가 누락된 경우\n"
    "  - 핵심 API는 있으나 보조 컴포넌트, 화면, 연결 로직이 부족한 경우\n"
    "  - 기능은 일부 구현되었으나 스펙의 주요 요구를 완전히 충족하지 못하는 경우\n"
    "\n"
    "- LOW:\n"
    "  - 명명, 경로, 역할 분류, 요약 표현상의 경미한 불일치\n"
    "  - 기능 동작을 직접 막지는 않지만 스펙과 구현 설명 사이에 작은 차이가 있는 경우\n"
    "\n"
    "spec_outdated_related 사용 기준:\n"
    "- PR 구현이 스펙에 없는 최신 구조, 신규 API, 신규 컴포넌트, 변경된 책임 분리를 명확히 포함하는 경우 true 또는 관련 설명을 포함합니다.\n"
    "- 스펙이 최신 구현을 반영하지 못했을 가능성이 있는 GAP에만 사용합니다.\n"
    "- 단순 구현 누락이나 근거 없는 추측에는 사용하지 마세요.\n"
    "\n"
    "금지사항:\n"
    "- 자동 승인 여부를 판단하지 마세요.\n"
    "- merge 가능, approve 가능, reject 필요 같은 결론을 작성하지 마세요.\n"
    "- 입력에 없는 API, 컴포넌트, 요구사항을 만들어내지 마세요.\n"
    "- 단순 권장사항이나 리팩터링 의견을 GAP으로 만들지 마세요.\n"
    "- 분석 과정이나 내부 추론을 출력하지 마세요.\n"
    "\n"
    "출력은 반드시 gap_report shape만 따르세요.\n"
    "추가 금지사항:\n"
    "- 스펙 스냅샷이 비어있거나 로딩되지 않은 상태를 GAP으로 보고하지 마세요.\n"
    "- 비교 대상 스펙이 없는 경우 GAP을 생성하지 말고 빈 리스트를 반환하세요.\n"
    "- '스펙이 없다', '스펙을 확인할 수 없다'는 내용의 GAP item을 만들지 마세요.\n"
    "\n"
    "출력은 반드시 gap_report shape만 따르세요.\n"
)


INTENT_CLASSIFICATION_PROMPT = (
    "중요: 모든 텍스트 필드(이유, 설명 등)는 반드시 한국어로 작성하세요.\n"
    "\n"
    "당신은 Product Manager를 위한 PR Gap Intent Classification Agent입니다.\n"
    "\n"
    "## 선행 검사\n"
    "gap_report가 비어있으면([] 또는 null) classifications도 빈 리스트([])로 반환하세요.\n"
    "각 gap item이 실제 스펙 근거를 가지고 있는지 확인하세요:\n"
    "- spec_target이 null이거나 스펙 출처를 확인할 수 없는 gap은 UNCERTAIN + PM_REVIEW로 처리하세요.\n"
    "\n"
    "목표:\n"
    "- 입력으로 주어진 각 implementation gap이 의도된 변경인지, 의도하지 않은 누락인지, 판단 불가능한지 분류합니다.\n"
    "- 각 gap에 대해 반드시 하나의 classification만 반환합니다.\n"
    "- 각 classification은 입력 gap의 gap_id를 그대로 유지해야 합니다.\n"
    "\n"
    "분류 대상:\n"
    "- gap_report에서 생성된 개별 GAP item\n"
    "- PR 설명, 커밋 메시지, 변경 의도\n"
    "- spec_outdated_related evidence\n"
    "- product/spec snapshot과 implementation_profile 간의 차이\n"
    "\n"
    "intent 값:\n"
    "- INTENTIONAL: PR 설명, 변경 의도, 또는 spec_outdated evidence를 통해 해당 gap이 의도된 변경이라고 판단되는 경우\n"
    "- UNINTENTIONAL: 스펙상 필요한 구현이 누락되었고, PR 설명이나 outdated evidence에서도 의도된 변경 근거가 없는 경우\n"
    "- UNCERTAIN: 의도 여부를 판단하기에 근거가 부족하거나, 스펙과 PR 맥락이 충돌하는 경우\n"
    "\n"
    "recommended_action 값:\n"
    "- APPROVE_AS_INTENTIONAL: gap이 명확히 의도된 변경이며, PR 텍스트나 spec_outdated evidence로 뒷받침되는 경우\n"
    "- REQUEST_FIX: gap이 의도되지 않은 누락 또는 구현 실수로 보이는 경우\n"
    "- PM_REVIEW: 의도 여부가 불확실하거나 제품/스펙 판단이 필요한 경우\n"
    "\n"
    "판단 규칙:\n"
    "- 각 input gap마다 정확히 하나의 classification을 반환하세요.\n"
    "- classification의 gap_id는 input gap의 gap_id와 반드시 동일해야 합니다.\n"
    "- missing required API, critical contract, 인증/권한, 데이터 무결성 관련 gap은 기본적으로 UNINTENTIONAL로 판단합니다.\n"
    "- 단, PR 텍스트 또는 spec_outdated evidence가 해당 gap이 의도된 변경임을 명확히 뒷받침하는 경우에만 INTENTIONAL로 분류할 수 있습니다.\n"
    "- spec_outdated_related가 true라고 해서 자동으로 INTENTIONAL로 판단하지 마세요. 실제 근거가 있어야 합니다.\n"
    "- 근거가 부족한 경우 APPROVE_AS_INTENTIONAL을 사용하지 말고 PM_REVIEW를 권장하세요.\n"
    "\n"
    "recommended_action 매핑 기준:\n"
    "- intent가 INTENTIONAL이고 근거가 명확하면 recommended_action은 APPROVE_AS_INTENTIONAL입니다.\n"
    "- intent가 UNINTENTIONAL이면 recommended_action은 REQUEST_FIX입니다.\n"
    "- intent가 UNCERTAIN이면 recommended_action은 PM_REVIEW입니다.\n"
    "\n"
    "금지사항:\n"
    "- missing required API를 근거 없이 승인하지 마세요.\n"
    "- PR 텍스트나 spec_outdated evidence 없이 critical gap을 INTENTIONAL로 분류하지 마세요.\n"
    "- 입력에 없는 gap_id를 생성하지 마세요.\n"
    "- 하나의 gap에 여러 classification을 반환하지 마세요.\n"
    "- 전체 PR에 대한 approve/reject/merge 가능 여부를 판단하지 마세요.\n"
    "- 일반적인 개선 제안이나 리팩터링 의견을 추가하지 마세요.\n"
    "\n"
    "출력 제약:\n"
    "- 반드시 classification 결과만 반환하세요.\n"
    "- 추가 설명, 마크다운, 분석 과정, 종합 결론을 출력하지 마세요.\n"
)


def _llm_implementation_profile(
    state: dict[str, Any],
    *,
    call_structured_for_forensic: StructuredCall,
    validate_llm_implementation_profile: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    pr_context = _as_dict(state.get("pr_context"))
    changed_files = _changed_file_set(state)
    inventory = _prioritize_inventory_for_pr(_as_dict(state.get("code_inventory")), changed_files)
    context = {
        "code_inventory": build_safe_code_reference(
            implementation_evidence_from_state(state),
            inventory,
        ),
        "pr_reference": build_untrusted_pr_reference(pr_context),
        "changed_files": sorted(changed_files),
    }
    user_msg = json.dumps(context, ensure_ascii=False, default=str)
    preserve = [
        str(pr_context.get("branch_name") or ""),
        *[
            str(item.get("file") or "")
            for item in inventory.get("files", [])
            if isinstance(item, dict)
        ][:80],
    ]
    compressed_msg, compression = _compress_if_available(
        user_msg,
        enabled=bool(state.get("compress_prompt", False)),
        rate=float(state.get("compression_rate", 0.55) or 0.55),
        preserve=[item for item in preserve if item],
    )
    result = call_structured_for_forensic(
        api_key=str(state.get("api_key") or ""),
        model=str(state.get("model") or DEFAULT_MODEL),
        schema=DevImplementationProfileResponse,
        system_prompt=f"{FORENSIC_PROFILE_PROMPT}\n\n{UNTRUSTED_DATA_BOUNDARY}",
        user_msg=compressed_msg,
        temperature=0.1,
    )
    implementation_profile = validate_llm_implementation_profile(
        result.parsed.implementation_profile.model_dump(),
        state,
    )
    return implementation_profile, _llm_meta(
        "llm",
        attempted=True,
        fallback_used=False,
        preliminary=False,
        extra={
            "model": str(state.get("model") or DEFAULT_MODEL),
            "usage": result.usage,
            "cost": result.cost,
            "retry_count": result.retry_count,
            "compression": compression,
        },
    )


def _llm_gap_report(
    state: dict[str, Any],
    *,
    call_structured_for_gap: StructuredCall,
    validate_llm_gap_report: Callable[[list[dict[str, Any]], dict[str, Any]], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    snapshot = _as_dict(state.get("published_spec_snapshot"))
    changed_files = _changed_file_set(state)
    context = {
        "published_spec_snapshot": build_safe_spec_reference(snapshot),
        "implementation_evidence": build_safe_code_reference(
            implementation_evidence_from_state(state),
            _as_dict(state.get("code_inventory")),
        ),
        "spec_outdated": bool(state.get("spec_outdated")),
        "dev_knowledge": _safe_dev_knowledge_reference(state),
        "changed_files": sorted(changed_files),
    }
    user_msg = json.dumps(context, ensure_ascii=False, default=str)
    preserve = [
        *[
            _contract_name(item)
            for item in snapshot.get("api_contracts", [])
            if isinstance(item, dict)
        ],
        *[
            _contract_name(item)
            for item in snapshot.get("component_contracts", [])
            if isinstance(item, dict)
        ],
    ]
    compressed_msg, compression = _compress_if_available(
        user_msg,
        enabled=bool(state.get("compress_prompt", False)),
        rate=float(state.get("compression_rate", 0.55) or 0.55),
        preserve=[item for item in preserve if item],
    )
    result = call_structured_for_gap(
        api_key=str(state.get("api_key") or ""),
        model=str(state.get("model") or DEFAULT_MODEL),
        schema=DevGapReportResponse,
        system_prompt=f"{GAP_REPORT_PROMPT}\n\n{UNTRUSTED_DATA_BOUNDARY}",
        user_msg=compressed_msg,
        temperature=0.1,
    )
    gaps = validate_llm_gap_report(
        [item.model_dump() for item in result.parsed.gaps],
        state,
    )
    return gaps, _llm_meta(
        "llm",
        attempted=True,
        fallback_used=False,
        preliminary=False,
        extra={
            "model": str(state.get("model") or DEFAULT_MODEL),
            "usage": result.usage,
            "cost": result.cost,
            "retry_count": result.retry_count,
            "compression": compression,
        },
    )


def _llm_intent_classification(
    state: dict[str, Any],
    *,
    call_structured_for_intent: StructuredCall,
    validate_llm_intent_classification: Callable[[list[dict[str, Any]], list[dict[str, Any]]], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pr_context = _as_dict(state.get("pr_context"))
    gaps = state.get("gap_report") or []
    preserve = [
        str(pr_context.get("branch_name") or ""),
        *[str(gap.get("gap_id") or "") for gap in gaps if isinstance(gap, dict)],
        *[str(gap.get("spec_target") or "") for gap in gaps if isinstance(gap, dict)],
    ]
    context = {
        "pr_reference": build_untrusted_pr_reference(pr_context),
        "gap_report": gaps,
        "implementation_evidence": build_safe_code_reference(
            implementation_evidence_from_state(state),
            _as_dict(state.get("code_inventory")),
        ),
        "dev_tracking_knowledge": _safe_dev_knowledge_reference(state),
        "published_spec_snapshot": build_safe_spec_reference(
            _as_dict(state.get("published_spec_snapshot"))
        ),
        "spec_outdated": bool(state.get("spec_outdated")),
    }
    user_msg = json.dumps(context, ensure_ascii=False, default=str)
    compressed_msg, compression = _compress_if_available(
        user_msg,
        enabled=bool(state.get("compress_prompt", False)),
        rate=float(state.get("compression_rate", 0.55) or 0.55),
        preserve=[item for item in preserve if item],
    )
    result = call_structured_for_intent(
        api_key=str(state.get("api_key") or ""),
        model=str(state.get("model") or DEFAULT_MODEL),
        schema=DevGapIntentResponse,
        system_prompt=f"{INTENT_CLASSIFICATION_PROMPT}\n\n{UNTRUSTED_DATA_BOUNDARY}",
        user_msg=compressed_msg,
        temperature=0.1,
    )
    classifications = validate_llm_intent_classification(
        [item.model_dump() for item in result.parsed.classifications],
        gaps,
    )
    return classifications, _llm_meta(
        "llm",
        attempted=True,
        fallback_used=False,
        preliminary=False,
        extra={
            "model": str(state.get("model") or DEFAULT_MODEL),
            "usage": result.usage,
            "cost": result.cost,
            "retry_count": result.retry_count,
            "compression": compression,
        },
    )
