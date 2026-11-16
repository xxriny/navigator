import React, { useState } from "react";
import useAppStore from "../../store/useAppStore";
import { apiBaseUrl } from "../../api/apiClient";
import MemoManager from "./MemoManager";
import {
  GitBranch, Zap, Loader2, AlertTriangle, ChevronDown, ChevronRight,
  Package, Globe, Database, BookmarkPlus,
} from "lucide-react";

const RISK_CONFIG = {
  low:      { label: "낮음",   color: "text-emerald-400", bg: "bg-emerald-500/10", border: "border-emerald-500/30" },
  medium:   { label: "보통",   color: "text-yellow-400",  bg: "bg-yellow-500/10",  border: "border-yellow-500/30" },
  high:     { label: "높음",   color: "text-orange-400",  bg: "bg-orange-500/10",  border: "border-orange-500/30" },
  critical: { label: "치명적", color: "text-red-500",     bg: "bg-red-500/10",     border: "border-red-500/30"    },
};

const IMPACT_TYPE_LABEL = {
  modify:           "수정",
  add:              "추가",
  delete:           "삭제",
  interface_change: "인터페이스 변경",
};

export default function AgileImpactTab() {
  const isDarkMode = useAppStore((s) => s.isDarkMode);
  const resultData = useAppStore((s) => s.resultData);
  const apiKey = useAppStore((s) => s.apiKey);
  const backendHasKey = useAppStore((s) => s.backendHasKey);
  const authToken = useAppStore((s) => s.authToken);
  const currentUser = useAppStore((s) => s.currentUser);
  const userRole = useAppStore((s) => s.userRole);
  const backendPort = useAppStore((s) => s.backendPort);
  const addComment = useAppStore((s) => s.addComment);
  const storedImpactResult = useAppStore((s) => s.agileImpactResult);
  const setAgileImpactResult = useAppStore((s) => s.setAgileImpactResult);
  const port = backendPort || 8000;
  const isGithubConnected = !!currentUser?.github_id;

  const [changeDesc, setChangeDesc] = useState("");
  const [result, setResult] = useState(storedImpactResult);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expandedComps, setExpandedComps] = useState(() => new Set());
  const [savedToMemo, setSavedToMemo] = useState(false);

  const canEdit = !userRole || userRole === "pm" || userRole === "engineer";
  const saData = resultData?.sa_output?.data || resultData?.sa_output || {};

  const toggleComp = (i) =>
    setExpandedComps((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });

  const runAnalysis = async () => {
    if (!changeDesc.trim()) { setError("변경 사항 설명을 입력하세요."); return; }
    setLoading(true); setError(""); setResult(null); setSavedToMemo(false);
    try {
      const res = await fetch(`${apiBaseUrl(port)}/api/agile/impact`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${authToken || ""}` },
        body: JSON.stringify({
          change_description: changeDesc,
          sa_data: {
            components: saData.components || [],
            apis: saData.apis || [],
            tables: saData.tables || [],
          },
          api_key: apiKey || "",
          auth_token: authToken || "",
          use_llm: !!apiKey || !!authToken || backendHasKey,
        }),
      });
      const json = await res.json();
      if (json.status === "ok") {
        setResult(json.data);
        setAgileImpactResult(json.data);
      } else setError(json.error || "분석 실패");
    } catch (e) {
      setError("서버 연결 실패: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  const saveToMemo = () => {
    if (!result) return;
    const components = result.impacted_components || [];
    const summary = result.summary || changeDesc;
    // Save overall summary as one memo
    addComment({
      text: `[영향 분석] ${summary}`,
      section: "agile_impact",
      detail: components.map((c) => `• ${c.name} (${IMPACT_TYPE_LABEL[c.impact_type] || c.impact_type}): ${c.description}`).join("\n"),
    });
    // Save each impacted component as separate memo
    components.forEach((comp) => {
      addComment({
        text: `${comp.name} — ${IMPACT_TYPE_LABEL[comp.impact_type] || comp.impact_type}`,
        section: "agile_impact",
        detail: comp.description,
      });
    });
    setSavedToMemo(true);
  };

  const riskCfg = result ? (RISK_CONFIG[result.risk_level] || RISK_CONFIG.medium) : null;

  return (
    <div className={`h-full flex gap-0 overflow-hidden ${isDarkMode ? "text-slate-300" : "text-slate-800"}`}>
      {/* ── 좌측: 영향도 분석 (60%) ─────────────────────────────── */}
      <div className="w-[60%] flex flex-col p-6 space-y-5 overflow-y-auto custom-scrollbar border-r border-white/5">
        {/* Header */}
        <div>
          <h2 className={`text-2xl font-black tracking-tight ${isDarkMode ? "text-white" : "text-slate-900"}`}>
            변경 분석
          </h2>
          <p className="text-sm opacity-60 mt-1">
            설계 변경 사항이 컴포넌트·API·테이블에 미치는 영향을 RAG + LLM으로 분석합니다.
          </p>
        </div>

        {/* Input */}
        <div className={`p-4 rounded-2xl border space-y-3 ${isDarkMode ? "bg-white/5 border-white/10" : "bg-white border-slate-200 shadow-sm"}`}>
          <label className="text-xs font-bold uppercase tracking-wider opacity-60">변경 사항 설명</label>
          <textarea
            value={changeDesc}
            onChange={(e) => setChangeDesc(e.target.value)}
            disabled={!canEdit}
            rows={3}
            placeholder="예: UserService에 소셜 로그인(OAuth2) 기능을 추가하고 기존 JWT 인증과 통합"
            className={`w-full text-sm rounded-xl p-3 resize-none border outline-none transition-all ${
              isDarkMode
                ? "bg-slate-900/50 border-white/5 focus:border-blue-500/50 text-white placeholder-slate-600"
                : "bg-slate-50 border-slate-200 focus:border-blue-400 text-slate-900 placeholder-slate-400"
            } ${!canEdit ? "opacity-50 cursor-not-allowed" : ""}`}
          />
          {!canEdit && (
            <p className={`text-xs ${isDarkMode ? "text-amber-300" : "text-amber-600"}`}>
              viewer 역할은 영향 분석을 실행할 수 없습니다.
            </p>
          )}
          {!isGithubConnected && (
            <p className="text-xs text-amber-400">
              ⚠ GitHub 연결 시 LLM 기반 심층 분석이 활성화됩니다
            </p>
          )}
          <div className="flex gap-2">
            <button
              onClick={runAnalysis}
              disabled={loading || !canEdit || !changeDesc.trim()}
              className={`flex items-center gap-2 px-5 py-2.5 rounded-xl font-bold text-sm shadow-lg transition-all ${
                loading || !canEdit || !changeDesc.trim()
                  ? "bg-white/5 text-slate-500 cursor-not-allowed"
                  : "bg-blue-600 hover:bg-blue-500 text-white shadow-blue-500/20 hover:scale-[1.02] active:scale-[0.99]"
              }`}
            >
              {loading ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
              {loading ? "분석 중..." : "영향 분석 실행"}
            </button>
            {result && !savedToMemo && (
              <button
                onClick={saveToMemo}
                className="flex items-center gap-2 px-5 py-2.5 rounded-xl font-bold text-sm bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-500/20 transition-all hover:scale-[1.02]"
              >
                <BookmarkPlus size={16} /> 메모로 저장
              </button>
            )}
            {savedToMemo && (
              <span className="flex items-center gap-1.5 px-4 py-2 text-sm text-emerald-400 font-bold">
                ✓ 메모 저장됨
              </span>
            )}
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className={`p-4 rounded-xl border-l-4 border-red-500 ${isDarkMode ? "bg-red-500/10" : "bg-red-50"}`}>
            <p className="text-sm text-red-400">{error}</p>
          </div>
        )}

        {/* Results */}
        {result && (
          <div className="space-y-5 animate-fade-in">
            {/* Summary + Risk */}
            <div className={`p-5 rounded-2xl border ${isDarkMode ? "bg-white/5 border-white/10" : "bg-white border-slate-200 shadow-sm"}`}>
              <div className="flex items-center gap-3 mb-3">
                <GitBranch size={18} className="text-blue-400" />
                <span className={`font-bold ${isDarkMode ? "text-white" : "text-slate-900"}`}>분석 결과</span>
                {riskCfg && (
                  <span className={`ml-auto px-3 py-1 rounded-full text-xs font-bold ${riskCfg.bg} ${riskCfg.color} border ${riskCfg.border}`}>
                    위험도: {riskCfg.label}
                  </span>
                )}
              </div>
              <p className="text-sm opacity-80">{result.summary || result.change_description}</p>
              {result.migration_notes && (
                <div className={`mt-3 p-3 rounded-xl text-sm border-l-4 border-amber-400 ${isDarkMode ? "bg-amber-500/10" : "bg-amber-50"}`}>
                  <p className={`text-xs font-bold uppercase mb-1 ${isDarkMode ? "text-amber-300" : "text-amber-600"}`}>마이그레이션 주의사항</p>
                  <p className={isDarkMode ? "text-amber-200" : "text-amber-800"}>{result.migration_notes}</p>
                </div>
              )}
            </div>

            {/* Impacted Components */}
            {result.impacted_components?.length > 0 && (
              <div className="space-y-2">
                <h3 className="text-xs font-bold uppercase tracking-wider opacity-60 flex items-center gap-2">
                  <Package size={13} /> 영향받는 컴포넌트 ({result.impacted_components.length})
                </h3>
                {result.impacted_components.map((comp, i) => {
                  const isExp = expandedComps.has(i);
                  return (
                    <div key={i} className={`rounded-xl border ${isDarkMode ? "bg-white/5 border-white/10" : "bg-white border-slate-200 shadow-sm"}`}>
                      <button
                        type="button"
                        onClick={() => toggleComp(i)}
                        className="w-full flex items-center gap-3 p-4 text-left"
                      >
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${isDarkMode ? "bg-blue-500/20 text-blue-300" : "bg-blue-50 text-blue-700"}`}>
                          {IMPACT_TYPE_LABEL[comp.impact_type] || comp.impact_type}
                        </span>
                        <span className={`flex-1 font-semibold text-sm ${isDarkMode ? "text-slate-200" : "text-slate-800"}`}>
                          {comp.name}
                        </span>
                        {isExp ? <ChevronDown size={16} className="opacity-50" /> : <ChevronRight size={16} className="opacity-50" />}
                      </button>
                      {isExp && (
                        <div className={`px-4 pb-4 space-y-2 border-t ${isDarkMode ? "border-white/5" : "border-slate-100"} animate-fade-in`}>
                          <p className="text-sm opacity-75 pt-3">{comp.description}</p>
                          {comp.affected_apis?.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-2">
                              <Globe size={12} className="text-blue-400 mt-0.5" />
                              {comp.affected_apis.map((api, j) => (
                                <span key={j} className={`px-2 py-0.5 rounded text-[11px] font-mono ${isDarkMode ? "bg-blue-500/10 text-blue-300" : "bg-blue-50 text-blue-700"}`}>
                                  {api}
                                </span>
                              ))}
                            </div>
                          )}
                          {comp.affected_tables?.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-1">
                              <Database size={12} className="text-rose-400 mt-0.5" />
                              {comp.affected_tables.map((t, j) => (
                                <span key={j} className={`px-2 py-0.5 rounded text-[11px] font-mono ${isDarkMode ? "bg-rose-500/10 text-rose-300" : "bg-rose-50 text-rose-700"}`}>
                                  {t}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Impacted APIs & Tables */}
            <div className="grid grid-cols-2 gap-4">
              {result.impacted_apis?.length > 0 && (
                <div className={`p-4 rounded-xl border ${isDarkMode ? "bg-white/5 border-white/10" : "bg-white border-slate-200"}`}>
                  <h4 className="text-xs font-bold uppercase tracking-wider opacity-60 flex items-center gap-1 mb-2">
                    <Globe size={12} /> 영향 API
                  </h4>
                  <div className="space-y-1">
                    {result.impacted_apis.map((api, i) => (
                      <div key={i} className={`text-xs font-mono px-2 py-1 rounded ${isDarkMode ? "bg-blue-500/10 text-blue-300" : "bg-blue-50 text-blue-700"}`}>
                        {api}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {result.impacted_tables?.length > 0 && (
                <div className={`p-4 rounded-xl border ${isDarkMode ? "bg-white/5 border-white/10" : "bg-white border-slate-200"}`}>
                  <h4 className="text-xs font-bold uppercase tracking-wider opacity-60 flex items-center gap-1 mb-2">
                    <Database size={12} /> 영향 테이블
                  </h4>
                  <div className="space-y-1">
                    {result.impacted_tables.map((t, i) => (
                      <div key={i} className={`text-xs font-mono px-2 py-1 rounded ${isDarkMode ? "bg-rose-500/10 text-rose-300" : "bg-rose-50 text-rose-700"}`}>
                        {t}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Placeholder */}
        {!result && !loading && !error && (
          <div className={`flex-1 flex flex-col items-center justify-center gap-4 opacity-30 border-2 border-dashed rounded-3xl min-h-48 ${isDarkMode ? "border-white/10" : "border-slate-200"}`}>
            <GitBranch size={48} />
            <p className="font-bold">변경 사항을 입력하고 분석을 실행하세요</p>
          </div>
        )}
      </div>

      {/* ── 우측: 메모 패널 (40%) ────────────────────────────────── */}
      <div className="w-[40%] overflow-hidden">
        <MemoManager />
      </div>
    </div>
  );
}
