import React, { useEffect, useState } from "react";
import { Upload, Eye, Trash2, Clock, User, ChevronLeft, Send, Download } from "lucide-react";
import useAppStore from "../../store/useAppStore";
import Button from "../ui/Button";
import Badge from "../ui/Badge";
import Card from "../ui/Card";

// ── Publish 모달 ──────────────────────────────────────────────

function PublishModal({ onClose }) {
  const { localResults, loadLocalResults, publishResult, publishLoading, publishError } = useAppStore((s) => ({
    localResults: s.localResults,
    loadLocalResults: s.loadLocalResults,
    publishResult: s.publishResult,
    publishLoading: s.publishLoading,
    publishError: s.publishError,
  }));

  const [selectedRunId, setSelectedRunId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");

  useEffect(() => { loadLocalResults(); }, []);

  const handlePublish = async () => {
    if (!selectedRunId || !title.trim()) return;
    const res = await publishResult({ runId: selectedRunId, title: title.trim(), description });
    if (res.success) onClose();
  };

  const selected = localResults.find((r) => r.run_id === selectedRunId);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-[#1a1a2e] border border-white/10 rounded-xl w-[520px] max-h-[80vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/10">
          <h2 className="text-white font-semibold text-lg flex items-center gap-2">
            <Upload size={18} className="text-purple-400" />
            분석 결과 Publish
          </h2>
          <button onClick={onClose} className="text-white/40 hover:text-white transition-colors text-xl leading-none">&times;</button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {/* 결과 선택 */}
          <div>
            <label className="block text-xs text-white/50 mb-1">분석 결과 선택</label>
            <select
              value={selectedRunId}
              onChange={(e) => {
                setSelectedRunId(e.target.value);
                const r = localResults.find((x) => x.run_id === e.target.value);
                if (r && !title) setTitle(r.title || r.run_id);
              }}
              className="w-full bg-[#1a1a2e] border border-white/10 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-purple-500 cursor-pointer"
            >
              <option value="" className="bg-[#1a1a2e] text-white/50">-- 선택하세요 --</option>
              {localResults.map((r) => (
                <option key={r.run_id} value={r.run_id} className="bg-[#1a1a2e] text-white">
                  {r.title || r.run_id} {r.is_published ? " ✓" : ""}
                </option>
              ))}
            </select>
            {selected?.is_published && (
              <p className="text-xs text-yellow-400 mt-1">이미 Publish된 결과입니다. 재Publish 시 새 버전으로 등록됩니다.</p>
            )}
          </div>

          {/* 선택된 결과 미리보기 */}
          {selected && (() => {
            const d = selected.data || selected;
            const rtmCount = Array.isArray(d.requirements_rtm) ? d.requirements_rtm.length : 0;
            const comCount = Array.isArray(d.components) ? d.components.length : (Array.isArray(d.sa_output?.data?.components) ? d.sa_output.data.components.length : 0);
            const stackCount = Array.isArray(d.tech_stacks) ? d.tech_stacks.length : 0;
            const apiCount = Array.isArray(d.apis) ? d.apis.length : (Array.isArray(d.sa_output?.data?.apis) ? d.sa_output.data.apis.length : 0);
            return (
              <div className="bg-white/3 border border-white/10 rounded-lg px-4 py-3 space-y-1.5">
                <p className="text-[10px] font-semibold text-white/40 uppercase tracking-widest mb-2">미리보기</p>
                <div className="flex flex-wrap gap-3 text-xs text-white/60">
                  {rtmCount > 0 && <span className="bg-blue-500/10 border border-blue-500/20 rounded px-2 py-0.5 text-blue-300">기능 {rtmCount}개</span>}
                  {comCount > 0 && <span className="bg-purple-500/10 border border-purple-500/20 rounded px-2 py-0.5 text-purple-300">컴포넌트 {comCount}개</span>}
                  {apiCount > 0 && <span className="bg-emerald-500/10 border border-emerald-500/20 rounded px-2 py-0.5 text-emerald-300">API {apiCount}개</span>}
                  {stackCount > 0 && <span className="bg-orange-500/10 border border-orange-500/20 rounded px-2 py-0.5 text-orange-300">스택 {stackCount}개</span>}
                  {!rtmCount && !comCount && !apiCount && !stackCount && (
                    <span className="text-white/30">메타데이터 없음</span>
                  )}
                </div>
              </div>
            );
          })()}

          {/* 제목 */}
          <div>
            <label className="block text-xs text-white/50 mb-1">제목 (커밋 메시지)</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="예: v1.0 — 초기 설계 완성"
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white text-sm placeholder-white/20 focus:outline-none focus:border-purple-500"
            />
          </div>

          {/* 설명 */}
          <div>
            <label className="block text-xs text-white/50 mb-1">설명 (선택)</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="변경 사항 또는 공유 목적을 간략히 적어주세요."
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white text-sm placeholder-white/20 focus:outline-none focus:border-purple-500 resize-none"
            />
          </div>

          {publishError && (
            <p className="text-red-400 text-xs bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{publishError}</p>
          )}
        </div>

        <div className="flex justify-end gap-2 px-6 py-4 border-t border-white/10">
          <button onClick={onClose} className="px-4 py-2 text-sm text-white/50 hover:text-white transition-colors">취소</button>
          <button
            onClick={handlePublish}
            disabled={!selectedRunId || !title.trim() || publishLoading}
            className="px-4 py-2 text-sm bg-purple-600 hover:bg-purple-500 disabled:opacity-40 text-white rounded-lg transition-colors flex items-center gap-2"
          >
            <Send size={14} />
            {publishLoading ? "업로드 중..." : "Publish"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 스냅샷 상세 뷰 ────────────────────────────────────────────

const PRIORITY_COLORS = {
  Must: "bg-red-500/20 text-red-300 border-red-500/30",
  Should: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
  Could: "bg-blue-500/20 text-blue-300 border-blue-500/30",
};

function SectionHeading({ label }) {
  return <h3 className="text-white/50 text-[10px] font-semibold uppercase tracking-widest mb-3">{label}</h3>;
}

function InfoRow({ label, value }) {
  if (!value && value !== 0) return null;
  return (
    <div className="flex gap-2 text-sm">
      <span className="text-white/40 shrink-0 w-28">{label}</span>
      <span className="text-white/80">{String(value)}</span>
    </div>
  );
}

function SnapshotDetail({ snapshot, onBack, onDelete, canDelete, onPull, isPulling }) {
  const data = snapshot.data || {};
  const saData = data.sa_output?.data || data.sa_merge_project_output?.data || {};
  const proj = data.project_overview || {};
  const sa = data.sa_overview || data.sa_output?.overview || {};
  const rtm = Array.isArray(data.requirements_rtm) ? data.requirements_rtm
            : Array.isArray(data.features) ? data.features : [];
  const apis = Array.isArray(data.apis) ? data.apis
             : Array.isArray(saData.apis) ? saData.apis : [];
  const tables = Array.isArray(data.tables) ? data.tables
               : Array.isArray(saData.tables) ? saData.tables : [];
  const components = Array.isArray(data.components) ? data.components
                   : Array.isArray(saData.components) ? saData.components : [];
  const techStacks = Array.isArray(data.tech_stacks) ? data.tech_stacks : [];
  const metrics = data.metrics || {};

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-3 px-6 py-4 border-b border-white/10">
        <button onClick={onBack} className="text-white/40 hover:text-white transition-colors">
          <ChevronLeft size={20} />
        </button>
        <div className="flex-1">
          <h2 className="text-white font-semibold">{snapshot.title}</h2>
          <p className="text-xs text-white/40 mt-0.5">
            v{snapshot.version} · {new Date(snapshot.published_at).toLocaleString("ko-KR")}
          </p>
        </div>
        {!data.artifact_kind && <button
          onClick={() => onPull(snapshot)}
          disabled={isPulling}
          className="px-3 py-1.5 text-xs bg-purple-600 hover:bg-purple-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg transition-colors flex items-center gap-1.5 mr-2"
        >
          <Download size={13} />
          {isPulling ? "Pull 중..." : "Pull"}
        </button>}
        {canDelete && (
          <button onClick={() => onDelete(snapshot.id)} className="p-2 text-red-400/60 hover:text-red-400 transition-colors rounded-lg hover:bg-red-500/10">
            <Trash2 size={16} />
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-5 custom-scrollbar">
        {snapshot.description && (
          <p className="text-white/60 text-sm bg-white/5 rounded-lg px-4 py-3 border border-white/5">{snapshot.description}</p>
        )}

        {/* 프로젝트 개요 */}
        {(proj.project_name || proj.summary) && (
          <Card className="bg-white/3 border-white/8 space-y-2">
            <SectionHeading label="프로젝트 개요" />
            <InfoRow label="프로젝트명" value={proj.project_name} />
            {proj.summary && <p className="text-white/70 text-sm leading-relaxed mt-1">{proj.summary}</p>}
            {techStacks.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {techStacks.map((s, i) => (
                  <span key={i} className="px-2 py-0.5 rounded bg-blue-500/15 text-blue-300 text-[11px] border border-blue-500/20">
                    {s.package || s.pkg || s.name || String(s)}
                  </span>
                ))}
              </div>
            )}
          </Card>
        )}

        {/* PM / SA 지표 */}
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: "성능", value: metrics.performance != null ? `${metrics.performance}점` : null, color: "text-emerald-400" },
            { label: "안정성", value: metrics.stability != null ? `${metrics.stability}점` : null, color: "text-blue-400" },
            { label: "Integrity", value: metrics.integrity || (sa.feasibility?.status), color: metrics.integrity === "PASS" ? "text-emerald-400" : "text-amber-400" },
          ].map(({ label, value, color }) => value ? (
            <div key={label} className="bg-white/5 rounded-lg p-3 border border-white/8 text-center">
              <p className="text-white/40 text-[10px] mb-1">{label}</p>
              <p className={`font-bold text-sm ${color}`}>{value}</p>
            </div>
          ) : null)}
        </div>

        {/* RTM */}
        {rtm.length > 0 && (
          <Card className="bg-white/3 border-white/8">
            <SectionHeading label={`요구사항 RTM (${rtm.length})`} />
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-white/10">
                    {["ID", "설명", "우선순위", "카테고리"].map(h => (
                      <th key={h} className="text-left py-1.5 px-2 text-white/40 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rtm.map((req, i) => {
                    const pri = req.priority || req.pri || "";
                    return (
                      <tr key={i} className="border-b border-white/5">
                        <td className="py-1.5 px-2 text-blue-400 font-mono whitespace-nowrap">
                          {req.feature_id || req.id}<br />
                          {req.label && <span className="text-white/30 font-sans">[{req.label}]</span>}
                        </td>
                        <td className="py-1.5 px-2 text-white/70 max-w-xs">{req.description || req.desc || req.label || "-"}</td>
                        <td className="py-1.5 px-2">
                          {pri ? <span className={`px-1.5 py-0.5 rounded border text-[10px] ${PRIORITY_COLORS[pri] || "bg-slate-700 text-slate-300 border-slate-600"}`}>{pri}</span> : "-"}
                        </td>
                        <td className="py-1.5 px-2 text-white/50">{req.category || req.cat || "-"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {/* APIs */}
        {apis.length > 0 && (
          <Card className="bg-white/3 border-white/8">
            <SectionHeading label={`API Spec (${apis.length})`} />
            <div className="space-y-1">
              {apis.map((api, i) => (
                <div key={i} className="flex items-center gap-2 text-xs py-1 border-b border-white/5">
                  <span className={`px-1.5 py-0.5 rounded font-mono font-bold text-[10px] ${
                    api.method === "GET" ? "bg-emerald-500/20 text-emerald-400" :
                    api.method === "POST" ? "bg-blue-500/20 text-blue-400" :
                    api.method === "PUT" ? "bg-yellow-500/20 text-yellow-400" :
                    "bg-red-500/20 text-red-400"
                  }`}>{api.method || "?"}</span>
                  <span className="text-white/70 font-mono">{api.path || api.endpoint || "-"}</span>
                  {api.description && <span className="text-white/40 ml-auto">{api.description}</span>}
                </div>
              ))}
            </div>
          </Card>
        )}

        {/* Tables */}
        {tables.length > 0 && (
          <Card className="bg-white/3 border-white/8">
            <SectionHeading label={`DB 테이블 (${tables.length})`} />
            <div className="flex flex-wrap gap-2">
              {tables.map((t, i) => (
                <span key={i} className="px-2 py-1 rounded bg-rose-500/10 text-rose-300 text-xs border border-rose-500/20">
                  {t.table_name || t.name || String(t)}
                </span>
              ))}
            </div>
          </Card>
        )}

        {/* Components */}
        {components.length > 0 && (
          <Card className="bg-white/3 border-white/8">
            <SectionHeading label={`컴포넌트 (${components.length})`} />
            <div className="space-y-1">
              {components.map((c, i) => (
                <div key={i} className="flex items-center gap-2 text-xs py-1 border-b border-white/5">
                  <span className="text-violet-400 font-medium">{c.component_name || c.name}</span>
                  {c.domain && <span className="text-white/40">· {c.domain}</span>}
                  {c.role && <span className="text-white/30 ml-auto">{c.role}</span>}
                </div>
              ))}
            </div>
          </Card>
        )}

        {/* SA Overview 경고 */}
        {(sa.critical_gaps?.length > 0 || sa.warnings?.length > 0) && (
          <Card className="bg-white/3 border-white/8">
            <SectionHeading label="SA 검토 사항" />
            {[...(sa.critical_gaps || []), ...(sa.warnings || [])].map((w, i) => (
              <p key={i} className="text-amber-300/80 text-xs py-0.5">⚠ {typeof w === "string" ? w : JSON.stringify(w)}</p>
            ))}
          </Card>
        )}
      </div>
    </div>
  );
}

// ── 메인 SharedTab ────────────────────────────────────────────

export default function SharedTab() {
  const {
    snapshots, snapshotsLoading, loadSnapshots,
    openSnapshot, closeSnapshot, activeSnapshot,
    deleteSnapshot, currentUser,
    pullSnapshot, currentSessionId, sessions, createSession,
    addNotification, publishLoading,
  } = useAppStore((s) => ({
    snapshots: s.snapshots,
    snapshotsLoading: s.snapshotsLoading,
    loadSnapshots: s.loadSnapshots,
    openSnapshot: s.openSnapshot,
    closeSnapshot: s.closeSnapshot,
    activeSnapshot: s.activeSnapshot,
    deleteSnapshot: s.deleteSnapshot,
    currentUser: s.currentUser,
    pullSnapshot: s.pullSnapshot,
    currentSessionId: s.currentSessionId,
    sessions: s.sessions,
    createSession: s.createSession,
    addNotification: s.addNotification,
    publishLoading: s.publishLoading,
  }));

  const [showPublishModal, setShowPublishModal] = useState(false);

  useEffect(() => { loadSnapshots(); }, []);

  const handleDelete = async (id) => {
    if (!window.confirm("이 스냅샷을 삭제하시겠습니까?")) return;
    await deleteSnapshot(id);
  };

  const handlePull = async (snap) => {
    let targetRunId = null;
    const activeSession = sessions.find(s => s.id === currentSessionId);
    
    if (activeSession) {
      if (!window.confirm(`이 스냅샷 "${snap.title}"을 현재 활성화된 세션("${activeSession.name}")으로 Pull하시겠습니까?\n현재 로컬 분석 결과가 이 스냅샷 내용으로 덮어씌워집니다.`)) {
        return;
      }
      targetRunId = activeSession.resultData?.run_id
               || activeSession.resultData?.metadata?.run_id;
    } else {
      if (!window.confirm(`현재 활성화된 세션이 없습니다.\n새 프로젝트 세션을 생성하여 스냅샷 "${snap.title}"을 Pull하시겠습니까?`)) {
        return;
      }
      const generatedTitle = `Pulled v${snap.version} — ${snap.title}`;
      createSession(generatedTitle);
      
      const nextStore = useAppStore.getState();
      const newSession = nextStore.sessions[0];
      
      const generateRunId = () => {
        const d = new Date();
        const yyyy = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, "0");
        const dd = String(d.getDate()).padStart(2, "0");
        const hh = String(d.getHours()).padStart(2, "0");
        const min = String(d.getMinutes()).padStart(2, "0");
        const ss = String(d.getSeconds()).padStart(2, "0");
        return `${yyyy}${mm}${dd}_${hh}${min}${ss}`;
      };
      targetRunId = generateRunId();
      
      useAppStore.setState(state => ({
        sessions: state.sessions.map(s => s.id === newSession.id ? {
          ...s,
          resultData: { run_id: targetRunId }
        } : s)
      }));
    }

    if (!targetRunId) {
      const generateRunId = () => {
        const d = new Date();
        const yyyy = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, "0");
        const dd = String(d.getDate()).padStart(2, "0");
        const hh = String(d.getHours()).padStart(2, "0");
        const min = String(d.getMinutes()).padStart(2, "0");
        const ss = String(d.getSeconds()).padStart(2, "0");
        return `${yyyy}${mm}${dd}_${hh}${min}${ss}`;
      };
      targetRunId = generateRunId();
      
      useAppStore.setState(state => ({
        sessions: state.sessions.map(s => s.id === currentSessionId ? {
          ...s,
          resultData: { ...(s.resultData || {}), run_id: targetRunId }
        } : s)
      }));
    }

    const res = await pullSnapshot(snap.id, targetRunId);
    if (res.success) {
      if (addNotification) {
        addNotification("스냅샷을 성공적으로 Pull하여 로컬 저장소에 반영했습니다.", "success");
      } else {
        alert("스냅샷을 성공적으로 Pull했습니다.");
      }
    } else {
      if (addNotification) {
        addNotification(`Pull 실패: ${res.error || "알 수 없는 오류"}`, "error");
      } else {
        alert(`Pull 실패: ${res.error || "알 수 없는 오류"}`);
      }
    }
  };

  const canDelete = (snap) =>
    currentUser && (currentUser.id === snap.published_by || currentUser.role === "pm");

  if (activeSnapshot) {
    return (
      <SnapshotDetail
        snapshot={activeSnapshot}
        onBack={closeSnapshot}
        onDelete={handleDelete}
        canDelete={canDelete(activeSnapshot)}
        onPull={handlePull}
        isPulling={publishLoading}
      />
    );
  }

  return (
    <div className="h-full flex flex-col">
      {/* 헤더 */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-white/10">
        <div>
          <h2 className="text-white font-semibold text-base">공유 스냅샷</h2>
          <p className="text-xs text-white/40 mt-0.5">팀에 Publish된 분석 결과 목록</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={loadSnapshots}
            disabled={snapshotsLoading}
            className="px-3 py-1.5 text-xs text-white/40 hover:text-white border border-white/10 hover:border-white/20 rounded-lg transition-colors"
          >
            {snapshotsLoading ? "로딩..." : "새로고침"}
          </button>
          <button
            onClick={() => setShowPublishModal(true)}
            className="px-3 py-1.5 text-xs bg-purple-600 hover:bg-purple-500 text-white rounded-lg transition-colors flex items-center gap-1.5"
          >
            <Upload size={13} />
            Publish
          </button>
        </div>
      </div>

      {/* 목록 */}
      <div className="flex-1 overflow-y-auto px-6 py-4 custom-scrollbar">
        {snapshotsLoading && snapshots.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-white/30">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-500 mb-3" />
            <p className="text-sm">스냅샷 로딩 중...</p>
          </div>
        ) : snapshots.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-white/30 gap-3">
            <Upload size={40} className="opacity-30" />
            <p className="text-sm">아직 공유된 스냅샷이 없습니다.</p>
            <p className="text-xs opacity-70">분석 완료 후 Publish 버튼을 눌러 팀과 공유하세요.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {snapshots.map((snap) => (
              <div
                key={snap.id}
                className="group bg-white/3 hover:bg-white/6 border border-white/8 hover:border-white/15 rounded-xl px-5 py-4 transition-all cursor-pointer"
                onClick={() => openSnapshot(snap.id)}
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <Badge className="text-[10px] bg-purple-500/20 text-purple-300 border-purple-500/30">
                        v{snap.version}
                      </Badge>
                      <span className="text-white font-medium text-sm truncate">{snap.title}</span>
                    </div>
                    {snap.description && (
                      <p className="text-white/40 text-xs truncate mt-0.5">{snap.description}</p>
                    )}
                    <div className="flex items-center gap-3 mt-2 text-white/30 text-xs">
                      <span className="flex items-center gap-1">
                        <Clock size={11} />
                        {new Date(snap.published_at).toLocaleString("ko-KR")}
                      </span>
                      {snap.published_by && (
                        <span className="flex items-center gap-1">
                          <User size={11} />
                          {snap.published_by.slice(0, 8)}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={(e) => { e.stopPropagation(); handlePull(snap); }}
                      className="p-1.5 text-purple-400/70 hover:text-purple-400 hover:bg-purple-500/10 rounded-lg transition-colors"
                      title="로컬로 Pull"
                    >
                      <Download size={14} />
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); openSnapshot(snap.id); }}
                      className="p-1.5 text-white/40 hover:text-white hover:bg-white/10 rounded-lg transition-colors"
                    >
                      <Eye size={14} />
                    </button>
                    {canDelete(snap) && (
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDelete(snap.id); }}
                        className="p-1.5 text-red-400/50 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showPublishModal && <PublishModal onClose={() => setShowPublishModal(false)} />}
    </div>
  );
}
