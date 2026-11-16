import React, { useState, useEffect, useCallback } from "react";
import { serverRequest } from "../../api/serverClient";
import useAppStore from '../../store/useAppStore';
import { apiBaseUrl } from '../../api/apiClient';
import {
  ClipboardList, Check, X, Clock, Loader2, RefreshCw,
  ChevronDown, ChevronRight, AlertTriangle, CheckCircle,
  XCircle, Plus, Trash2, Sparkles, Users, GitPullRequest,
  CircleDot, Inbox, Pencil, Save, UserCheck, RotateCcw,
} from "lucide-react";

const STATUS_CONFIG = {
  unassigned:       { label: "미할당",     color: "text-slate-400",   bg: "bg-slate-500/10",    border: "border-slate-500/20",   Icon: Inbox },
  pending_approval: { label: "승인대기중", color: "text-yellow-400",  bg: "bg-yellow-500/10",   border: "border-yellow-500/30",  Icon: Clock },
  in_progress:      { label: "진행중",     color: "text-blue-400",    bg: "bg-blue-500/10",     border: "border-blue-500/30",    Icon: CircleDot },
  pr_pending:       { label: "PR대기중",   color: "text-violet-400",  bg: "bg-violet-500/10",   border: "border-violet-500/30",  Icon: GitPullRequest },
  completed:        { label: "완료",       color: "text-emerald-400", bg: "bg-emerald-500/10",  border: "border-emerald-500/30", Icon: CheckCircle },
  rejected:         { label: "거절",       color: "text-red-400",     bg: "bg-red-500/10",      border: "border-red-500/30",     Icon: XCircle },
};

const FILTER_TABS = ["all", "unassigned", "pending_approval", "in_progress", "pr_pending", "completed", "rejected"];
const TYPE_FILTERS = [
  { id: "all", label: "전체 태스크" },
  { id: "regular", label: "일반 태스크" },
  { id: "dev_gap", label: "Dev GAP 승인" },
  { id: "sa_review", label: "SA 재검토" },
];

const TASK_TYPE_LABEL = {
  feature:       "기능 개발",
  bugfix:        "버그 수정",
  refactor:      "리팩토링",
  test:          "테스트",
  infra:         "인프라/DevOps",
  doc_sync:      "문서 동기화",
  publish_docs:  "설계 문서 퍼블리시",
  verify_sa:     "SA 검증",
  sa_re_review:  "SA GAP 재검토",
};

const EFFORT_LABEL = { S: "S (2h↓)", M: "M (2~8h)", L: "L (1~3d)", XL: "XL (3d↑)" };

const AREA_LABEL = {
  backend:   "백엔드",
  frontend:  "프론트엔드",
  fullstack: "풀스택",
  devops:    "DevOps",
  sa:        "SA",
};

const TASK_TYPES = ["feature", "bugfix", "refactor", "test", "infra", "doc_sync"];
const AREAS = ["backend", "frontend", "fullstack", "devops"];

// 역할별 볼 수 있는 area 필터
const ROLE_AREAS = {
  backend:  ["backend", "fullstack"],
  frontend: ["frontend", "fullstack"],
  devops:   ["devops"],
  engineer: null, // fullstack — 모두 볼 수 있음
  pm:       null, // 모두 볼 수 있음
};

const backendFetch = (port, authToken, path, options = {}) => fetch(`${apiBaseUrl(port)}${path}`, {
  ...options,
  headers: { Authorization: `Bearer ${authToken}`, ...(options.headers || {}) },
});

export default function TaskApprovalPanel() {
  const isDarkMode  = useAppStore((s) => s.isDarkMode);
  const userRole    = useAppStore((s) => s.userRole);
  const currentUser = useAppStore((s) => s.currentUser);
  const backendPort = useAppStore((s) => s.backendPort);
  const authToken   = useAppStore((s) => s.authToken);
  const resultData  = useAppStore((s) => s.resultData);
  const apiKey      = useAppStore((s) => s.apiKey) || "";

  const port    = backendPort || 8000;
  const isPM    = userRole === "pm";
  const userId  = currentUser?.id || "";
  const teamId  = currentUser?.team_id || "";
  const runId   = resultData?.run_id || "";

  const [tasks, setTasks]               = useState([]);
  const [loading, setLoading]           = useState(false);
  const [error, setError]               = useState("");
  const [expandedIds, setExpandedIds]   = useState(() => new Set());
  const [filterStatus, setFilterStatus] = useState("all");
  const [taskTypeFilter, setTaskTypeFilter] = useState("all");
  const [decisionReasons, setDecisionReasons] = useState({});
  const [teamMembers, setTeamMembers]   = useState([]);

  // 선택 삭제
  const [selectedIds, setSelectedIds]       = useState(() => new Set());
  const [deleteLoading, setDeleteLoading]   = useState(false);

  // 커스텀 태스크 생성 폼
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [createForm, setCreateForm] = useState({
    task_type: "feature", title: "", description: "", area: "backend", assignee: "",
  });
  const [createLoading, setCreateLoading] = useState(false);
  const [createMsg, setCreateMsg]         = useState("");

  // AI 태스크 생성
  const [genLoading, setGenLoading] = useState(false);
  const [genMsg, setGenMsg]         = useState("");
  const [proposals, setProposals]   = useState([]);
  const [missingZones, setMissingZones] = useState([]);

  // 배분
  const [distLoading, setDistLoading] = useState(false);
  const [distMsg, setDistMsg]         = useState("");

  // 인라인 편집
  const [editingId, setEditingId]     = useState(null);
  const [editForm, setEditForm]       = useState({});
  const [editLoading, setEditLoading] = useState(false);

  // 거절 사유 입력
  const [rejectingId, setRejectingId]   = useState(null);
  const [rejectReason, setRejectReason] = useState("");

  // PM 재배분 (rejected → pending_approval + 새 담당자)
  const [reassignId, setReassignId]       = useState(null);
  const [reassignTarget, setReassignTarget] = useState("");

  const fetchTeamMembers = useCallback(async () => {
    if (!authToken || !currentUser?.team_id) return;
    try {
      const data = await serverRequest(`/auth/teams/${currentUser.team_id}/members`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      setTeamMembers(data.members || []);
    } catch (_) {}
  }, [authToken, currentUser?.team_id]);

  useEffect(() => { fetchTeamMembers(); }, [fetchTeamMembers]);

  const fetchTasks = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const params = new URLSearchParams();
      if (teamId) params.set("team_id", teamId);
      const query = params.toString() ? `?${params}` : "";
      const res  = await backendFetch(port, authToken, `/api/tasks${query}`);
      const json = await res.json();
      if (json.status === "ok") setTasks(json.data);
      else setError(json.error || "조회 실패");
    } catch (e) { setError("서버 연결 실패: " + e.message); }
    finally { setLoading(false); }
  }, [port, teamId, authToken]);

  useEffect(() => {
    fetchTasks();
    const id = setInterval(() => {
      if (!document.hidden) fetchTasks();
    }, 15_000);
    const onVisibility = () => {
      if (!document.hidden) fetchTasks();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [fetchTasks]);

  // 상태 전환
  const handleAction = async (taskId, newStatus) => {
    try {
      const res = await backendFetch(port, authToken, `/api/tasks/${taskId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus, reviewed_by: userId }),
      });
      const json = await res.json();
      if (json.status === "ok") setTasks((prev) => prev.map((t) => t.id === taskId ? json.data : t));
      else setError(json.error || "업데이트 실패");
    } catch (e) { setError("서버 연결 실패: " + e.message); }
  };


  const handleDevGapDecision = async (taskId, action, reason) => {
    try {
      const headers = { "Content-Type": "application/json" };
      if (authToken) headers.Authorization = `Bearer ${authToken}`;
      const endpoint = action === "approve" ? "approve" : "reject";
      const res = await backendFetch(port, authToken, `/api/dev-tracking/tasks/${taskId}/${endpoint}`, {
        method: "POST",
        headers,
        body: JSON.stringify({ reason }),
      });
      const json = await res.json();
      if (json.status === "ok") setTasks((prev) => prev.map((t) => t.id === taskId ? json.data : t));
      else setError(json.error || "Dev GAP 결정 처리 실패");
    } catch (e) { setError("서버 연결 실패: " + e.message); }
  };
  const startEdit = (task) => {
    setEditingId(task.id);
    setEditForm({
      title: task.title || "",
      description: task.description || "",
      area: task.area || "backend",
      effort: task.effort || "M",
      task_type: task.task_type || "feature",
    });
  };

  // afterSave: { status, assignee?, clearResult? }
  const handleSaveEdit = async (taskId, afterSave = {}) => {
    setEditLoading(true);
    const currentTask = tasks.find((t) => t.id === taskId);
    const targetStatus = afterSave.status ?? (currentTask?.status === "rejected" ? "unassigned" : (currentTask?.status || "unassigned"));
    const patch = { status: targetStatus, result: afterSave.clearResult ? "" : undefined, ...editForm };
    if ("assignee" in afterSave) patch.assignee = afterSave.assignee;
    else if (currentTask?.status === "rejected") patch.assignee = "";
    try {
      const res = await backendFetch(port, authToken, `/api/tasks/${taskId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      const json = await res.json();
      if (json.status === "ok") {
        setTasks((prev) => prev.map((t) => t.id === taskId ? json.data : t));
        setEditingId(null);
      } else {
        setError(json.error || "수정 실패");
      }
    } catch (e) { setError("서버 연결 실패: " + e.message); }
    finally { setEditLoading(false); }
  };

  // 거절 (사유 포함)
  const handleRejectWithReason = async (taskId) => {
    try {
      const res = await backendFetch(port, authToken, `/api/tasks/${taskId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "rejected", result: rejectReason, reviewed_by: userId }),
      });
      const json = await res.json();
      if (json.status === "ok") {
        setTasks((prev) => prev.map((t) => t.id === taskId ? json.data : t));
        setRejectingId(null);
        setRejectReason("");
      } else { setError(json.error || "거절 실패"); }
    } catch (e) { setError("서버 연결 실패: " + e.message); }
  };

  // PM: rejected → unassigned (미할당으로 되돌리기)
  const handleReturnToUnassigned = async (taskId) => {
    try {
      const res = await backendFetch(port, authToken, `/api/tasks/${taskId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "unassigned", assignee: "" }),
      });
      const json = await res.json();
      if (json.status === "ok") setTasks((prev) => prev.map((t) => t.id === taskId ? json.data : t));
      else setError(json.error || "실패");
    } catch (e) { setError("서버 연결 실패: " + e.message); }
  };

  // PM: rejected → pending_approval + 새 담당자 배정
  const handleReassign = async (taskId) => {
    if (!reassignTarget) return;
    try {
      const res = await backendFetch(port, authToken, `/api/tasks/${taskId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "pending_approval", assignee: reassignTarget, result: "" }),
      });
      const json = await res.json();
      if (json.status === "ok") {
        setTasks((prev) => prev.map((t) => t.id === taskId ? json.data : t));
        setReassignId(null);
        setReassignTarget("");
      } else { setError(json.error || "재배분 실패"); }
    } catch (e) { setError("서버 연결 실패: " + e.message); }
  };

  // AI 태스크 자동 생성
  const handleGenerateTasks = async () => {
    if (!runId) { setGenMsg("분석 결과(run_id)가 없습니다. 파이프라인을 먼저 실행하세요."); return; }
    if (!teamId) { setGenMsg("팀 정보가 없습니다."); return; }
    setGenLoading(true); setGenMsg("");
    try {
      const res = await backendFetch(port, authToken, `/api/agile/generate-tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: runId, team_id: teamId, auth_token: authToken, api_key: apiKey, created_by: userId }),
      });
      const json = await res.json();
      if (json.status === "ok") {
        const d = json.data;
        setProposals([
          ...(d.task_proposals || []).map((item) => ({ kind: "create", item })),
          ...(d.update_proposals || []).map((item) => ({ kind: "update", item })),
        ]);
        setMissingZones(d.missing_risk_zones || []);
        setGenMsg(`검토할 제안 ${(d.task_proposals || []).length + (d.update_proposals || []).length}개`);
        fetchTasks();
      } else {
        setGenMsg("실패: " + (json.error || "unknown"));
      }
    } catch (e) { setGenMsg("연결 실패: " + e.message); }
    finally { setGenLoading(false); }
  };

  const handleApproveProposal = async (token) => {
    try {
      const res = await backendFetch(port, authToken, `/api/agile/task-proposals/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      const json = await res.json();
      if (!res.ok || json.status !== "ok") {
        setGenMsg(`실패: ${json.detail || json.error || "승인 실패"}`);
        return;
      }
      setProposals((items) => items.filter(({ item }) => item.approval_token !== token));
      setGenMsg("제안이 저장되었습니다.");
      fetchTasks();
    } catch (e) { setGenMsg(`연결 실패: ${e.message}`); }
  };

  // 팀 배분
  const handleDistribute = async () => {
    if (!teamId) { setDistMsg("팀 정보가 없습니다."); return; }
    setDistLoading(true); setDistMsg("");
    try {
      const res = await backendFetch(port, authToken, `/api/agile/distribute-tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          team_id: teamId,
          auth_token: authToken,
          api_key: apiKey,
          distributed_by: userId,
          members: teamMembers.map((m) => ({ id: m.id, name: m.name || m.email, role: m.role })),
        }),
      });
      const json = await res.json();
      if (json.status === "ok") {
        const d = json.data;
        setDistMsg(d.assigned > 0
          ? `배분 완료: ${d.assigned}개 태스크 배분됨`
          : (d.message || "배분할 태스크 없음"));
        fetchTasks();
      } else {
        setDistMsg("실패: " + (json.error || "unknown"));
      }
    } catch (e) { setDistMsg("연결 실패: " + e.message); }
    finally { setDistLoading(false); }
  };

  // 커스텀 태스크 생성
  const handleCreateCustomTask = async () => {
    if (!createForm.title.trim()) { setCreateMsg("제목을 입력하세요."); return; }
    setCreateLoading(true); setCreateMsg("");
    try {
      const res = await backendFetch(port, authToken, `/api/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...createForm, created_by: userId, team_id: teamId }),
      });
      const json = await res.json();
      if (json.status === "ok") {
        setCreateMsg("태스크가 생성되었습니다.");
        setCreateForm({ task_type: "feature", title: "", description: "", area: "backend", assignee: "" });
        setShowCreateForm(false);
        fetchTasks();
      } else {
        setCreateMsg("실패: " + (json.error || "unknown"));
      }
    } catch (e) { setCreateMsg("연결 실패: " + e.message); }
    finally { setCreateLoading(false); }
  };

  const handleDelete = async (taskId) => {
    try {
      const res  = await backendFetch(port, authToken, `/api/tasks/${taskId}`, { method: "DELETE" });
      const json = await res.json();
      if (json.status === "ok") setTasks((prev) => prev.filter((t) => t.id !== taskId));
      else setError(json.error || "삭제 실패");
    } catch (e) { setError("서버 연결 실패: " + e.message); }
  };

  const toggleExpand = (id) =>
    setExpandedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const toggleSelect = (id) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const handleDeleteSelected = async () => {
    if (!selectedIds.size) return;
    setDeleteLoading(true);
    try {
      const responses = await Promise.all([...selectedIds].map((id) =>
        backendFetch(port, authToken, `/api/tasks/${id}`, { method: "DELETE" })
      ));
      const results = await Promise.all(responses.map((res) => res.json()));
      if (responses.some((res, index) => !res.ok || results[index].status !== "ok")) {
        setError("일부 태스크를 삭제하지 못했습니다.");
      }
      await fetchTasks();
      setSelectedIds(new Set());
    } catch (e) { setError("삭제 실패: " + e.message); }
    finally { setDeleteLoading(false); }
  };

  const roleAreaFilter = ROLE_AREAS[userRole] ?? null;
  const visibleTasks = tasks
    .filter((t) => filterStatus === "all" || t.status === filterStatus)
    .filter((t) => {
      if (taskTypeFilter === "dev_gap") return t.task_type === "dev_gap_approval";
      if (taskTypeFilter === "sa_review") return t.task_type === "sa_re_review";
      if (taskTypeFilter === "regular") return !["dev_gap_approval", "sa_re_review"].includes(t.task_type);
      return true;
    })
    .filter((t) => {
      // 본인에게 할당된 태스크는 영역 필터와 무관하게 항상 표시
      if (t.assignee && t.assignee === currentUser?.name) return true;
      if (!roleAreaFilter) return true;
      if (!t.area) return false;
      return roleAreaFilter.includes(t.area);
    });

  const countOf = (s) => tasks.filter((t) => {
    if (t.status !== s) return false;
    if (t.assignee && t.assignee === currentUser?.name) return true;
    if (!roleAreaFilter) return true;
    if (!t.area) return false;
    return roleAreaFilter.includes(t.area);
  }).length;


  const countType = (type) => {
    if (type === "dev_gap") return tasks.filter((t) => t.task_type === "dev_gap_approval").length;
    if (type === "sa_review") return tasks.filter((t) => t.task_type === "sa_re_review").length;
    if (type === "regular") return tasks.filter((t) => !["dev_gap_approval", "sa_re_review"].includes(t.task_type)).length;
    return tasks.length;
  };

  const getDecisionReason = (taskId, fallback) => {
    const value = decisionReasons[taskId];
    return value && value.trim() ? value.trim() : fallback;
  };

  const parseTaskResult = (result) => {
    if (!result) return {};
    if (typeof result === "object") return result;
    try {
      const parsed = JSON.parse(result);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_) {
      return { raw_result: result };
    }
  };
  // 미할당 탭일 때 도메인별 그룹핑
  const AREA_ORDER = ["backend", "frontend", "devops", "fullstack", ""];
  const STATUS_ORDER = ["unassigned", "pending_approval", "in_progress", "pr_pending", "completed", "rejected"];
  const unassignedGroups = React.useMemo(() => {
    if (filterStatus !== "unassigned") return null;
    const groups = {};
    for (const t of visibleTasks) {
      const key = t.area || "";
      if (!groups[key]) groups[key] = [];
      groups[key].push(t);
    }
    return AREA_ORDER.filter((a) => groups[a]?.length).map((a) => ({ area: a, tasks: groups[a] }));
  }, [filterStatus, visibleTasks]);

  // 전체 탭일 때 상태별 그룹핑
  const allStatusGroups = React.useMemo(() => {
    if (filterStatus !== "all") return null;
    const groups = {};
    for (const t of visibleTasks) {
      const key = t.status || "unassigned";
      if (!groups[key]) groups[key] = [];
      groups[key].push(t);
    }
    return STATUS_ORDER.filter((s) => groups[s]?.length).map((s) => ({ status: s, tasks: groups[s] }));
  }, [filterStatus, visibleTasks]);

  const base = isDarkMode
    ? "bg-white/5 border-white/10 hover:bg-white/10"
    : "bg-slate-100 border-slate-200 hover:bg-slate-200";

  return (
    <div className={`h-full flex flex-col p-6 space-y-5 ${isDarkMode ? "text-slate-300" : "text-slate-800"}`}>
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className={`text-2xl font-black tracking-tight flex items-center gap-2 ${isDarkMode ? "text-white" : "text-slate-900"}`}>
            <ClipboardList size={22} /> 태스크 관리
          </h2>
          <p className="text-sm opacity-60 mt-1">
            {isPM ? "AI가 생성한 태스크를 배분하고 관리하세요." : "배분된 태스크를 수락 또는 거절하고 진행 상태를 업데이트하세요."}
          </p>
        </div>
        <button
          onClick={fetchTasks}
          disabled={loading}
          className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold border transition-all ${base}`}
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          새로고침
        </button>
      </div>

      {/* PM Quick Actions */}
      {isPM && (
        <div className={`p-4 rounded-2xl border space-y-3 ${isDarkMode ? "bg-white/5 border-white/10" : "bg-white border-slate-200 shadow-sm"}`}>
          <p className="text-xs font-bold uppercase tracking-wider opacity-60">AI 태스크 관리</p>

          {/* 1행: AI 생성 + 배분 */}
          <div className="flex flex-wrap gap-2">
            <button
              onClick={handleGenerateTasks}
              disabled={genLoading || !runId}
              title={!runId ? "파이프라인 실행 후 사용 가능" : ""}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold transition-all ${
                runId
                  ? "bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-500/10"
                  : isDarkMode ? "bg-white/5 text-slate-500 cursor-not-allowed" : "bg-slate-100 text-slate-400 cursor-not-allowed"
              }`}
            >
              {genLoading ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
              AI 태스크 생성
            </button>
            <button
              onClick={handleDistribute}
              disabled={distLoading}
              className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold bg-violet-600 hover:bg-violet-500 text-white shadow-lg shadow-violet-500/10 transition-all"
            >
              {distLoading ? <Loader2 size={12} className="animate-spin" /> : <Users size={12} />}
              팀 배분
            </button>
            <button
              onClick={() => { setShowCreateForm((v) => !v); setCreateMsg(""); }}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold border transition-all ${base}`}
            >
              <Plus size={12} /> 직접 생성
            </button>
          </div>

          {/* 피드백 메시지 */}
          {genMsg && (
            <p className={`text-xs ${genMsg.startsWith("실패") || genMsg.startsWith("연결") || genMsg.includes("없습니다") ? "text-red-400" : "text-emerald-400"}`}>
              {genMsg}
            </p>
          )}
          {distMsg && (
            <p className={`text-xs ${distMsg.startsWith("실패") || distMsg.startsWith("연결") ? "text-red-400" : "text-blue-400"}`}>
              {distMsg}
            </p>
          )}
          {missingZones.length > 0 && (
            <p className="text-xs text-amber-400">검토가 필요한 보안 테스트 영역: {missingZones.join(", ")}</p>
          )}
          {proposals.map(({ kind, item }) => (
            <div key={item.approval_token} className={`rounded-lg border p-3 text-xs ${base}`}>
              <p className="font-bold">{kind === "create" ? "신규" : "수정"}: {item.title || item.task_id}</p>
              <p className="mt-1 whitespace-pre-wrap">{kind === "create" ? item.description : JSON.stringify(item.changes)}</p>
              {kind === "create" && <p className="mt-1 opacity-70">
                유형 {item.task_type} · 영역 {item.area} · 공수 {item.effort} · 우선순위 {item.priority} · 기능 {item.feature_ref || "없음"}
              </p>}
              {kind === "update" && <p className="mt-1 opacity-70">이전 값: {JSON.stringify(item.before)} · 사유: {item.reason}</p>}
              <button onClick={() => handleApproveProposal(item.approval_token)} className="mt-2 rounded bg-emerald-600 px-3 py-1 text-white">
                검토 후 승인
              </button>
            </div>
          ))}

          {/* 커스텀 태스크 생성 폼 */}
          {showCreateForm && (
            <div className={`mt-1 p-4 rounded-xl border space-y-3 ${isDarkMode ? "bg-black/20 border-white/10" : "bg-slate-50 border-slate-200"}`}>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-bold opacity-60 mb-1">태스크 유형</label>
                  <select
                    value={createForm.task_type}
                    onChange={(e) => setCreateForm((f) => ({ ...f, task_type: e.target.value }))}
                    style={{ colorScheme: isDarkMode ? "dark" : "light" }}
                    className={`w-full px-3 py-2 rounded-lg text-xs font-semibold border outline-none ${isDarkMode ? "bg-slate-800 border-white/10 text-slate-100" : "bg-white border-slate-200 text-slate-800"}`}
                  >
                    {TASK_TYPES.map((t) => <option key={t} value={t}>{TASK_TYPE_LABEL[t] || t}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-bold opacity-60 mb-1">담당 영역</label>
                  <select
                    value={createForm.area}
                    onChange={(e) => setCreateForm((f) => ({ ...f, area: e.target.value }))}
                    style={{ colorScheme: isDarkMode ? "dark" : "light" }}
                    className={`w-full px-3 py-2 rounded-lg text-xs font-semibold border outline-none ${isDarkMode ? "bg-slate-800 border-white/10 text-slate-100" : "bg-white border-slate-200 text-slate-800"}`}
                  >
                    {AREAS.map((a) => <option key={a} value={a}>{AREA_LABEL[a] || a}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-xs font-bold opacity-60 mb-1">제목 *</label>
                <input
                  type="text"
                  value={createForm.title}
                  onChange={(e) => setCreateForm((f) => ({ ...f, title: e.target.value }))}
                  placeholder="태스크 제목을 입력하세요"
                  className={`w-full px-3 py-2 rounded-lg text-xs border outline-none ${isDarkMode ? "bg-white/5 border-white/10 text-white placeholder:text-slate-500" : "bg-white border-slate-200 text-slate-800"}`}
                />
              </div>
              <div>
                <label className="block text-xs font-bold opacity-60 mb-1">설명</label>
                <textarea
                  value={createForm.description}
                  onChange={(e) => setCreateForm((f) => ({ ...f, description: e.target.value }))}
                  placeholder="태스크에 대한 상세 설명"
                  rows={2}
                  className={`w-full px-3 py-2 rounded-lg text-xs border outline-none resize-none ${isDarkMode ? "bg-white/5 border-white/10 text-white placeholder:text-slate-500" : "bg-white border-slate-200 text-slate-800"}`}
                />
              </div>
              <div>
                <label className="block text-xs font-bold opacity-60 mb-1">담당자</label>
                <select
                  value={createForm.assignee}
                  onChange={(e) => setCreateForm((f) => ({ ...f, assignee: e.target.value }))}
                  style={{ colorScheme: isDarkMode ? "dark" : "light" }}
                  className={`w-full px-3 py-2 rounded-lg text-xs font-semibold border outline-none ${isDarkMode ? "bg-slate-800 border-white/10 text-slate-100" : "bg-white border-slate-200 text-slate-800"}`}
                >
                  <option value="">미할당</option>
                  {teamMembers.filter((m) => m.role !== "pm").map((m) => {
                    const roleLabel = {
                      software_engineer: "Software Engineer (개발자)",
                      backend:           "Backend (백엔드)",
                      frontend:          "Frontend (프론트엔드)",
                      devops:            "DevOps",
                    }[m.role] || m.role;
                    return (
                      <option key={m.id} value={m.name}>
                        {m.name} — {roleLabel}
                      </option>
                    );
                  })}
                </select>
              </div>
              <div className="flex gap-2 pt-1">
                <button
                  onClick={handleCreateCustomTask}
                  disabled={createLoading}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold bg-violet-600 hover:bg-violet-500 text-white transition-all"
                >
                  {createLoading ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
                  생성
                </button>
                <button
                  onClick={() => { setShowCreateForm(false); setCreateMsg(""); }}
                  className={`px-4 py-2 rounded-xl text-xs font-bold border transition-all ${base}`}
                >
                  취소
                </button>
              </div>
              {createMsg && (
                <p className={`text-xs ${createMsg.startsWith("실패") || createMsg.startsWith("연결") ? "text-red-400" : "text-emerald-400"}`}>
                  {createMsg}
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Type Filter Tabs */}
      <div className="flex gap-1.5 flex-wrap">
        {TYPE_FILTERS.map((item) => (
          <button
            key={item.id}
            onClick={() => setTaskTypeFilter(item.id)}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
              taskTypeFilter === item.id
                ? isDarkMode ? "bg-emerald-500/20 text-emerald-200" : "bg-emerald-700 text-white"
                : isDarkMode ? "bg-white/5 text-slate-400 hover:text-slate-200" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {item.label}
            <span className="ml-1.5 opacity-60">({countType(item.id)})</span>
          </button>
        ))}
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-1.5 flex-wrap">
        {FILTER_TABS.map((s) => (
          <button
            key={s}
            onClick={() => setFilterStatus(s)}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
              filterStatus === s
                ? isDarkMode ? "bg-white/15 text-white" : "bg-slate-800 text-white"
                : isDarkMode ? "bg-white/5 text-slate-400 hover:text-slate-200" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {s === "all" ? "전체" : STATUS_CONFIG[s]?.label || s}
            {s !== "all" && <span className="ml-1.5 opacity-60">({countOf(s)})</span>}
          </button>
        ))}
      </div>

      {/* Error */}
      {error && (
        <div className={`p-3 rounded-xl border-l-4 border-red-500 text-sm ${isDarkMode ? "bg-red-500/10 text-red-300" : "bg-red-50 text-red-700"}`}>
          {error}
        </div>
      )}

      {/* 선택 삭제 툴바 — 미할당 탭 + 선택 항목 있을 때 */}
      {filterStatus === "unassigned" && isPM && (
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              const allIds = new Set(visibleTasks.map((t) => t.id));
              const allSelected = visibleTasks.every((t) => selectedIds.has(t.id));
              setSelectedIds(allSelected ? new Set() : allIds);
            }}
            className={`text-xs font-bold px-3 py-1.5 rounded-lg border transition-all ${base}`}
          >
            {visibleTasks.length > 0 && visibleTasks.every((t) => selectedIds.has(t.id)) ? "전체 해제" : "전체 선택"}
          </button>
          {selectedIds.size > 0 && (
            <button
              onClick={handleDeleteSelected}
              disabled={deleteLoading}
              className="flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/30 transition-all"
            >
              {deleteLoading ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
              선택 삭제 ({selectedIds.size})
            </button>
          )}
        </div>
      )}

      {/* Task List */}
      <div className="flex-1 overflow-y-auto space-y-3 custom-scrollbar pr-1">
        {loading && visibleTasks.length === 0 && (
          <div className="h-40 flex items-center justify-center opacity-30">
            <Loader2 size={32} className="animate-spin" />
          </div>
        )}
        {!loading && visibleTasks.length === 0 && (
          <div className={`h-48 flex flex-col items-center justify-center gap-3 opacity-30 border-2 border-dashed rounded-3xl ${isDarkMode ? "border-white/10" : "border-slate-200"}`}>
            <ClipboardList size={48} />
            <p>태스크가 없습니다.</p>
          </div>
        )}

        {/* 미할당: 도메인별 그룹 */}
        {unassignedGroups && unassignedGroups.map(({ area, tasks: groupTasks }) => (
          <div key={area || "etc"} className="space-y-2">
            <div className="flex items-center gap-2">
              <span className={`text-xs font-bold px-2.5 py-1 rounded-lg ${isDarkMode ? "bg-white/10 text-slate-300" : "bg-slate-200 text-slate-600"}`}>
                {AREA_LABEL[area] || "기타"}
              </span>
              <span className="text-xs opacity-40">{groupTasks.length}개</span>
              {isPM && (
                <button
                  onClick={() => {
                    const groupIds = new Set(groupTasks.map((t) => t.id));
                    const allSelected = groupTasks.every((t) => selectedIds.has(t.id));
                    setSelectedIds((prev) => {
                      const next = new Set(prev);
                      groupIds.forEach((id) => allSelected ? next.delete(id) : next.add(id));
                      return next;
                    });
                  }}
                  className="text-[11px] opacity-50 hover:opacity-100 transition-opacity"
                >
                  {groupTasks.every((t) => selectedIds.has(t.id)) ? "그룹 해제" : "그룹 선택"}
                </button>
              )}
            </div>
            {groupTasks.map((task) => renderTaskCard(task))}
          </div>
        ))}

        {/* 그 외 탭: 기존 방식 */}
        {!unassignedGroups && visibleTasks.map((task) => renderTaskCard(task))}
      </div>
    </div>
  );

  function renderTaskCard(task) {
          const cfg        = STATUS_CONFIG[task.status] || STATUS_CONFIG.unassigned;
          const isExpanded = expandedIds.has(task.id);
          const isMyTask   = task.assignee && task.assignee === currentUser?.name;
          const isDevGapApproval = task.task_type === "dev_gap_approval";
          const isSelected = selectedIds.has(task.id);
          const payload = task.payload || {};
          const pmReport = payload.pm_report || {};
          const prContext = payload.pr_context || {};
          const gapReport = Array.isArray(payload.gap_report) ? payload.gap_report : [];
          // author:xxrin
          // LLM fallback 상태를 PM이 놓치지 않도록 PM Report warning을 Dev GAP 카드에 표시한다.
          const llmWarnings = Array.isArray(pmReport.llm_warnings) ? pmReport.llm_warnings : [];
          const highGapCount = gapReport.filter((gap) => gap?.severity === "HIGH").length;
          const taskResult = parseTaskResult(task.result);
          const followup = taskResult.followup || {};
          const statusCheck = taskResult.status_check || {};
          const docSync = followup.doc_sync || {};
          const prComment = followup.pr_comment || {};
          const ragMetadata = followup.rag_metadata || {};
          const codeChunkUpsert = followup.code_chunk_upsert || {};
          const saReviewTask = taskResult.sa_review_task || {};
          const normalizedGapReport = gapReport.map((gap, index) => ({
            gap_id: gap?.gap_id || `GAP_${String(index + 1).padStart(3, "0")}`,
            severity: String(gap?.severity || "UNKNOWN").toUpperCase(),
            type: gap?.type || "UNKNOWN",
            spec_target: gap?.spec_target || "-",
            description: gap?.description || "설명 없음",
            recommended_action: gap?.recommended_action || "-",
            intent: gap?.intent || "-",
            preliminary: Boolean(gap?.preliminary),
          }));

          return (
            <div key={task.id} className={`rounded-2xl border transition-all ${isSelected ? "ring-2 ring-blue-500/50" : ""} ${cfg.bg} ${cfg.border}`}>
              <div className="flex items-center gap-2 pr-4">
                {/* 체크박스 — 미할당 + PM만 */}
                {task.status === "unassigned" && isPM && (
                  <button
                    onClick={() => toggleSelect(task.id)}
                    className={`ml-3 shrink-0 w-4 h-4 rounded border flex items-center justify-center transition-all ${
                      isSelected
                        ? "bg-blue-500 border-blue-500"
                        : isDarkMode ? "border-white/20 hover:border-white/40" : "border-slate-300 hover:border-slate-500"
                    }`}
                  >
                    {isSelected && <Check size={10} className="text-white" />}
                  </button>
                )}
              <button
                type="button"
                onClick={() => toggleExpand(task.id)}
                className="flex-1 flex items-center gap-3 p-4 text-left"
              >
                <cfg.Icon size={18} className={cfg.color} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-xs font-bold px-2 py-0.5 rounded ${cfg.bg} ${cfg.color}`}>
                      {TASK_TYPE_LABEL[task.task_type] || task.task_type}
                    </span>
                    {task.area && (
                      <span className={`text-xs px-2 py-0.5 rounded font-semibold ${isDarkMode ? "bg-white/5 text-slate-400" : "bg-slate-100 text-slate-500"}`}>
                        {AREA_LABEL[task.area] || task.area}
                      </span>
                    )}
                    {task.effort && (
                      <span className="text-xs opacity-50 font-mono">{EFFORT_LABEL[task.effort] || task.effort}</span>
                    )}
                    <span className={`font-semibold text-sm truncate ${isDarkMode ? "text-slate-200" : "text-slate-800"}`}>
                      {task.title}
                    </span>
                  </div>
                  <p className="text-xs opacity-50 mt-0.5 flex items-center gap-1.5 flex-wrap">
                    <span>{task.created_at?.slice(0, 16).replace("T", " ")}</span>
                    {task.assignee
                      ? <span className="before:content-['·'] before:mr-1.5">{task.assignee}</span>
                      : <span className="before:content-['·'] before:mr-1.5 italic">미할당</span>
                    }
                    {task.feature_ref && <span className="before:content-['·'] before:mr-1.5 font-mono">{task.feature_ref}</span>}
                  </p>
                </div>
                <span className={`text-xs font-bold shrink-0 ${cfg.color}`}>{cfg.label}</span>
                {isExpanded ? <ChevronDown size={16} className="opacity-50 shrink-0" /> : <ChevronRight size={16} className="opacity-50 shrink-0" />}
              </button>
              </div>

              {isExpanded && (
                <div className={`px-4 pb-4 space-y-3 border-t ${isDarkMode ? "border-white/5" : "border-slate-100"}`}>
                  {/* 인라인 편집 폼 (unassigned 또는 rejected + PM) */}
                  {isPM && (task.status === "unassigned" || task.status === "rejected") && editingId === task.id ? (
                    <div className="pt-3 space-y-2">
                      <div className="grid grid-cols-3 gap-2">
                        <div>
                          <label className="block text-xs font-bold opacity-50 mb-1">유형</label>
                          <select
                            value={editForm.task_type}
                            onChange={(e) => setEditForm((f) => ({ ...f, task_type: e.target.value }))}
                            style={{ colorScheme: isDarkMode ? "dark" : "light" }}
                            className={`w-full px-2 py-1.5 rounded-lg text-xs font-semibold border outline-none ${isDarkMode ? "bg-slate-800 border-white/10 text-slate-100" : "bg-white border-slate-200 text-slate-800"}`}
                          >
                            {TASK_TYPES.map((t) => <option key={t} value={t}>{TASK_TYPE_LABEL[t] || t}</option>)}
                          </select>
                        </div>
                        <div>
                          <label className="block text-xs font-bold opacity-50 mb-1">영역</label>
                          <select
                            value={editForm.area}
                            onChange={(e) => setEditForm((f) => ({ ...f, area: e.target.value }))}
                            style={{ colorScheme: isDarkMode ? "dark" : "light" }}
                            className={`w-full px-2 py-1.5 rounded-lg text-xs font-semibold border outline-none ${isDarkMode ? "bg-slate-800 border-white/10 text-slate-100" : "bg-white border-slate-200 text-slate-800"}`}
                          >
                            {AREAS.map((a) => <option key={a} value={a}>{AREA_LABEL[a] || a}</option>)}
                          </select>
                        </div>
                        <div>
                          <label className="block text-xs font-bold opacity-50 mb-1">노력도</label>
                          <select
                            value={editForm.effort}
                            onChange={(e) => setEditForm((f) => ({ ...f, effort: e.target.value }))}
                            style={{ colorScheme: isDarkMode ? "dark" : "light" }}
                            className={`w-full px-2 py-1.5 rounded-lg text-xs font-semibold border outline-none ${isDarkMode ? "bg-slate-800 border-white/10 text-slate-100" : "bg-white border-slate-200 text-slate-800"}`}
                          >
                            {Object.keys(EFFORT_LABEL).map((e) => <option key={e} value={e}>{EFFORT_LABEL[e]}</option>)}
                          </select>
                        </div>
                      </div>
                      <div>
                        <label className="block text-xs font-bold opacity-50 mb-1">제목</label>
                        <input
                          type="text"
                          value={editForm.title}
                          onChange={(e) => setEditForm((f) => ({ ...f, title: e.target.value }))}
                          className={`w-full px-3 py-2 rounded-lg text-xs border outline-none ${isDarkMode ? "bg-white/5 border-white/10 text-white" : "bg-white border-slate-200 text-slate-800"}`}
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-bold opacity-50 mb-1">설명</label>
                        <textarea
                          value={editForm.description}
                          onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))}
                          rows={3}
                          className={`w-full px-3 py-2 rounded-lg text-xs border outline-none resize-none ${isDarkMode ? "bg-white/5 border-white/10 text-white" : "bg-white border-slate-200 text-slate-800"}`}
                        />
                      </div>
                      <div className="flex gap-2 flex-wrap">
                        {task.status === "rejected" && task.assignee ? (
                          <>
                            <button
                              onClick={() => handleSaveEdit(task.id, { status: "pending_approval", assignee: task.assignee, clearResult: true })}
                              disabled={editLoading}
                              className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white transition-all"
                            >
                              {editLoading ? <Loader2 size={12} className="animate-spin" /> : <UserCheck size={12} />}
                              수정 후 재할당
                            </button>
                            <button
                              onClick={() => handleSaveEdit(task.id, { status: "unassigned", assignee: "", clearResult: true })}
                              disabled={editLoading}
                              className={`flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold border transition-all ${base}`}
                            >
                              {editLoading ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                              수정 후 미할당
                            </button>
                          </>
                        ) : (
                          <button
                            onClick={() => handleSaveEdit(task.id)}
                            disabled={editLoading}
                            className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white transition-all"
                          >
                            {editLoading ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                            저장
                          </button>
                        )}
                        <button
                          onClick={() => setEditingId(null)}
                          className={`px-4 py-2 rounded-xl text-xs font-bold border transition-all ${base}`}
                        >
                          취소
                        </button>
                      </div>
                    </div>
                  ) : (
                    task.description && (
                      <p className="text-sm opacity-70 pt-3 leading-relaxed">{task.description}</p>
                    )
                  )}

                  {/* PM 액션: pending_approval → 승인(in_progress) / 거절(rejected) */}
                  {isDevGapApproval && (
                    <div className={`rounded-xl border p-3 space-y-2 ${isDarkMode ? "bg-black/20 border-white/10" : "bg-white border-slate-200"}`}>
                      <div className="flex flex-wrap items-center gap-2 text-xs font-semibold">
                        <span className={`px-2 py-1 rounded ${isDarkMode ? "bg-white/10 text-slate-200" : "bg-slate-100 text-slate-700"}`}>
                          PR #{prContext.pr_number || "-"}
                        </span>
                        <span className={`px-2 py-1 rounded font-mono ${isDarkMode ? "bg-white/10 text-slate-300" : "bg-slate-100 text-slate-600"}`}>
                          {prContext.branch_name || "unknown branch"}
                        </span>
                        <span className="px-2 py-1 rounded bg-red-500/10 text-red-400">
                          GAP {gapReport.length}
                        </span>
                        {highGapCount > 0 && (
                          <span className="px-2 py-1 rounded bg-orange-500/10 text-orange-400">
                            HIGH {highGapCount}
                          </span>
                        )}
                        {(prContext.owner || prContext.repo) && (
                          <span className={`px-2 py-1 rounded font-mono ${isDarkMode ? "bg-white/5 text-slate-400" : "bg-slate-100 text-slate-500"}`}>
                            {prContext.owner || "-"} / {prContext.repo || "-"}
                          </span>
                        )}
                        {payload.approval_status && (
                          <span className={`px-2 py-1 rounded font-bold ${
                            payload.approval_status === "approved"
                              ? "bg-emerald-500/10 text-emerald-400"
                              : payload.approval_status === "rejected"
                              ? "bg-red-500/10 text-red-400"
                              : "bg-blue-500/10 text-blue-400"
                          }`}>
                            {payload.approval_status}
                          </span>
                        )}
                      </div>
                      {pmReport.summary && (
                        <p className="text-xs leading-relaxed opacity-80">{pmReport.summary}</p>
                      )}
                      {llmWarnings.length > 0 && (
                        <div className={`rounded-xl border p-3 space-y-2 ${isDarkMode ? "bg-yellow-500/10 border-yellow-500/20 text-yellow-100" : "bg-yellow-50 border-yellow-200 text-yellow-900"}`}>
                          <div className="flex items-center gap-2 text-xs font-bold">
                            <AlertTriangle size={14} className="shrink-0" />
                            <span>LLM analysis failed. Manual PM review required.</span>
                          </div>
                          <div className="space-y-1">
                            {llmWarnings.map((warning, index) => (
                              <p key={`${warning.node || "llm"}-${index}`} className="text-[11px] leading-relaxed opacity-85">
                                <span className="font-semibold">{warning.node || "llm"}:</span>{" "}
                                {warning.message || warning.llm_error_message || "Fallback analysis was used."}
                              </p>
                            ))}
                          </div>
                        </div>
                      )}
                      {Array.isArray(pmReport.recommended_pm_actions) && pmReport.recommended_pm_actions.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {pmReport.recommended_pm_actions.map((action) => (
                            <span key={action} className={`text-[11px] px-2 py-0.5 rounded border ${isDarkMode ? "border-white/10 text-slate-300" : "border-slate-200 text-slate-600"}`}>
                              {action}
                            </span>
                          ))}
                        </div>
                      )}
                      {normalizedGapReport.length > 0 && (
                        <div className="space-y-1.5 pt-1">
                          <p className="text-[11px] font-bold opacity-50 uppercase tracking-wide">Gap Report Details</p>
                          {normalizedGapReport.map((gap) => (
                            <div key={gap.gap_id} className={`rounded-lg p-2 text-xs space-y-0.5 ${isDarkMode ? "bg-white/5" : "bg-slate-50"}`}>
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="font-mono font-bold opacity-60">{gap.gap_id}</span>
                                <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                  gap.severity === "HIGH" ? "bg-red-500/20 text-red-400" :
                                  gap.severity === "MEDIUM" ? "bg-orange-500/20 text-orange-400" :
                                  "bg-slate-500/20 text-slate-400"
                                }`}>{gap.severity}</span>
                                <span className="opacity-60">{gap.type}</span>
                                {gap.preliminary && <span className="px-1.5 py-0.5 rounded text-[10px] bg-blue-500/10 text-blue-400">preliminary</span>}
                              </div>
                              <p className="opacity-80">{gap.description}</p>
                              {gap.recommended_action !== "-" && (
                                <p className="opacity-60"><span className="font-semibold">권장:</span> {gap.recommended_action}</p>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {/* PM 편집: unassigned 상태에서만 (rejected는 해당 패널에서 처리) */}
                  {isPM && task.status === "unassigned" && editingId !== task.id && (
                    <div className="flex gap-2 pt-1">
                      <button
                        onClick={(e) => { e.stopPropagation(); startEdit(task); }}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold border transition-all ${base}`}
                      >
                        <Pencil size={12} /> 편집
                      </button>
                    </div>
                  )}

                  {/* 일반 태스크: 담당자가 수락/거절 */}
                  {!isDevGapApproval && isMyTask && task.status === "pending_approval" && (
                    rejectingId === task.id ? (
                      <div className={`pt-2 space-y-2 p-3 rounded-xl border ${isDarkMode ? "bg-red-500/5 border-red-500/20" : "bg-red-50 border-red-200"}`}>
                        <p className="text-xs font-bold text-red-400">거절 사유를 입력하세요</p>
                        <textarea
                          value={rejectReason}
                          onChange={(e) => setRejectReason(e.target.value)}
                          placeholder="거절 이유를 작성해주세요 (PM이 확인합니다)"
                          rows={2}
                          className={`w-full px-3 py-2 rounded-lg text-xs border outline-none resize-none ${isDarkMode ? "bg-white/5 border-white/10 text-white placeholder:text-slate-500" : "bg-white border-slate-200 text-slate-800"}`}
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleRejectWithReason(task.id)}
                            className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold bg-red-500 hover:bg-red-400 text-white transition-all"
                          >
                            <X size={12} /> 거절 확인
                          </button>
                          <button
                            onClick={() => { setRejectingId(null); setRejectReason(""); }}
                            className={`px-4 py-2 rounded-xl text-xs font-bold border transition-all ${base}`}
                          >
                            취소
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex gap-2 pt-1">
                        <button
                          onClick={() => handleAction(task.id, "in_progress")}
                          className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-500/10 transition-all"
                        >
                          <Check size={12} /> 수락
                        </button>
                        <button
                          onClick={() => { setRejectingId(task.id); setRejectReason(""); }}
                          className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/30 transition-all"
                        >
                          <X size={12} /> 거절
                        </button>
                      </div>
                    )
                  )}

                  {/* Dev GAP 태스크: PM이 승인/거절 */}
                  {isDevGapApproval && isPM && task.status === "pending_approval" && (
                    rejectingId === task.id ? (
                      <div className={`pt-2 space-y-2 p-3 rounded-xl border ${isDarkMode ? "bg-red-500/5 border-red-500/20" : "bg-red-50 border-red-200"}`}>
                        <p className="text-xs font-bold text-red-400">거절 사유를 입력하세요</p>
                        <textarea
                          value={rejectReason}
                          onChange={(e) => setRejectReason(e.target.value)}
                          placeholder="거절 이유를 작성해주세요"
                          rows={2}
                          className={`w-full px-3 py-2 rounded-lg text-xs border outline-none resize-none ${isDarkMode ? "bg-white/5 border-white/10 text-white placeholder:text-slate-500" : "bg-white border-slate-200 text-slate-800"}`}
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleDevGapDecision(task.id, "reject", rejectReason)}
                            className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold bg-red-500 hover:bg-red-400 text-white transition-all"
                          >
                            <X size={12} /> 거절 확인
                          </button>
                          <button
                            onClick={() => { setRejectingId(null); setRejectReason(""); }}
                            className={`px-4 py-2 rounded-xl text-xs font-bold border transition-all ${base}`}
                          >
                            취소
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex gap-2 pt-1">
                        <button
                          onClick={() => handleDevGapDecision(task.id, "approve", getDecisionReason(task.id))}
                          className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-500/10 transition-all"
                        >
                          <Check size={12} /> 승인
                        </button>
                        <button
                          onClick={() => { setRejectingId(task.id); setRejectReason(""); }}
                          className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/30 transition-all"
                        >
                          <X size={12} /> 거절
                        </button>
                      </div>
                    )
                  )}
                  {/* Dev GAP 결정 결과 표시 */}
                  {isDevGapApproval && (taskResult.approval_status || payload.approval_status) && task.status !== "pending_approval" && (
                    <div className={`p-3 rounded-xl border text-xs space-y-1 ${
                      (taskResult.approval_status || payload.approval_status) === "approved"
                        ? isDarkMode ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-300" : "bg-emerald-50 border-emerald-200 text-emerald-700"
                        : isDarkMode ? "bg-red-500/10 border-red-500/20 text-red-300" : "bg-red-50 border-red-200 text-red-700"
                    }`}>
                      <p className="font-bold">
                        {(taskResult.approval_status || payload.approval_status) === "approved" ? "✓ 승인됨" : "✗ 거절됨"}
                        {(taskResult.approved_by || payload.approved_by) && (
                          <span className="font-normal opacity-70 ml-2">by {taskResult.approved_by || payload.approved_by}</span>
                        )}
                      </p>
                      {(taskResult.rejection_reason || payload.rejection_reason) && (
                        <p className="opacity-80">사유: {taskResult.rejection_reason || payload.rejection_reason}</p>
                      )}
                    </div>
                  )}

                  {/* 작업자 액션: in_progress → PR대기중 */}
                  {isMyTask && task.status === "in_progress" && (
                    <div className="flex gap-2 pt-1">
                      <button
                        onClick={() => handleAction(task.id, "pr_pending")}
                        className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold bg-violet-600 hover:bg-violet-500 text-white shadow-lg shadow-violet-500/10 transition-all"
                      >
                        <GitPullRequest size={12} /> PR 제출
                      </button>
                    </div>
                  )}

                  {/* PM: rejected 태스크 처리 옵션 */}
                  {isPM && !isDevGapApproval && task.status === "rejected" && (
                    <div className={`p-3 rounded-xl border space-y-2 ${isDarkMode ? "bg-orange-500/5 border-orange-500/20" : "bg-orange-50 border-orange-200"}`}>
                      {task.result && (
                        <div className="text-xs text-orange-400 font-semibold">
                          거절 사유: <span className="font-normal opacity-80">{task.result}</span>
                        </div>
                      )}
                      <p className="text-xs font-bold opacity-60">이 태스크를 어떻게 처리할까요?</p>
                      {reassignId === task.id ? (
                        <div className="space-y-2">
                          <select
                            value={reassignTarget}
                            onChange={(e) => setReassignTarget(e.target.value)}
                            style={{ colorScheme: isDarkMode ? "dark" : "light" }}
                            className={`w-full px-3 py-2 rounded-lg text-xs font-semibold border outline-none ${isDarkMode ? "bg-slate-800 border-white/10 text-slate-100" : "bg-white border-slate-200 text-slate-800"}`}
                          >
                            <option value="">담당자 선택</option>
                            {teamMembers.filter((m) => m.role !== "pm" && m.name !== task.assignee).map((m) => (
                              <option key={m.id} value={m.name}>{m.name} — {m.role}</option>
                            ))}
                          </select>
                          <div className="flex gap-2">
                            <button
                              onClick={() => handleReassign(task.id)}
                              disabled={!reassignTarget}
                              className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold bg-violet-600 hover:bg-violet-500 text-white transition-all disabled:opacity-40"
                            >
                              <UserCheck size={12} /> 배정
                            </button>
                            <button
                              onClick={() => { setReassignId(null); setReassignTarget(""); }}
                              className={`px-4 py-2 rounded-xl text-xs font-bold border transition-all ${base}`}
                            >
                              취소
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex flex-wrap gap-2">
                          <button
                            onClick={() => handleReturnToUnassigned(task.id)}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold border transition-all ${base}`}
                          >
                            <RotateCcw size={12} /> 미할당으로
                          </button>
                          <button
                            onClick={() => startEdit(task)}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold border transition-all ${base}`}
                          >
                            <Pencil size={12} /> 내용 수정
                          </button>
                          <button
                            onClick={() => { setReassignId(task.id); setReassignTarget(""); }}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-violet-600 hover:bg-violet-500 text-white transition-all"
                          >
                            <UserCheck size={12} /> 다른 팀원에게
                          </button>
                        </div>
                      )}
                    </div>
                  )}

                  {/* 삭제: completed / rejected 만 */}
                  {(task.status === "completed" || task.status === "rejected") && (
                    <div className="flex justify-end pt-1">
                      <button
                        onClick={() => handleDelete(task.id)}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold text-slate-500 hover:text-red-400 hover:bg-red-500/10 border border-transparent hover:border-red-500/20 transition-all"
                      >
                        <Trash2 size={12} /> 삭제
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
  }
}
