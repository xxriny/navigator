from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .analysis_rules import (
    _contract_name,
    _preliminary_uncertain_intent_classification,
    _rule_based_gap_report,
    _rule_based_implementation_profile,
)
from .context_compaction import (
    _prioritize_inventory_for_pr,
    _split_text_chunks,
)
from .llm import (
    _call_structured_for_forensic,
    _call_structured_for_gap,
    _call_structured_for_intent,
    _dev_tracking_llm_enabled,
    _extend_llm_warnings,
    _llm_meta,
    _validate_llm_implementation_profile,
    _validate_llm_intent_classification,
)
from .llm_nodes import (
    _llm_gap_report as _run_llm_gap_report,
    _llm_implementation_profile as _run_llm_implementation_profile,
    _llm_intent_classification as _run_llm_intent_classification,
)
from .notifications import (
    _desired_status_check_for_state,
    pr_comment_notifier as _pr_comment_notifier,
    pr_status_check_updater as _pr_status_check_updater,
    update_pr_status_check as _update_pr_status_check,
)
from .persistence import (
    analysis_persister,
    dev_knowledge_loader,
    develop_embedding,
    spec_loader,
)
from .repo_ops import _changed_files_from_git, _run_gh, _run_git
from .evidence import build_implementation_evidence, build_security_gate, evidence_gap_report, implementation_evidence_from_state
from .schemas import (
    DevGapIntentItem,
    DevGapIntentResponse,
    DevGapItem,
    DevGapReportResponse,
    DevImplementationProfile,
    DevImplementationProfileResponse,
)
from .task_flow import task_coordinator as _task_coordinator
from .utils import _as_dict, _now_iso


def _fallback_reverse_context(source_dir: str, reason: str) -> tuple[str, dict[str, Any]]:
    from pipeline.core.ast_scanner import extract_file_inventory

    files = extract_file_inventory(source_dir, max_files=120)
    context = (
        "Fallback reverse context was generated because the full reverse analyzer "
        f"could not be loaded: {reason}. "
        f"Detected {len(files)} source files."
    )
    return context, {"files": files, "fallback_reason": reason}


def _normalize_reverse_code_inventory(reverse_inventory: dict[str, Any]) -> dict[str, Any]:
    # author: xxrin
    # build_reverse_context()의 튜플 반환값을 Dev Tracking 공통 code_inventory shape로 맞춘다.
    # 별도 code_inventory_builder 노드 없이 reverse_analyzer 단일 단계에서 후속 노드 입력을 완성하기 위함이다.
    if not isinstance(reverse_inventory, dict):
        return {"files": [], "symbols_by_file": {}, "summary": {"file_count": 0, "symbol_count": 0}}
    if "files" in reverse_inventory and "symbols_by_file" in reverse_inventory:
        files = reverse_inventory.get("files") if isinstance(reverse_inventory.get("files"), list) else []
        symbols_by_file = (
            reverse_inventory.get("symbols_by_file")
            if isinstance(reverse_inventory.get("symbols_by_file"), dict)
            else {}
        )
        summary = reverse_inventory.get("summary") if isinstance(reverse_inventory.get("summary"), dict) else {}
        return {
            "files": files,
            "symbols_by_file": symbols_by_file,
            "summary": {
                "file_count": summary.get("file_count", len(files)),
                "symbol_count": summary.get("symbol_count", sum(len(items) for items in symbols_by_file.values())),
            },
        }

    files: list[dict[str, Any]] = []
    symbols_by_file: dict[str, list[dict[str, Any]]] = {}
    for file_path, symbols in reverse_inventory.items():
        if not isinstance(file_path, str) or not file_path:
            continue
        if not isinstance(symbols, list):
            continue
        files.append({"file": file_path})
        symbols_by_file[file_path] = [
            {
                "name": item.get("name", "") if isinstance(item, dict) else "",
                "type": item.get("type", "function") if isinstance(item, dict) else "function",
                "summary": item.get("summary", "") if isinstance(item, dict) else "",
                "docstring": item.get("docstring", "") if isinstance(item, dict) else "",
                "start_line": item.get("start_line", item.get("lineno", 0)) if isinstance(item, dict) else 0,
                "end_line": item.get("end_line", item.get("lineno", 0)) if isinstance(item, dict) else 0,
                "lang": item.get("lang", "") if isinstance(item, dict) else "",
            }
            for item in symbols
            if isinstance(item, dict)
        ]

    return {
        "files": files,
        "symbols_by_file": symbols_by_file,
        "summary": {
            "file_count": len(files),
            "symbol_count": sum(len(items) for items in symbols_by_file.values()),
        },
    }


def _validate_llm_gap_report(gaps: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    # author:xxrin
    # LLM GAP 결과가 비어 있거나 중복된 식별자를 내면 PM 판단이 흔들리므로 semantic validation을 수행한다.
    seen: set[str] = set()
    snapshot = _as_dict(state.get("published_spec_snapshot"))
    known_targets = {
        _contract_name(contract)
        for field in ("api_contracts", "component_contracts")
        for contract in (snapshot.get(field) if isinstance(snapshot.get(field), list) else [])
        if isinstance(contract, dict)
    }
    for index, gap in enumerate(gaps):
        if not isinstance(gap, dict):
            raise ValueError(f"LLM gap item {index} is not an object")
        gap_id = str(gap.get("gap_id") or "").strip()
        gap_type = str(gap.get("type") or "").strip()
        description = str(gap.get("description") or "").strip()
        severity = str(gap.get("severity") or "").strip()
        if not gap_id or not severity or not gap_type or not description:
            raise ValueError(f"LLM gap item {index} is missing required non-empty fields")
        if gap_id in seen:
            raise ValueError(f"LLM gap report returned duplicate gap_id: {gap_id}")
        if str(gap.get("spec_target") or "").strip().lower() not in known_targets:
            raise ValueError(f"LLM gap report references unknown spec_target: {gap.get('spec_target')}")
        seen.add(gap_id)

    evidence = implementation_evidence_from_state(state)
    expected_gaps = evidence_gap_report(state) if evidence.get("complete") else _rule_based_gap_report(state, preliminary=True)
    observed = {(str(gap.get("type") or ""), str(gap.get("spec_target") or "").lower()) for gap in gaps}
    expected = {(str(gap.get("type") or ""), str(gap.get("spec_target") or "").lower()) for gap in expected_gaps}
    if observed != expected:
        raise ValueError("LLM gap report does not match rule-based evidence")
    return expected_gaps if evidence.get("complete") else gaps


def dev_task_planner(state: dict[str, Any]) -> dict[str, Any]:
    # author: xxrin
    # PR 이벤트 입력을 공통 컨텍스트(pr_context)로 정규화합니다.
    # 이후 노드가 입력 포맷 차이 없이 동일 키를 안정적으로 참조하도록 하기 위함입니다.
    # GitHub PR/webhook payload를 이후 노드들이 공통으로 쓰는 pr_context로 정리한다.
    repository = _as_dict(state.get("repository"))
    pull_request = _as_dict(state.get("pull_request"))
    actor = _as_dict(state.get("actor"))

    owner = str(repository.get("owner") or "").strip()
    repo = str(repository.get("repo") or "").strip()
    branch_name = str(
        pull_request.get("branch_name")
        or pull_request.get("head_ref")
        or pull_request.get("ref")
        or ""
    ).strip()
    pr_number = pull_request.get("pr_number") or pull_request.get("number")
    if pr_number is None or pr_number == "":
        pr_number = 0
    head_sha = str(pull_request.get("head_sha") or pull_request.get("sha") or "").strip()

    missing = [
        name
        for name, value in {
            "repository.owner": owner,
            "repository.repo": repo,
            "pull_request.branch_name": branch_name,
            # "pull_request.pr_number": pr_number, # pr_number is now optional
            "pull_request.head_sha": head_sha,
        }.items()
        if value in ("", None)
    ]
    if missing:
        return {
            "status": "FAIL",
            "error_type": "INVALID_WEBHOOK_PAYLOAD",
            "errors": [f"Missing {item}" for item in missing],
            "dev_tracking_next_action": "blocked",
            "current_step": "dev_task_planner_failed",
        }

    pr_context = {
        "owner": owner,
        "repo": repo,
        "branch_name": branch_name,
        "base_branch": pull_request.get("base_branch") or pull_request.get("base_ref") or "",
        "pr_number": int(pr_number),
        "head_sha": head_sha,
        "created_at": pull_request.get("branch_created_at") or pull_request.get("created_at") or _now_iso(),
        "title": pull_request.get("title") or "",
        "description": pull_request.get("description") or pull_request.get("body") or "",
        "actor": actor,
        "changed_files": pull_request.get("changed_files") if isinstance(pull_request.get("changed_files"), list) else [],
    }
    return {
        "status": "PASS",
        "pr_context": pr_context,
        "dev_tracking_next_action": "branch_fetcher",
        "current_step": "dev_task_planner_done",
    }


def _worktree_path_for(base_cache_dir: str, owner: str, repo: str, pr_number: Any, sha: str) -> Path:
    """PR 분석 전용 worktree 경로를 반환한다."""
    safe_sha = (sha or "unknown")[:8]
    safe_pr = str(pr_number or "nopr")
    return Path(base_cache_dir) / "worktrees" / owner / repo / f"pr{safe_pr}-{safe_sha}"


def _remove_worktree(repo_path: Path, worktree_path: Path) -> None:
    """worktree를 정리한다. 실패해도 분석 전체에 영향 없음."""
    import shutil
    try:
        wt_str = str(worktree_path).replace("\\", "/")
        _run_git(["worktree", "remove", "--force", wt_str], str(repo_path))
        _run_git(["worktree", "prune"], str(repo_path))
    except Exception:
        pass
    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)


def branch_fetcher(state: dict[str, Any]) -> dict[str, Any]:
    # author:xxrin
    # PR 분석용 git worktree를 생성해 메인 클론/작업 디렉토리를 보존한다.
    # provided_source_dir가 있으면 해당 경로를 그대로 사용(worktree 불필요).
    from connectors.repo_cache import CACHE_DIR

    pr_context = _as_dict(state.get("pr_context"))
    provided_source_dir = str(state.get("source_dir") or "").strip()
    expected_sha = str(pr_context.get("head_sha") or "").strip()
    owner = str(pr_context.get("owner") or "")
    repo = str(pr_context.get("repo") or "")
    branch = str(pr_context.get("branch_name") or "")
    pr_number = pr_context.get("pr_number")

    checkout = {
        "branch_name": branch,
        "head_sha": expected_sha,
        "head_sha_matched": False,
        "worktree_used": False,
    }

    # ── 사용자가 직접 source_dir를 제공한 경우 ──────────────────
    if provided_source_dir:
        repo_path = Path(provided_source_dir)
        if not repo_path.is_dir():
            return {
                "status": "FAIL",
                "error_type": "LOCAL_REPO_PATH_MISSING",
                "errors": [f"source_dir does not exist: {provided_source_dir}"],
                "dev_tracking_next_action": "blocked",
                "current_step": "branch_fetcher_failed",
            }
        checkout["source_dir"] = str(repo_path)
        checkout["reset_skipped_reason"] = "provided_source_dir"
        # HEAD SHA 검증만 수행 (checkout/reset 없음)
        if (repo_path / ".git").exists():
            code, out, _ = _run_git(["rev-parse", "HEAD"], str(repo_path))
            actual_sha = out.strip() if code == 0 else ""
            checkout["actual_sha"] = actual_sha
            checkout["head_sha_matched"] = bool(
                not expected_sha or actual_sha.lower().startswith(expected_sha.lower())
            )
            if expected_sha and not checkout["head_sha_matched"]:
                return {
                    "status": "FAIL",
                    "error_type": "HEAD_SHA_MISMATCH",
                    "checkout": checkout,
                    "dev_tracking_next_action": "blocked",
                    "current_step": "branch_fetcher_failed",
                }
            changed_files = pr_context.get("changed_files") if isinstance(pr_context.get("changed_files"), list) else []
            if not changed_files:
                changed_files = _changed_files_from_git(repo_path, str(pr_context.get("base_branch") or ""))
            checkout["changed_files"] = changed_files
        else:
            checkout["head_sha_matched"] = False
            checkout["warning"] = "source_dir is not a git repository; verification skipped"
            checkout["changed_files"] = pr_context.get("changed_files") if isinstance(pr_context.get("changed_files"), list) else []

        return {
            "status": "PASS",
            "source_dir": str(repo_path),
            "worktree_path": None,
            "checkout": checkout,
            "changed_files": checkout.get("changed_files", []),
            "dev_tracking_next_action": "reverse_analyzer",
            "current_step": "branch_fetcher_done",
        }

    # ── source_dir 미제공: repo_cache 메인 클론 + worktree 생성 ──
    token = state.get("github_oauth_token") or state.get("github_token") or None
    from connectors.repo_cache import get_local_repo_path
    try:
        main_clone = Path(get_local_repo_path(owner, repo, token))
    except ValueError as e:
        return {
            "status": "FAIL",
            "error_type": "REPO_CLONE_FAILED",
            "errors": [str(e)],
            "dev_tracking_next_action": "blocked",
            "current_step": "branch_fetcher_failed",
        }

    if not (main_clone / ".git").is_dir():
        return {
            "status": "FAIL",
            "error_type": "LOCAL_REPO_PATH_MISSING",
            "errors": [f"cloned repo has no .git: {main_clone}"],
            "dev_tracking_next_action": "blocked",
            "current_step": "branch_fetcher_failed",
        }

    # fetch the branch into main clone (read-only side effect on main clone)
    if branch:
        _run_git(["fetch", "origin", branch], str(main_clone))
    elif expected_sha:
        # SHA만 있고 브랜치가 없는 경우 전체 fetch로 SHA를 확보
        _run_git(["fetch", "origin"], str(main_clone))

    # stale worktree 항목 정리 (디렉토리가 없어도 .git/worktrees 등록이 남아있을 수 있음)
    _run_git(["worktree", "prune"], str(main_clone))

    # worktree 경로 결정 및 기존 잔여 worktree 정리
    worktree_path = _worktree_path_for(CACHE_DIR, owner, repo, pr_number, expected_sha)
    if worktree_path.exists():
        _remove_worktree(main_clone, worktree_path)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    # worktree 생성: 지정 SHA 또는 브랜치로 (Windows 경로 호환: 슬래시 통일)
    wt_ref = expected_sha or (f"origin/{branch}" if branch else "HEAD")
    wt_path_str = str(worktree_path).replace("\\", "/")
    wt_code, wt_out, wt_err = _run_git(
        ["worktree", "add", "--detach", wt_path_str, wt_ref],
        str(main_clone),
    )
    if wt_code != 0:
        # SHA로 실패한 경우 브랜치 ref로 재시도
        if expected_sha and branch:
            wt_ref_fallback = f"origin/{branch}"
            wt_code, wt_out, wt_err = _run_git(
                ["worktree", "add", "--detach", wt_path_str, wt_ref_fallback],
                str(main_clone),
            )
    if wt_code != 0:
        return {
            "status": "FAIL",
            "error_type": "WORKTREE_CREATE_FAILED",
            "errors": [wt_err or wt_out],
            "checkout": checkout,
            "dev_tracking_next_action": "blocked",
            "current_step": "branch_fetcher_failed",
        }

    checkout["worktree_used"] = True
    checkout["source_dir"] = str(worktree_path)

    # HEAD SHA 검증
    code, out, err = _run_git(["rev-parse", "HEAD"], str(worktree_path))
    if code != 0:
        _remove_worktree(main_clone, worktree_path)
        return {
            "status": "FAIL",
            "error_type": "HEAD_SHA_READ_FAILED",
            "errors": [err or out],
            "checkout": checkout,
            "dev_tracking_next_action": "blocked",
            "current_step": "branch_fetcher_failed",
        }
    actual_sha = out.strip()
    checkout["actual_sha"] = actual_sha
    checkout["head_sha_matched"] = bool(
        not expected_sha or actual_sha.lower().startswith(expected_sha.lower())
    )
    if expected_sha and not checkout["head_sha_matched"]:
        _remove_worktree(main_clone, worktree_path)
        return {
            "status": "FAIL",
            "error_type": "HEAD_SHA_MISMATCH",
            "checkout": checkout,
            "dev_tracking_next_action": "blocked",
            "current_step": "branch_fetcher_failed",
        }

    changed_files = pr_context.get("changed_files") if isinstance(pr_context.get("changed_files"), list) else []
    if not changed_files:
        changed_files = _changed_files_from_git(worktree_path, str(pr_context.get("base_branch") or ""))
    checkout["changed_files"] = changed_files

    return {
        "status": "PASS",
        "source_dir": str(worktree_path),
        # 정리에 필요한 경로를 state에 저장
        "worktree_path": str(worktree_path),
        "worktree_main_clone": str(main_clone),
        "checkout": checkout,
        "changed_files": changed_files,
        "dev_tracking_next_action": "reverse_analyzer",
        "current_step": "branch_fetcher_done",
    }


def reverse_analyzer(state: dict[str, Any]) -> dict[str, Any]:
    # author:xxrin
    # build_reverse_context()의 project_context와 code_inventory를 단일 reverse 단계에서 함께 만든다.
    source_dir = str(state.get("source_dir") or "")
    fallback_warning = ""
    try:
        from orchestration.pipeline_runner import build_reverse_context

        reverse_result = build_reverse_context(source_dir)
    except ModuleNotFoundError as exc:
        fallback_warning = f"REVERSE_CONTEXT_FALLBACK:{exc.name}"
        reverse_result = _fallback_reverse_context(source_dir, str(exc))
    except Exception as exc:
        fallback_warning = f"REVERSE_CONTEXT_FALLBACK:{type(exc).__name__}"
        reverse_result = _fallback_reverse_context(source_dir, str(exc) or type(exc).__name__)
    if isinstance(reverse_result, tuple):
        project_context = reverse_result[0] or ""
        reverse_inventory = reverse_result[1] or {}
    else:
        project_context = reverse_result or ""
        reverse_inventory = {}
    code_inventory = _normalize_reverse_code_inventory(reverse_inventory)
    status = "PASS" if project_context else "WARN"
    warnings = [] if project_context else ["REVERSE_CONTEXT_EMPTY"]
    if fallback_warning:
        warnings.append(fallback_warning)
    implementation_evidence = build_implementation_evidence(
        source_dir,
        _as_dict(state.get("pr_context")),
        _as_dict(state.get("checkout")),
        fallback_used=bool(fallback_warning),
    )
    if implementation_evidence["complete"]:
        code_inventory["files"] = [{"file": item["file"]} for item in implementation_evidence["files"]]
        code_inventory["symbols_by_file"] = {
            item["file"]: item["symbols"] for item in implementation_evidence["files"]
        }
        code_inventory["summary"] = {
            "file_count": len(implementation_evidence["files"]),
            "symbol_count": implementation_evidence["symbol_count"],
        }
    code_inventory["implementation_evidence"] = implementation_evidence
    result = {
        "status": status,
        "project_context": project_context,
        "code_inventory": code_inventory,
        "warnings": warnings,
        "dev_tracking_next_action": "forensic_profiler",
        "current_step": "reverse_analyzer_done",
    }
    if reverse_inventory:
        result["reverse_code_inventory"] = reverse_inventory
    return result


def code_inventory_builder(state: dict[str, Any]) -> dict[str, Any]:
    # author:xxrin
    # 코드 인벤토리만 메모리에 만든다. 이 단계에서는 의도적으로 RAG에 저장하지 않는다.
    from pipeline.core.ast_scanner import extract_file_inventory, extract_functions

    source_dir = str(state.get("source_dir") or "")
    if not Path(source_dir).is_dir():
        return {
            "status": "FAIL",
            "error_type": "CODE_INVENTORY_FAILED",
            "errors": [f"source_dir does not exist: {source_dir}"],
            "dev_tracking_next_action": "blocked",
            "current_step": "code_inventory_builder_failed",
        }

    functions = extract_functions(source_dir, max_functions=500)
    files = extract_file_inventory(source_dir, max_files=600)
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in functions:
        file_path = str(item.get("file") or "")
        if not file_path:
            continue
        by_file[file_path].append(
            {
                "name": item.get("func_name") or item.get("name") or "",
                "type": "function",
                "docstring": item.get("docstring") or "",
                "start_line": item.get("lineno") or 0,
                "end_line": item.get("end_lineno") or item.get("lineno") or 0,
                "lang": item.get("lang") or "",
            }
        )

    code_inventory = {
        "files": files,
        "symbols_by_file": dict(by_file),
        "summary": {
            "file_count": len(files),
            "symbol_count": sum(len(items) for items in by_file.values()),
        },
    }
    return {
        "status": "PASS",
        "code_inventory": code_inventory,
        "dev_tracking_next_action": "forensic_profiler",
        "current_step": "code_inventory_builder_done",
    }


def _llm_implementation_profile(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return _run_llm_implementation_profile(
        state,
        call_structured_for_forensic=_call_structured_for_forensic,
        validate_llm_implementation_profile=_validate_llm_implementation_profile,
    )


def forensic_profiler(state: dict[str, Any]) -> dict[str, Any]:
    # author:xxrin
    # LLM structured output이 primary이고, rule-based는 PM 검토용 preliminary fallback이다.
    meta: dict[str, Any]
    llm_warnings = state.get("llm_warnings") or []
    if _dev_tracking_llm_enabled(state, "use_llm_forensic_profiler"):
        try:
            implementation_profile, meta = _llm_implementation_profile(state)
        except Exception as exc:
            implementation_profile = _rule_based_implementation_profile(state)
            meta = _llm_meta(
                "rule_based_fallback",
                attempted=True,
                fallback_used=True,
                preliminary=True,
                error=exc,
            )
            llm_warnings = _extend_llm_warnings(
                state,
                node="forensic_profiler",
                message="LLM forensic profiling failed; rule-based code role draft requires PM review.",
                error=exc,
            )
    else:
        implementation_profile = _rule_based_implementation_profile(state)
        meta = _llm_meta(
            "rule_based",
            attempted=False,
            fallback_used=False,
            preliminary=True,
        )
    return {
        "status": "PASS",
        "implementation_profile": implementation_profile,
        "forensic_profiler_meta": meta,
        "llm_warnings": llm_warnings,
        "dev_tracking_next_action": "spec_loader",
        "current_step": "forensic_profiler_done",
    }


def _llm_gap_report(state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _run_llm_gap_report(
        state,
        call_structured_for_gap=_call_structured_for_gap,
        validate_llm_gap_report=_validate_llm_gap_report,
    )


def gap_analyzer(state: dict[str, Any]) -> dict[str, Any]:
    # author:xxrin
    # LLM structured output이 primary이고, rule-based 비교는 PM 검토용 preliminary fallback이다.
    snapshot = _as_dict(state.get("published_spec_snapshot"))
    contract_groups = [snapshot.get(field) or [] for field in ("api_contracts", "component_contracts")]
    contracts = [contract for group in contract_groups if isinstance(group, list) for contract in group]
    if (not contracts or any(not isinstance(group, list) for group in contract_groups)
            or any(not isinstance(contract, dict) or not _contract_name(contract) for contract in contracts)):
        return {
            "status": "FAIL",
            "error_type": "SPEC_SNAPSHOT_UNVERIFIED",
            "errors": ["Published specification has no verifiable contracts."],
            "dev_tracking_next_action": "blocked",
            "current_step": "gap_analyzer_failed",
        }
    llm_warnings = state.get("llm_warnings") or []
    if _dev_tracking_llm_enabled(state, "use_llm_gap_analyzer"):
        try:
            gaps, analyzer_meta = _llm_gap_report(state)
        except Exception as exc:
            gaps = evidence_gap_report(state) if implementation_evidence_from_state(state).get("complete") else _rule_based_gap_report(state, preliminary=True)
            analyzer_meta = _llm_meta(
                "rule_based_fallback",
                attempted=True,
                fallback_used=True,
                preliminary=True,
                error=exc,
            )
            llm_warnings = _extend_llm_warnings(
                state,
                node="gap_analyzer",
                message="LLM GAP analysis failed; rule-based GAP draft requires PM review.",
                error=exc,
            )
    else:
        gaps = evidence_gap_report(state) if implementation_evidence_from_state(state).get("complete") else _rule_based_gap_report(state, preliminary=True)
        analyzer_meta = _llm_meta(
            "rule_based",
            attempted=False,
            fallback_used=False,
            preliminary=True,
        )

    has_high_gap = any(item.get("severity") == "HIGH" for item in gaps)
    return {
        "status": "PASS",
        "gap_report": gaps,
        "gap_analyzer_meta": analyzer_meta,
        "llm_warnings": llm_warnings,
        "has_high_gap": has_high_gap,
        "dev_tracking_next_action": "intent_classifier" if has_high_gap else "milestone_tracker",
        "current_step": "gap_analyzer_done",
    }


def _llm_intent_classification(state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _run_llm_intent_classification(
        state,
        call_structured_for_intent=_call_structured_for_intent,
        validate_llm_intent_classification=_validate_llm_intent_classification,
    )


def intent_classifier(state: dict[str, Any]) -> dict[str, Any]:
    classifier_meta: dict[str, Any]
    llm_warnings = state.get("llm_warnings") or []
    use_llm = _dev_tracking_llm_enabled(state, "use_llm_intent_classifier")
    if use_llm and state.get("gap_report"):
        try:
            classifications, classifier_meta = _llm_intent_classification(state)
        except Exception as exc:
            classifications = _preliminary_uncertain_intent_classification(
                state,
                reason_prefix="LLM intent classification failed.",
            )
            classifier_meta = _llm_meta(
                "rule_based_fallback",
                attempted=True,
                fallback_used=True,
                preliminary=True,
                error=exc,
            )
            llm_warnings = _extend_llm_warnings(
                state,
                node="intent_classifier",
                message="LLM intent classification failed; all GAP intents require PM review.",
                error=exc,
            )
    else:
        classifications = _preliminary_uncertain_intent_classification(
            state,
            reason_prefix="LLM intent classification was not attempted because api_key is missing.",
        )
        classifier_meta = _llm_meta(
            "rule_based",
            attempted=False,
            fallback_used=False,
            preliminary=True,
        )
    return {
        "status": "PASS",
        "intent_classification": classifications,
        "intent_classifier_meta": classifier_meta,
        "llm_warnings": llm_warnings,
        "requires_pm_approval": bool(classifications),
        "dev_tracking_next_action": "milestone_tracker",
        "current_step": "intent_classifier_done",
    }


def milestone_tracker(state: dict[str, Any]) -> dict[str, Any]:
    # author:xxrin
    # GAP/의도 분류 결과를 PM이 읽을 수 있는 마일스톤 진행률로 바꾼다.
    gaps = state.get("gap_report") or []
    classifications = state.get("intent_classification") or []
    blocked = sum(
        1
        for item in classifications
        if item.get("recommended_action") in {"REQUEST_FIX", "PM_REVIEW"}
    )
    total = max(1, len(gaps))
    completed = max(0, total - blocked)
    completion_rate = int((completed / total) * 100)
    snapshot_status = _as_dict(_as_dict(state.get("published_spec_snapshot")).get("milestone_status"))
    milestone_status = {
        "milestone_id": snapshot_status.get("milestone_id") or "PR_ANALYSIS",
        "completion_rate": completion_rate,
        "completed_features": completed,
        "total_features": total,
        "blocked_features": blocked,
        "estimated_completion_date": snapshot_status.get("estimated_completion_date") or "",
    }
    return {
        "status": "PASS",
        "milestone_status": milestone_status,
        "dev_tracking_next_action": "pm_report_generator",
        "current_step": "milestone_tracker_done",
    }


def pm_report_generator(state: dict[str, Any]) -> dict[str, Any]:
    # author:xxrin
    # TaskApprovalPanel에서 볼 수 있도록 agile_tasks payload에 들어갈 PM Report를 만든다.
    pr_context = _as_dict(state.get("pr_context"))
    gaps = state.get("gap_report") or []
    classifications = state.get("intent_classification") or []
    llm_warnings = [
        item
        for item in state.get("llm_warnings") or []
        if isinstance(item, dict)
    ]
    high_count = sum(1 for gap in gaps if gap.get("severity") == "HIGH")
    med_count = sum(1 for gap in gaps if gap.get("severity") == "MED")
    low_count = sum(1 for gap in gaps if gap.get("severity") == "LOW")
    recommended = sorted(
        {
            item.get("recommended_action")
            for item in classifications
            if item.get("recommended_action")
        }
    )
    security_gate = build_security_gate(state)
    verified_no_gap = security_gate["success_eligible"]
    if not recommended:
        recommended = ["APPROVE_AS_INTENTIONAL"] if verified_no_gap else ["PM_REVIEW"]
    if llm_warnings and "PM_REVIEW" not in recommended:
        recommended.append("PM_REVIEW")
        recommended = sorted(recommended)
    spec_warning = ""
    if state.get("spec_outdated"):
        selected = _as_dict(state.get("published_spec_snapshot"))
        latest = _as_dict(state.get("latest_snapshot"))
        # author: xxrin
        # PM 화면에 노출되는 스펙 버전 차이 안내 문구를 한글로 명확히 남긴다.
        spec_warning = (
            f"개발자는 snapshot {selected.get('version', '?')} 기준으로 작업했으며 "
            f"현재 snapshot {latest.get('version', '?')} 설계가 존재합니다."
        )
    pm_report = {
        "summary": (
            f"PR #{pr_context.get('pr_number')}에서 GAP {len(gaps)}건이 발견되었습니다. "
            f"(HIGH {high_count}, MED {med_count}, LOW {low_count})."
        ),
        "pr_summary": {
            "owner": pr_context.get("owner"),
            "repo": pr_context.get("repo"),
            "pr_number": pr_context.get("pr_number"),
            "branch_name": pr_context.get("branch_name"),
            "title": pr_context.get("title"),
        },
        "implementation_summary": _as_dict(state.get("implementation_profile")).get("implementation_summary", ""),
        "gap_summary": gaps,
        "intent_summary": classifications,
        "milestone_summary": state.get("milestone_status") or {},
        "spec_outdated_warning": spec_warning,
        "llm_warnings": llm_warnings,
        "recommended_pm_actions": recommended,
        "security_gate": security_gate,
    }
    approval_status = "NO_GAP_DETECTED" if verified_no_gap else "PENDING_PM_APPROVAL"
    return {
        "status": "PASS",
        "pm_report": pm_report,
        "approval_status": approval_status,
        "security_gate": security_gate,
        "dev_tracking_next_action": "pr_comment_notifier",
        "current_step": "pm_report_generator_done",
    }


def pr_comment_notifier(state: dict[str, Any]) -> dict[str, Any]:
    return _pr_comment_notifier(state, run_gh=_run_gh)


def update_pr_status_check(
    state: dict[str, Any],
    status_state: str,
    description: str,
) -> dict[str, Any]:
    return _update_pr_status_check(
        state,
        status_state,
        description,
        run_gh=_run_gh,
    )


def pr_status_check_updater(state: dict[str, Any]) -> dict[str, Any]:
    return _pr_status_check_updater(
        state,
        update_status_check=update_pr_status_check,
    )


def run_dev_gap_decision_followup(
    task: dict[str, Any],
    decision_status: str,
    reviewed_by: str = "",
    result_payload: dict[str, Any] | None = None,
    shared_db: Any = None,
) -> dict[str, Any]:
    # REST 계층의 기존 import 경로를 유지하기 위한 얇은 wrapper다.
    from .followup import run_dev_gap_decision_followup as run_followup

    return run_followup(
        task,
        decision_status,
        reviewed_by,
        result_payload,
        shared_db,
        run_gh=_run_gh,
    )


def task_coordinator(state: dict[str, Any]) -> dict[str, Any]:
    from pipeline.domain.agile.task_coordinator import create_task

    return _task_coordinator(state, create_task=create_task)


def develop_loop_controller(state: dict[str, Any]) -> dict[str, Any]:
    # author: xxrin
    # 승인 상태와 대기 PR 큐를 기준으로 다음 액션을 결정합니다.
    # PM 승인 대기 시 중단하고, 추가 PR이 있으면 연속 처리하도록 흐름을 제어하기 위함입니다.
    # 구버전 feature 루프가 아니라 PR 묶음 처리 기준으로 loop 결정을 내림
    if state.get("approval_status") == "PENDING_PM_APPROVAL":
        decision = "BLOCKED"
        next_action = "pm_approval_pending"
    else:
        decision = "COMPLETE"
        next_action = "complete"
    return {
        "status": "PASS",
        "loop_decision": decision,
        "dev_tracking_next_action": next_action,
        "dev_tracking_loop_count": int(state.get("dev_tracking_loop_count", 0) or 0) + 1,
        "current_step": "develop_loop_controller_done",
    }
