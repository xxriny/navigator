from __future__ import annotations

from typing import Any

from .utils import _as_dict


def _rule_based_implementation_profile(state: dict[str, Any]) -> dict[str, Any]:
    inventory = _as_dict(state.get("code_inventory"))
    files = inventory.get("files", []) if isinstance(inventory.get("files"), list) else []
    symbols_by_file = _as_dict(inventory.get("symbols_by_file"))

    detected_apis: list[dict[str, Any]] = []
    detected_components: list[dict[str, Any]] = []
    file_role_map: dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("file") or "")
        lower = path.lower()
        role = "module"
        if any(token in lower for token in ("/api/", "router", "route", "endpoint")):
            role = "api"
            for symbol in symbols_by_file.get(path, []):
                detected_apis.append({"name": symbol.get("name"), "file": path})
        elif lower.endswith((".jsx", ".tsx")) or "/components/" in lower:
            role = "component"
            for symbol in symbols_by_file.get(path, []):
                name = str(symbol.get("name") or "")
                if name[:1].isupper():
                    detected_components.append({"name": name, "file": path})
        elif "test" in lower:
            role = "test"
        elif lower.endswith((".md", ".mdx")):
            role = "doc"
        file_role_map[path] = role

    return {
        "detected_apis": detected_apis,
        "detected_components": detected_components,
        "file_role_map": file_role_map,
        "implementation_summary": (
            f"Detected {len(files)} files, {len(detected_apis)} API candidates, "
            f"and {len(detected_components)} component candidates."
        ),
    }


def _contract_name(contract: dict[str, Any]) -> str:
    for key in ("name", "id", "path", "endpoint", "route", "method_path"):
        value = contract.get(key)
        if value:
            return str(value).lower()
    method = contract.get("method")
    url = contract.get("url") or contract.get("uri")
    if method or url:
        return f"{method or ''} {url or ''}".strip().lower()
    return ""


def _rule_based_gap_report(state: dict[str, Any], *, preliminary: bool = False) -> list[dict[str, Any]]:
    snapshot = _as_dict(state.get("published_spec_snapshot"))
    profile = _as_dict(state.get("implementation_profile"))
    spec_apis = snapshot.get("api_contracts") or []
    spec_components = snapshot.get("component_contracts") or []
    detected_apis = profile.get("detected_apis") or []
    detected_components = profile.get("detected_components") or []

    detected_api_text = " ".join(
        f"{item.get('name', '')} {item.get('file', '')}".lower()
        for item in detected_apis
        if isinstance(item, dict)
    )
    detected_component_names = {
        str(item.get("name") or "").lower()
        for item in detected_components
        if isinstance(item, dict)
    }

    gaps: list[dict[str, Any]] = []
    index = 1
    for contract in spec_apis:
        if not isinstance(contract, dict):
            continue
        name = _contract_name(contract)
        if name and name not in detected_api_text:
            gaps.append(
                {
                    "gap_id": f"GAP_{index:03d}",
                    "severity": "HIGH",
                    "type": "MISSING_API",
                    "spec_target": name,
                    "implementation_target": None,
                    "description": f"스펙의 API '{name}'가 구현 정보에서 발견되지 않았습니다.",
                    "spec_outdated_related": bool(state.get("spec_outdated")),
                    "preliminary": preliminary,
                }
            )
            index += 1
    for contract in spec_components:
        if not isinstance(contract, dict):
            continue
        name = _contract_name(contract)
        if name and name not in detected_component_names:
            gaps.append(
                {
                    "gap_id": f"GAP_{index:03d}",
                    "severity": "MED",
                    "type": "MISSING_COMPONENT",
                    "spec_target": name,
                    "implementation_target": None,
                    "description": f"스펙의 컴포넌트 '{name}'가 구현 정보에서 발견되지 않았습니다.",
                    "spec_outdated_related": bool(state.get("spec_outdated")),
                    "preliminary": preliminary,
                }
            )
            index += 1
    return gaps


def _preliminary_uncertain_intent_classification(
    state: dict[str, Any],
    *,
    reason_prefix: str,
) -> list[dict[str, Any]]:
    knowledge_text = str(state.get("dev_knowledge_context") or "").lower()
    classifications: list[dict[str, Any]] = []
    for gap in state.get("gap_report") or []:
        if not isinstance(gap, dict):
            continue
        target = str(gap.get("spec_target") or "").lower()
        approved_knowledge_hint = (
            target
            and target in knowledge_text
            and "approved_intentional_change" in knowledge_text
        )
        reason = (
            f"{reason_prefix} Manual PM review is required before treating "
            f"{gap.get('spec_target')} as intentional or unintentional."
        )
        if approved_knowledge_hint:
            reason += " Existing PM decision notes an APPROVED_INTENTIONAL_CHANGE for the same target, but this run still requires PM review."
        classifications.append(
            {
                "gap_id": gap.get("gap_id"),
                "intent": "UNCERTAIN",
                "confidence": 0.5,
                "reason": reason,
                "recommended_action": "PM_REVIEW",
                "preliminary": True,
            }
        )
    return classifications
