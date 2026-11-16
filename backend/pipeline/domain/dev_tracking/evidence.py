from __future__ import annotations

from typing import Any

from .analysis_rules import _contract_name
from .utils import _as_dict


def implementation_evidence_from_state(state: dict[str, Any]) -> dict[str, Any]:
    evidence = _as_dict(state.get("implementation_evidence"))
    if evidence:
        return evidence
    return _as_dict(_as_dict(state.get("code_inventory")).get("implementation_evidence"))


def build_implementation_evidence(source_dir: str, pr_context: dict[str, Any], checkout: dict[str, Any], *, fallback_used: bool) -> dict[str, Any]:
    from pipeline.core.ast_scanner import extract_functions

    expected_sha = str(pr_context.get("head_sha") or "").strip()
    observed_sha = str(checkout.get("actual_sha") or "").strip()
    files_by_path: dict[str, list[dict[str, Any]]] = {}
    scan_error = ""
    try:
        functions = extract_functions(source_dir, max_functions=500)
    except Exception as exc:
        functions = []
        scan_error = type(exc).__name__
    for function in functions:
        if not isinstance(function, dict):
            continue
        file_path = str(function.get("file") or "").strip()
        name = str(function.get("func_name") or function.get("name") or "").strip()
        start_line = function.get("lineno") or function.get("start_line")
        if file_path and name and isinstance(start_line, int) and start_line > 0:
            files_by_path.setdefault(file_path, []).append({
                "name": name,
                "start_line": start_line,
                "end_line": function.get("end_lineno") or function.get("end_line") or start_line,
                "lang": str(function.get("lang") or ""),
            })
    files = [{"file": path, "symbols": symbols} for path, symbols in sorted(files_by_path.items())]
    sha_matches = bool(expected_sha and observed_sha and observed_sha.lower().startswith(expected_sha.lower()))
    return {
        "complete": bool(files and len(functions) < 500 and not fallback_used and checkout.get("head_sha_matched") is True and sha_matches),
        "head_sha": observed_sha,
        "files": files,
        "symbol_count": sum(len(item["symbols"]) for item in files),
        "truncated": len(functions) >= 500,
        "scan_error": scan_error,
    }


def _contract_matches_from_evidence(state: dict[str, Any]) -> tuple[list[tuple[str, str]], bool]:
    snapshot = _as_dict(state.get("published_spec_snapshot"))
    evidence = implementation_evidence_from_state(state)
    if evidence.get("complete") is not True:
        return [], False
    api_names: set[str] = set()
    component_names: set[str] = set()
    for item in evidence.get("files") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("file") or "").lower()
        names = {
            str(symbol.get("name") or "").lower()
            for symbol in item.get("symbols") or []
            if isinstance(symbol, dict) and symbol.get("name")
        }
        if any(token in path for token in ("/api/", "router", "route", "endpoint")):
            api_names.update(names)
        if path.endswith((".jsx", ".tsx")) or "/components/" in path:
            component_names.update(names)
    missing: list[tuple[str, str]] = []
    unverified = False
    for field, known_names in (("api_contracts", api_names), ("component_contracts", component_names)):
        for contract in snapshot.get(field) or []:
            if not isinstance(contract, dict):
                continue
            target = _contract_name(contract)
            if any(contract.get(key) for key in ("method", "url", "uri", "path", "route", "endpoint", "method_path")):
                unverified = True
            elif target and target not in known_names:
                missing.append((field, target))
    return missing, unverified


def evidence_gap_report(state: dict[str, Any]) -> list[dict[str, Any]]:
    missing, _ = _contract_matches_from_evidence(state)
    return [
        {
            "gap_id": f"GAP_{index:03d}",
            "severity": "HIGH" if field == "api_contracts" else "MED",
            "type": "MISSING_API" if field == "api_contracts" else "MISSING_COMPONENT",
            "spec_target": target,
            "implementation_target": None,
            "description": f"AST 근거에서 '{target}' 구현을 찾지 못했습니다.",
            "spec_outdated_related": bool(state.get("spec_outdated")),
            "preliminary": True,
        }
        for index, (field, target) in enumerate(missing, start=1)
    ]


def build_security_gate(state: dict[str, Any]) -> dict[str, Any]:
    pr_context = _as_dict(state.get("pr_context"))
    snapshot = _as_dict(state.get("published_spec_snapshot"))
    evidence = implementation_evidence_from_state(state)
    expected_sha = str(pr_context.get("head_sha") or "").strip()
    evidence_sha = str(evidence.get("head_sha") or "").strip()
    blockers: list[str] = []
    if not all(str(pr_context.get(field) or "").strip() for field in ("owner", "repo", "head_sha")):
        blockers.append("PR_CONTEXT_MISSING")
    groups = [snapshot.get(field) or [] for field in ("api_contracts", "component_contracts")]
    if (not all(isinstance(group, list) for group in groups)
            or not any(groups)
            or any(not isinstance(contract, dict) or not _contract_name(contract) for group in groups for contract in group)):
        blockers.append("SPEC_CONTRACTS_MISSING")
    if evidence.get("complete") is not True:
        blockers.append("EVIDENCE_INCOMPLETE")
    if expected_sha and not evidence_sha.lower().startswith(expected_sha.lower()):
        blockers.append("EVIDENCE_SHA_MISMATCH")
    if state.get("warnings") or state.get("llm_warnings"):
        blockers.append("ANALYSIS_WARNINGS_PRESENT")
    missing, unverified = _contract_matches_from_evidence(state)
    if unverified:
        blockers.append("ROUTE_EVIDENCE_UNVERIFIED")
    if state.get("gap_report") or missing:
        blockers.append("GAPS_PRESENT")
    return {"success_eligible": not blockers, "blockers": blockers}
