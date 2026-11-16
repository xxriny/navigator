import React, { useState, useEffect, useMemo, useRef } from "react";
import useAppStore from "../store/useAppStore";
import { apiBaseUrl } from "../api/apiClient";
import {
  Send, Loader2, Rocket, Sparkles, ScanSearch,
  Github as GithubIcon, FileText, X, Plus, GitBranch,
  FolderGit2, Search, ArrowUp, BookOpen, CircleDot, ChevronDown,
} from "lucide-react";
import ChatThread from "./chat/ChatThread";
import SyncConfirmModal from "./SyncConfirmModal";
import { serverRequest } from "../api/serverClient";

const MODE_META = {
  create:  { label: "신규 분석", Icon: Sparkles,   placeholder: "구체화하고 싶은 아이디어를 입력하세요..." },
  reverse: { label: "재분석",   Icon: ScanSearch, placeholder: "분석할 레포지토리를 선택하거나 컨텍스트를 입력하세요..." },
};

export default function HomeScreen() {
  const runSyncUpdate = useAppStore((s) => s.runSyncUpdate);
  const sendIdeaChat = useAppStore((s) => s.sendIdeaChat);
  const addChatMessage = useAppStore((s) => s.addChatMessage);
  const createSession = useAppStore((s) => s.createSession);
  const currentSessionId = useAppStore((s) => s.currentSessionId);
  const apiKey = useAppStore((s) => s.apiKey);
  const model = useAppStore((s) => s.model);
  const selectedMode = useAppStore((s) => s.selectedMode);
  const setSelectedMode = useAppStore((s) => s.setSelectedMode);
  const projectFolder = useAppStore((s) => s.projectFolder);
  const isDarkMode = useAppStore((s) => s.isDarkMode);
  const chatHistory = useAppStore((s) => s.chatHistory);
  const chatInput = useAppStore((s) => s.chatInput);
  const setChatInput = useAppStore((s) => s.setChatInput);
  const pipelineStatus = useAppStore((s) => s.pipelineStatus);
  const pipelineType = useAppStore((s) => s.pipelineType);
  const userComments = useAppStore((s) => s.userComments);
  const lastIdeaReady = useAppStore((s) => s.lastIdeaReady);
  const lastIdeaSummary = useAppStore((s) => s.lastIdeaSummary);
  const currentUser = useAppStore((s) => s.currentUser);
  const addNotification = useAppStore((s) => s.addNotification);
  const resultData = useAppStore((s) => s.resultData);
  const designSnapshotCounter = useAppStore((s) => s.designSnapshotCounter);
  const setGithubSettings = useAppStore((s) => s.setGithubSettings);
  const githubToken = useAppStore((s) => s.githubToken);
  const githubOwner = useAppStore((s) => s.githubOwner);
  const githubRepo = useAppStore((s) => s.githubRepo);
  const backendPort = useAppStore((s) => s.backendPort);
  const authToken = useAppStore((s) => s.authToken);

  const [projectTitle] = useState("새 프로젝트");
  const [syncModalOpen, setSyncModalOpen] = useState(false);
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [exportLoading, setExportLoading] = useState(false);
  const textareaRef = useRef(null);
  const plusMenuRef = useRef(null);
  const exportMenuRef = useRef(null);

  const hasStarted = (chatHistory || []).some(
    (m) => m && (m.role === "user" || m.role === "assistant")
  );

  const isPm = currentUser?.role === "pm";
  const isReverseMode = selectedMode === "reverse";
  const trimmedInput = (chatInput || "").trim();
  const isProcessing = pipelineStatus === "running";
  const activeMemoCount = (userComments || []).filter((m) => !m.applied).length;
  const modeMeta = MODE_META[selectedMode] || MODE_META.create;

  const hasSyncedAtLeastOnce = (chatHistory || []).some(
    (m) => m && m.role === "system_marker" && m.kind === "sync"
  );
  const showExportBanner = selectedMode === "create" && hasSyncedAtLeastOnce && hasStarted;

  const handleGithubExport = async (mode) => {
    setExportMenuOpen(false);
    if (!resultData) {
      addNotification?.("분석 결과가 없습니다. 먼저 분석을 실행하세요.", "error", 4000);
      return;
    }
    if (!githubOwner || !githubRepo) {
      addNotification?.("GitHub 레포지토리가 연결되어 있지 않습니다. 설정에서 연결해 주세요.", "error", 4000);
      return;
    }
    setExportLoading(true);
    const authHeader = authToken ? { Authorization: `Bearer ${authToken}` } : {};
    const port = backendPort || 8000;
    try {
      let ghToken = "";
      try {
        const td = await fetch(`${apiBaseUrl(port)}/auth/github/token`, { headers: authHeader });
        const tdJson = await td.json();
        ghToken = tdJson.github_oauth_token || "";
      } catch (_) {}

      const res = await fetch(`${apiBaseUrl(port)}/api/github/publish`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeader },
        body: JSON.stringify({
          owner: githubOwner,
          repo: githubRepo,
          result_data: resultData,
          page_title: "SA 설계 문서",
          project_name: resultData?.pm_bundle?.project_name || "Project",
          publish_mode: mode,
          api_key: apiKey || "",
          model: model || "gemini-2.0-flash-lite",
          token: ghToken,
        }),
      });
      const json = await res.json();
      if (json.status === "ok") {
        const label = mode === "wiki" ? "Wiki" : "Issues";
        addNotification?.(`GitHub ${label}에 성공적으로 내보냈습니다.`, "success", 5000);
      } else {
        addNotification?.(`내보내기 실패: ${json.error || "알 수 없는 오류"}`, "error", 6000);
      }
    } catch (e) {
      addNotification?.(`내보내기 오류: ${e.message}`, "error", 5000);
    } finally {
      setExportLoading(false);
    }
  };

  // exportMenu 외부 클릭 닫기
  useEffect(() => {
    if (!exportMenuOpen) return;
    const handler = (e) => {
      if (exportMenuRef.current && !exportMenuRef.current.contains(e.target)) {
        setExportMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [exportMenuOpen]);

  const canSendInitial = isPm && !!trimmedInput && (!isReverseMode || !!projectFolder);
  const canSendFollowUp = isPm && !!trimmedInput && !isProcessing;
  const canSend = hasStarted ? canSendFollowUp : canSendInitial;

  const handleSend = () => {
    if (!canSend) return;
    const text = trimmedInput;
    if (!hasStarted && !currentSessionId) {
      createSession(projectTitle);
    }
    addChatMessage("user", text);
    setChatInput("");
    sendIdeaChat(text, apiKey, model);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const syncPreview = useMemo(() => {
    const isFirstBuild = !resultData;
    const lastSyncIdx = (() => {
      for (let i = (chatHistory || []).length - 1; i >= 0; i--) {
        const m = chatHistory[i];
        if (m && m.role === "system_marker" && m.kind === "sync") return i;
      }
      return -1;
    })();
    const chatDiff = (chatHistory || [])
      .slice(lastSyncIdx + 1)
      .filter((m) => m && (m.role === "user" || m.role === "assistant"));
    const activeMemos = (userComments || []).filter((c) => !c.applied);
    const targetVersion = isFirstBuild
      ? "v1.0"
      : `v1.${(designSnapshotCounter || 0) + 1}`;
    return { isFirstBuild, chatDiff, activeMemos, targetVersion };
  }, [resultData, chatHistory, userComments, designSnapshotCounter]);

  const handleSync = () => {
    if (isProcessing) return;
    setSyncModalOpen(true);
  };

  const handleSyncConfirm = (finalIdea) => {
    setSyncModalOpen(false);
    runSyncUpdate({ ideaOverride: finalIdea || "" });
  };

  const handleSyncCancel = () => setSyncModalOpen(false);

  const handleAttach = (payload) => {
    setPlusMenuOpen(false);
    if (payload.type === "repo") {
      setGithubSettings(githubToken, payload.owner, payload.repo, payload.branch);
      if (!currentSessionId) createSession(projectTitle);
      addChatMessage("user", `레포지토리 분석 요청: ${payload.owner}/${payload.repo} · ${payload.branch}`);
      runSyncUpdate({ ideaOverride: `[REVERSE_ENGINEER]\n레포: ${payload.owner}/${payload.repo}\n브랜치: ${payload.branch}` });
    } else {
      if (!currentSessionId) createSession(projectTitle);
      addChatMessage("user", `문서 분석 요청: ${payload.name}`);
      runSyncUpdate({ ideaOverride: `[문서 기반 분석]\n파일명: ${payload.name}\n\n${payload.text}` });
    }
  };

  // Close plus menu on outside click
  useEffect(() => {
    if (!plusMenuOpen) return;
    const handler = (e) => {
      if (plusMenuRef.current && !plusMenuRef.current.contains(e.target)) setPlusMenuOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [plusMenuOpen]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height =
        Math.min(textareaRef.current.scrollHeight, 150) + "px";
    }
  }, [chatInput]);

  return (
    <div
      className={`h-full w-full flex flex-col relative ${
        isDarkMode ? "bg-transparent text-slate-200" : "bg-transparent text-slate-800"
      }`}
    >
      {/* 배경 글로우 */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-[10%] -left-[5%] w-[50%] h-[50%] bg-blue-500/10 blur-[120px] rounded-full" />
      </div>

      {hasStarted ? (
        /* ── Conversation 모드 ── */
        <div className="flex-1 min-h-0 flex flex-col relative z-10 animate-fade-in">
          {showExportBanner && (
            <div className="px-6 pt-3 shrink-0">
              <div className={`max-w-5xl mx-auto flex items-center gap-3 px-4 py-2.5 rounded-2xl border ${
                isDarkMode
                  ? "bg-gradient-to-r from-emerald-500/10 to-blue-500/10 border-emerald-500/20"
                  : "bg-gradient-to-r from-emerald-50 to-blue-50 border-emerald-200"
              }`}>
                <GithubIcon size={16} className={isDarkMode ? "text-emerald-300" : "text-emerald-700"} />
                <span className={`text-[12px] font-medium flex-1 ${isDarkMode ? "text-slate-200" : "text-slate-700"}`}>
                  설계서가 픽스되었습니다. 빈 GitHub 레포에 첫 커밋으로 내보낼 수 있습니다.
                </span>
                <div className="relative" ref={exportMenuRef}>
                  <button
                    onClick={() => setExportMenuOpen((v) => !v)}
                    disabled={exportLoading}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-bold bg-gradient-to-r from-emerald-600 to-blue-600 hover:from-emerald-500 hover:to-blue-500 text-white shadow-md shadow-emerald-500/20 transition-all disabled:opacity-50"
                  >
                    {exportLoading
                      ? <Loader2 size={13} className="animate-spin" />
                      : <GithubIcon size={13} />}
                    GitHub에 내보내기
                    <ChevronDown size={11} className={`transition-transform ${exportMenuOpen ? "rotate-180" : ""}`} />
                  </button>

                  {exportMenuOpen && (
                    <div className="absolute right-0 top-full mt-1.5 w-52 rounded-xl border shadow-xl z-50 overflow-hidden bg-[#1c2128] border-white/10">
                      <button
                        onClick={() => handleGithubExport("wiki")}
                        className="w-full flex items-center gap-2.5 px-4 py-3 text-[12px] font-medium text-slate-200 hover:bg-white/5 transition-colors text-left"
                      >
                        <BookOpen size={14} className="text-emerald-400 shrink-0" />
                        <div>
                          <p className="font-semibold">Wiki로 내보내기</p>
                          <p className="text-[10px] text-slate-500 mt-0.5">설계서를 GitHub Wiki 페이지로</p>
                        </div>
                      </button>
                      <div className="border-t border-white/5" />
                      <button
                        onClick={() => handleGithubExport("issue")}
                        className="w-full flex items-center gap-2.5 px-4 py-3 text-[12px] font-medium text-slate-200 hover:bg-white/5 transition-colors text-left"
                      >
                        <CircleDot size={14} className="text-blue-400 shrink-0" />
                        <div>
                          <p className="font-semibold">Issues로 내보내기</p>
                          <p className="text-[10px] text-slate-500 mt-0.5">태스크를 GitHub Issues로</p>
                        </div>
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
          <ChatThread onSync={handleSync} />
        </div>
      ) : (
        /* ── Welcome 모드 (Perplexity 스타일) ── */
        <div className="flex-1 min-h-0 flex flex-col items-center justify-center px-8 relative z-10 animate-fade-in">
          {/* 타이틀 */}
          <div className="text-center mb-12">
            <h1 className="text-5xl font-black tracking-widest drop-shadow-sm">
              <span className="text-gradient">NAVIGATOR</span>
            </h1>
          </div>

          {/* 입력 카드 */}
          <div className="w-full max-w-5xl">
            {/* PM 권한 가드 */}
            {!isPm && (
              <div className="mb-3 w-full text-center py-2 px-4 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-400 text-[12px] font-medium">
                LLM 분석은 PM 권한이 필요합니다. 팀 PM에게 권한을 요청하세요.
              </div>
            )}


            {/* 입력 카드 */}
            <div className={`w-full relative rounded-3xl border transition-all ${
              isDarkMode
                ? "bg-[#1c2128]/60 border-white/[0.05] shadow-[0_12px_40px_rgba(0,0,0,0.4)] backdrop-blur-md focus-within:border-white/[0.12]"
                : "bg-white/85 border-slate-200 shadow-[0_12px_32px_rgba(0,0,0,0.06)] backdrop-blur-md focus-within:border-slate-300"
            } ${!isPm ? "opacity-50 pointer-events-none" : ""}`}>

              {/* 텍스트 영역 */}
              <div className="px-6 pt-6 pb-4">
                <textarea
                  ref={textareaRef}
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  rows={2}
                  placeholder={modeMeta.placeholder}
                  disabled={isProcessing}
                  className={`w-full bg-transparent border-none focus:outline-none focus:ring-0 text-[15px] resize-none scrollbar-hide leading-relaxed ${
                    isDarkMode ? "text-slate-100 placeholder:text-slate-600" : "text-slate-800 placeholder:text-slate-400"
                  }`}
                />
              </div>

              {/* 구분선 */}
              <div className={`mx-5 h-px ${isDarkMode ? "bg-white/[0.04]" : "bg-slate-100"}`} />

              {/* 하단 툴바 */}
              <div className="flex items-center justify-between px-3.5 py-2.5">
                {/* 왼쪽: + 버튼 + 구분점 + 모드 칩 */}
                <div className="flex items-center gap-1.5">
                  {/* + 버튼 */}
                  <div className="relative" ref={plusMenuRef}>
                    <button
                      onClick={() => setPlusMenuOpen((v) => !v)}
                      title="레포지토리 또는 문서 첨부"
                      className={`w-8 h-8 flex items-center justify-center rounded-full transition-all ${
                        plusMenuOpen
                          ? isDarkMode ? "bg-white/[0.12] text-white" : "bg-slate-200 text-slate-700"
                          : isDarkMode
                            ? "text-slate-500 hover:bg-white/[0.08] hover:text-slate-300"
                            : "text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                      }`}
                    >
                      <Plus size={16} strokeWidth={2.2} />
                    </button>

                    {plusMenuOpen && (
                      <PlusMenu
                        isDarkMode={isDarkMode}
                        onAttach={handleAttach}
                        onClose={() => setPlusMenuOpen(false)}
                      />
                    )}
                  </div>

                  {/* 모드 칩 */}
                  <ModeChip
                    selectedMode={selectedMode}
                    setSelectedMode={setSelectedMode}
                    isDarkMode={isDarkMode}
                  />
                </div>

                {/* 오른쪽: Send 원형 버튼 (Perplexity Style - ArrowUp) */}
                <button
                  onClick={handleSend}
                  disabled={!canSend}
                  className={`w-9 h-9 flex items-center justify-center rounded-full transition-all duration-300 ${
                    canSend
                      ? "bg-blue-600 text-white hover:bg-blue-500 hover:scale-105 active:scale-95 shadow-md shadow-blue-500/20"
                      : isDarkMode
                        ? "bg-white/[0.05] text-slate-500 cursor-default"
                        : "bg-slate-100 text-slate-400 cursor-default"
                  }`}
                >
                  <ArrowUp size={16} strokeWidth={2.5} />
                </button>
              </div>
            </div>

          </div>
        </div>
      )}

      {/* Conversation 모드 하단 도크 */}
      {hasStarted && (
        <div className="shrink-0 px-6 pb-8 pt-2 relative z-20">
          <div className="max-w-5xl mx-auto">
            {!isPm && (
              <div className="mb-3 w-full text-center py-2 px-4 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-400 text-[12px] font-medium">
                LLM 분석은 PM 권한이 필요합니다. 팀 PM에게 권한을 요청하세요.
              </div>
            )}

            {isProcessing && (
              <div className={`mb-3 px-4 py-2.5 rounded-2xl flex items-center gap-3 border animate-fade-in ${
                isDarkMode
                  ? "bg-blue-500/10 border-blue-500/30 text-blue-300"
                  : "bg-blue-50 border-blue-200 text-blue-700"
              }`}>
                <Loader2 size={14} className="shrink-0 animate-spin text-blue-500" />
                <span className="text-[12px] font-medium">
                  {pipelineType === "analysis_update"
                    ? "메모/대화를 반영해 설계서를 업데이트 중입니다..."
                    : "분석을 진행 중입니다..."}
                </span>
              </div>
            )}


            <div className={`w-full relative rounded-3xl border transition-all ${
              isDarkMode
                ? "bg-[#1c2128]/60 border-white/[0.05] shadow-[0_12px_40px_rgba(0,0,0,0.4)] backdrop-blur-md focus-within:border-white/[0.12]"
                : "bg-white/85 border-slate-200 shadow-[0_12px_32px_rgba(0,0,0,0.06)] backdrop-blur-md focus-within:border-slate-300"
            } ${!isPm ? "opacity-50 pointer-events-none" : ""}`}>
              {/* 텍스트 영역 */}
              <div className="px-6 pt-6 pb-4">
                <textarea
                  ref={textareaRef}
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  rows={2}
                  placeholder="메시지를 입력하세요..."
                  disabled={isProcessing}
                  className={`w-full bg-transparent border-none focus:outline-none focus:ring-0 text-[15px] resize-none scrollbar-hide leading-relaxed ${
                    isDarkMode ? "text-slate-100 placeholder:text-slate-600" : "text-slate-800 placeholder:text-slate-400"
                  }`}
                />
              </div>

              {/* 구분선 */}
              <div className={`mx-5 h-px ${isDarkMode ? "bg-white/[0.04]" : "bg-slate-100"}`} />

              {/* 하단 툴바 */}
              <div className="flex items-center justify-between px-3.5 py-2.5">
                {/* 왼쪽: + 버튼 */}
                <div className="flex items-center gap-1.5" ref={plusMenuRef}>
                  <div className="relative">
                    <button
                      onClick={() => setPlusMenuOpen((v) => !v)}
                      title="레포지토리 또는 문서 첨부"
                      className={`w-8 h-8 flex items-center justify-center rounded-full transition-all ${
                        plusMenuOpen
                          ? isDarkMode ? "bg-white/[0.12] text-white" : "bg-slate-200 text-slate-700"
                          : isDarkMode
                            ? "text-slate-500 hover:bg-white/[0.08] hover:text-slate-300"
                            : "text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                      }`}
                    >
                      <Plus size={16} strokeWidth={2.2} />
                    </button>
                    {plusMenuOpen && (
                      <PlusMenu
                        isDarkMode={isDarkMode}
                        onAttach={handleAttach}
                        onClose={() => setPlusMenuOpen(false)}
                      />
                    )}
                  </div>
                </div>

                {/* 오른쪽: Sync + Send (Perplexity Style) */}
                <div className="flex items-center gap-2">
                  <SyncButton
                    onClick={handleSync}
                    glow={lastIdeaReady}
                    activeMemoCount={activeMemoCount}
                    isProcessing={isProcessing}
                    isDarkMode={isDarkMode}
                    tooltip={lastIdeaSummary}
                  />
                  <button
                    onClick={handleSend}
                    disabled={!canSend}
                    className={`w-9 h-9 flex items-center justify-center rounded-full transition-all duration-300 ${
                      canSend
                        ? "bg-blue-600 text-white hover:bg-blue-500 hover:scale-105 active:scale-95 shadow-md shadow-blue-500/20"
                        : isDarkMode
                          ? "bg-white/[0.05] text-slate-500 cursor-default"
                          : "bg-slate-100 text-slate-400 cursor-default"
                    }`}
                  >
                    <ArrowUp size={16} strokeWidth={2.5} />
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      <SyncConfirmModal
        open={syncModalOpen}
        isDarkMode={isDarkMode}
        isFirstBuild={syncPreview.isFirstBuild}
        targetVersion={syncPreview.targetVersion}
        chatDiff={syncPreview.chatDiff}
        activeMemos={syncPreview.activeMemos}
        onCancel={handleSyncCancel}
        onConfirm={handleSyncConfirm}
      />
    </div>
  );
}

/* ── + 메뉴 (GitHub 레포 피커 + 문서 업로드) ── */
function PlusMenu({ isDarkMode, onAttach, onClose }) {
  const [view, setView] = useState("main"); // "main" | "repo"
  const backendPort = useAppStore.getState().backendPort;

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    onClose();
    e.target.value = "";
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${apiBaseUrl(backendPort)}/api/upload-doc`, {
        method: "POST",
        headers: useAppStore.getState().getAuthHeader(),
        body: form,
      });
      const data = await res.json();
      if (data.status === "ok") {
        onAttach({ type: "file", name: file.name, text: data.text });
      } else {
        alert("파일 파싱 실패: " + (data.error || "알 수 없는 오류"));
      }
    } catch (err) {
      alert("파일 업로드 오류: " + err.message);
    }
  };

  return (
    <div className={`absolute top-[40px] left-0 w-[240px] rounded-2xl border shadow-[0_8px_32px_rgba(0,0,0,0.4)] z-50 animate-fade-in overflow-hidden ${
      isDarkMode ? "bg-[#0e1218] border-white/[0.08]" : "bg-white border-slate-200/80"
    }`}>
      {view === "main" ? (
        <>
          <div className={`px-4 pt-3 pb-2 ${isDarkMode ? "border-b border-white/[0.05]" : "border-b border-slate-100"}`}>
            <p className={`text-[10px] font-black uppercase tracking-[0.22em] ${isDarkMode ? "text-slate-600" : "text-slate-400"}`}>
              컨텍스트 첨부
            </p>
          </div>
          <div className="p-2 space-y-0.5">
            <button
              onClick={() => setView("repo")}
              className={`w-full flex items-center gap-3 px-2.5 py-2.5 rounded-xl transition-all text-left group ${
                isDarkMode ? "hover:bg-white/[0.06]" : "hover:bg-slate-50"
              }`}
            >
              <div className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 transition-colors ${
                isDarkMode ? "bg-slate-800 group-hover:bg-blue-500/20 text-slate-400 group-hover:text-blue-400" : "bg-slate-100 group-hover:bg-blue-50 text-slate-500 group-hover:text-blue-600"
              }`}>
                <FolderGit2 size={14} />
              </div>
              <div className="flex-1 min-w-0">
                <p className={`text-[13px] font-semibold ${isDarkMode ? "text-slate-200" : "text-slate-700"}`}>레포지토리</p>
                <p className={`text-[11px] ${isDarkMode ? "text-slate-600" : "text-slate-400"}`}>GitHub repo · 브랜치 선택</p>
              </div>
            </button>
            <label className={`w-full flex items-center gap-3 px-2.5 py-2.5 rounded-xl transition-all text-left cursor-pointer group ${
              isDarkMode ? "hover:bg-white/[0.06]" : "hover:bg-slate-50"
            }`}>
              <div className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 transition-colors ${
                isDarkMode ? "bg-slate-800 group-hover:bg-violet-500/20 text-slate-400 group-hover:text-violet-400" : "bg-slate-100 group-hover:bg-violet-50 text-slate-500 group-hover:text-violet-600"
              }`}>
                <FileText size={14} />
              </div>
              <div className="flex-1 min-w-0">
                <p className={`text-[13px] font-semibold ${isDarkMode ? "text-slate-200" : "text-slate-700"}`}>문서 업로드</p>
                <p className={`text-[11px] ${isDarkMode ? "text-slate-600" : "text-slate-400"}`}>PDF · Word · TXT · MD</p>
              </div>
              <input type="file" accept=".pdf,.doc,.docx,.txt,.md" className="hidden" onChange={handleFileChange} />
            </label>
          </div>
        </>
      ) : (
        <RepoPicker isDarkMode={isDarkMode} onAttach={onAttach} onBack={() => setView("main")} />
      )}
    </div>
  );
}

/* ── GitHub 레포 피커 (서버 API 자동 목록) ── */
function RepoPicker({ isDarkMode, onAttach, onBack }) {
  const authToken = useAppStore((s) => s.authToken);
  const isGithubConnected = !!useAppStore((s) => s.currentUser?.github_id);

  const [repos, setRepos] = useState([]);
  const [loading, setLoading] = useState(false);
  const [scopeError, setScopeError] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedRepo, setSelectedRepo] = useState(null);
  const [branches, setBranches] = useState([]);
  const [branchLoading, setBranchLoading] = useState(false);
  const [selectedBranch, setSelectedBranch] = useState("main");

  useEffect(() => {
    if (!isGithubConnected || !authToken) return;
    setLoading(true);
    serverRequest("/auth/github/repos", {
      headers: { Authorization: `Bearer ${authToken}` },
    })
      .then((data) => {
        if (data.status === "ok") setRepos(data.repos || []);
        else setScopeError(true);
      })
      .catch(() => setScopeError(true))
      .finally(() => setLoading(false));
  }, [authToken, isGithubConnected]);

  const selectRepo = (repo) => {
    setSelectedRepo(repo);
    setBranchLoading(true);
    serverRequest(`/auth/github/branches?owner=${encodeURIComponent(repo.owner)}&repo=${encodeURIComponent(repo.name)}`, {
      headers: { Authorization: `Bearer ${authToken}` },
    })
      .then((data) => {
        if (data.status === "ok") {
          const names = (data.data || []).map((b) => b.name ?? b);
          setBranches(names);
          setSelectedBranch(names[0] || "main");
        }
      })
      .catch(() => {})
      .finally(() => setBranchLoading(false));
  };

  const filtered = repos.filter((r) =>
    (r.full_name || `${r.owner}/${r.name}`).toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleConfirm = () => {
    if (!selectedRepo) return;
    onAttach({ type: "repo", owner: selectedRepo.owner, repo: selectedRepo.name, branch: selectedBranch });
  };

  return (
    <div className="p-2">
      {/* 헤더 */}
      <div className="flex items-center gap-2 px-1.5 py-1.5 mb-1">
        <button
          onClick={onBack}
          className={`w-6 h-6 rounded-lg flex items-center justify-center text-xs transition-colors ${
            isDarkMode ? "hover:bg-white/[0.08] text-slate-500" : "hover:bg-slate-100 text-slate-400"
          }`}
        >
          ←
        </button>
        <p className={`text-[11px] font-black uppercase tracking-[0.18em] ${isDarkMode ? "text-slate-400" : "text-slate-600"}`}>
          레포지토리 선택
        </p>
      </div>

      {!isGithubConnected ? (
        <p className={`text-center py-5 text-[12px] px-3 ${isDarkMode ? "text-slate-500" : "text-slate-400"}`}>
          설정에서 GitHub 계정을 연결하세요.
        </p>
      ) : loading ? (
        <div className={`text-center py-6 text-[12px] ${isDarkMode ? "text-slate-600" : "text-slate-400"}`}>
          <Loader2 size={16} className="inline animate-spin mb-1" />
          <p>불러오는 중...</p>
        </div>
      ) : scopeError ? (
        <p className={`text-center py-5 text-[12px] px-3 ${isDarkMode ? "text-slate-500" : "text-slate-400"}`}>
          레포 목록을 가져올 수 없습니다.
        </p>
      ) : selectedRepo ? (
        /* 브랜치 선택 */
        <div className="space-y-2 px-1">
          <div className={`flex items-center gap-2 px-3 py-2 rounded-xl ${isDarkMode ? "bg-white/[0.04]" : "bg-slate-50"}`}>
            <GithubIcon size={12} className={isDarkMode ? "text-slate-500" : "text-slate-400"} />
            <span className={`text-[12px] font-semibold truncate ${isDarkMode ? "text-slate-300" : "text-slate-700"}`}>
              {selectedRepo.full_name || `${selectedRepo.owner}/${selectedRepo.name}`}
            </span>
          </div>
          <p className={`text-[10px] font-black uppercase tracking-[0.18em] px-1 ${isDarkMode ? "text-slate-600" : "text-slate-400"}`}>
            브랜치
          </p>
          {branchLoading ? (
            <div className={`text-center py-3 ${isDarkMode ? "text-slate-600" : "text-slate-400"}`}>
              <Loader2 size={13} className="inline animate-spin" />
            </div>
          ) : (
            <div className="max-h-[130px] overflow-y-auto space-y-0.5">
              {branches.map((b) => (
                <button
                  key={b}
                  onClick={() => setSelectedBranch(b)}
                  className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-xl text-left text-[12px] transition-all ${
                    selectedBranch === b
                      ? isDarkMode ? "bg-blue-500/20 text-blue-300" : "bg-blue-50 text-blue-700"
                      : isDarkMode ? "hover:bg-white/[0.05] text-slate-400" : "hover:bg-slate-50 text-slate-600"
                  }`}
                >
                  <GitBranch size={11} />
                  {b}
                </button>
              ))}
            </div>
          )}
          <button
            onClick={() => setSelectedRepo(null)}
            className={`text-[10px] px-1 ${isDarkMode ? "text-slate-600 hover:text-slate-400" : "text-slate-400 hover:text-slate-600"}`}
          >
            ← 레포 다시 선택
          </button>
        </div>
      ) : (
        /* 레포 목록 */
        <div className="space-y-1.5 px-1">
          <div className={`flex items-center gap-2 px-2.5 py-1.5 rounded-xl border ${
            isDarkMode ? "bg-white/[0.03] border-white/[0.06]" : "bg-slate-50 border-slate-200"
          }`}>
            <Search size={11} className={isDarkMode ? "text-slate-600" : "text-slate-400"} />
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="레포 검색..."
              autoFocus
              className="flex-1 bg-transparent text-[12px] outline-none placeholder:text-slate-500"
            />
          </div>
          <div className="max-h-[180px] overflow-y-auto space-y-0.5">
            {filtered.length === 0 ? (
              <p className={`text-center py-4 text-[12px] ${isDarkMode ? "text-slate-600" : "text-slate-400"}`}>
                레포지토리 없음
              </p>
            ) : (
              filtered.map((r) => (
                <button
                  key={r.full_name || r.name}
                  onClick={() => selectRepo(r)}
                  className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-xl text-left transition-all group ${
                    isDarkMode ? "hover:bg-white/[0.06]" : "hover:bg-slate-50"
                  }`}
                >
                  <GithubIcon size={12} className={isDarkMode ? "text-slate-600 group-hover:text-slate-400" : "text-slate-400 group-hover:text-slate-600"} />
                  <span className={`text-[12px] font-medium truncate ${isDarkMode ? "text-slate-400 group-hover:text-slate-200" : "text-slate-600 group-hover:text-slate-800"}`}>
                    {r.full_name || `${r.owner}/${r.name}`}
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      )}

      {/* 확인 버튼 */}
      {selectedRepo && (
        <div className={`mt-2 px-1 pt-2 ${isDarkMode ? "border-t border-white/[0.05]" : "border-t border-slate-100"}`}>
          <button
            onClick={handleConfirm}
            className={`w-full py-2 rounded-xl text-[12px] font-bold transition-all shadow active:scale-[0.98] ${
              isDarkMode ? "bg-white text-slate-900 hover:bg-slate-100" : "bg-slate-900 text-white hover:bg-slate-700"
            }`}
          >
            선택 완료
          </button>
        </div>
      )}
    </div>
  );
}


/* ── 모드 토글 (pill segmented) ── */
function ModeChip({ selectedMode, setSelectedMode, isDarkMode }) {
  return (
    <div className={`flex items-center rounded-full p-0.5 ${
      isDarkMode ? "bg-white/[0.06]" : "bg-slate-100"
    }`}>
      {Object.entries(MODE_META).map(([key, meta]) => {
        const isActive = selectedMode === key;
        return (
          <button
            key={key}
            onClick={() => setSelectedMode(key)}
            className={`flex items-center gap-1.5 h-7 px-3 rounded-full text-[12px] font-semibold transition-all select-none ${
              isActive
                ? isDarkMode
                  ? "bg-white/[0.14] text-white shadow-sm"
                  : "bg-white text-slate-800 shadow-sm"
                : isDarkMode
                  ? "text-slate-500 hover:text-slate-300"
                  : "text-slate-400 hover:text-slate-600"
            }`}
          >
            <meta.Icon size={12} strokeWidth={2} />
            {meta.label}
          </button>
        );
      })}
    </div>
  );
}

/* ── Sync 버튼 ── */
function SyncButton({ onClick, glow, activeMemoCount, isProcessing, isDarkMode, tooltip }) {
  const title = (() => {
    const lines = ["🚀 현재 대화 + 활성 메모로 설계서 업데이트"];
    if (activeMemoCount > 0) lines.push(`📋 메모 ${activeMemoCount}건 함께 반영`);
    if (tooltip) lines.push(`💡 ${tooltip}`);
    return lines.join("\n");
  })();

  return (
    <button
      onClick={onClick}
      disabled={isProcessing}
      title={title}
      className={`relative w-9 h-9 flex items-center justify-center rounded-full transition-all duration-150 ${
        glow
          ? "bg-gradient-to-br from-emerald-500 to-teal-600 text-white animate-pulse shadow-[0_0_20px_rgba(52,211,153,0.4)] ring-1 ring-emerald-400/40"
          : isDarkMode
            ? "bg-white/[0.06] hover:bg-white/[0.1] text-slate-400 hover:text-white"
            : "bg-slate-100 hover:bg-slate-200 text-slate-500 hover:text-slate-700"
      } ${isProcessing ? "opacity-40 cursor-default" : ""}`}
    >
      <Rocket size={18} />
      {activeMemoCount > 0 && (
        <span className={`absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 flex items-center justify-center rounded-full text-[10px] font-black ${
          isDarkMode ? "bg-blue-500 text-white border border-[#0F1219]" : "bg-blue-500 text-white border border-white"
        }`}>
          {activeMemoCount}
        </span>
      )}
    </button>
  );
}
