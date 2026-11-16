import React, { useEffect, useState } from "react";
import { platform } from "./platform/index.js";
import useAppStore from "./store/useAppStore";
import GlobalErrorBoundary from "./components/GlobalErrorBoundary";
import ResultViewer from "./components/ResultViewer";
import HomeScreen from "./components/HomeScreen";
import PipelineProgress from "./components/PipelineProgress";
import StatusBar from "./components/StatusBar";
import SessionPanel from "./components/SessionPanel";
import SettingsPanel from "./components/SettingsPanel";
import LoginScreen from "./components/auth/LoginScreen";
import TeamCreateScreen from "./components/auth/TeamCreateScreen";
import TeamChatPage from "./components/chat/TeamChatPage";
import PricingScreen from "./components/billing/PricingScreen";

import TopBar from "./components/layout/TopBar";
import StudioCard from "./components/layout/StudioCard";
import StudioSidebar from "./components/layout/StudioSidebar";
import PanelWrapper from "./components/layout/PanelWrapper";
import WorkspaceSwitcher from "./components/layout/WorkspaceSwitcher";
import LeftSidebar from "./components/layout/LeftSidebar";
import ToastContainer from "./components/ui/ToastContainer";
import { ICON_PANELS } from "./constants/uiConstants";

import {
  X, PanelRightClose, PanelRightOpen
} from "lucide-react";

export default function App() {
  const isDarkMode = useAppStore((state) => state.isDarkMode);
  const setBackendPort = useAppStore((state) => state.setBackendPort);
  const connectWebSocket = useAppStore((state) => state.connectWebSocket);
  const fetchConfig = useAppStore((state) => state.fetchConfig);
  const activeViewportTab = useAppStore((state) => state.activeViewportTab);
  const activateOutputTab = useAppStore((state) => state.activateOutputTab);
  const pipelineStatus = useAppStore((state) => state.pipelineStatus);
  const pipelineType = useAppStore((state) => state.pipelineType);
  const thinkingLog = useAppStore((state) => state.thinkingLog);
  const resultData = useAppStore((state) => state.resultData);
  const sa_artifacts = useAppStore((state) => state.sa_artifacts);

  const authToken = useAppStore((state) => state.authToken);
  const authChecked = useAppStore((state) => state.authChecked);
  const hasUsers = useAppStore((state) => state.hasUsers);
  const currentUser = useAppStore((state) => state.currentUser);
  const userPlan = useAppStore((state) => state.userPlan);
  const checkAuthStatus = useAppStore((state) => state.checkAuthStatus);
  const activeIconPanel = useAppStore((state) => state.activeIconPanel);
  const setActiveIconPanel = useAppStore((state) => state.setActiveIconPanel);
  const showOnboardingBridge = useAppStore((state) => state.showOnboardingBridge);
  const currentSessionId = useAppStore((state) => state.currentSessionId);
  const chatHistory = useAppStore((state) => state.chatHistory);

  const [showSessions, setShowSessions] = useState(false);
  const [showGithubModal, setShowGithubModal] = useState(false);
  const [showPricingModal, setShowPricingModal] = useState(false);
  const [pricingDismissed, setPricingDismissed] = useState(
    () => localStorage.getItem("pricing_dismissed") === "1"
  );
  const [isStudioOpen, setIsStudioOpen] = useState(true);

  const activeOutputId = activeViewportTab?.kind === "output" ? activeViewportTab.id : null;

  const SA_PIPELINE_TYPES = ["analysis_create", "analysis_reverse", "analysis_update"];
  const hasSaData = Boolean(sa_artifacts || resultData?.sa_output) ||
    (SA_PIPELINE_TYPES.includes(pipelineType) && pipelineStatus !== "idle");
  const hasProgress = pipelineStatus === "running" || pipelineStatus === "error" || thinkingLog.length > 0;

  useEffect(() => {
    // Cloud Run 인증 확인은 로컬 백엔드와 무관하므로 즉시 실행
    checkAuthStatus();

    async function initBackend() {
      const port = await platform.getBackendPort();
      setBackendPort(port);
      connectWebSocket(port);
      fetchConfig(port);
    }
    initBackend();
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => platform.setTitleBarTheme(isDarkMode), 100);
    return () => clearTimeout(timer);
  }, [isDarkMode]);

  // 분석이 시작되면 메모/세션/설정 등 활성 패널을 강제로 닫고 메인 viewport(Progress)를 노출.
  // store의 activateOutputTab만으로는 App의 로컬 state(activeIconPanel)를 끄지 못하므로
  // 여기서 보강. (Memos 탭 → "지적사항 반영 업데이트" 클릭 시 progress로 자동 전환되도록.)
  useEffect(() => {
    if (pipelineStatus === "running") {
      setActiveIconPanel(null);
      setShowSessions(false);
      setShowGithubModal(false);
    }
  }, [pipelineStatus]);

  const handleIconPanel = (id) => {
    setActiveIconPanel(activeIconPanel === id ? null : id);
    setShowSessions(false);
    setShowGithubModal(false);
  };

  const renderCenter = () => {
    if (activeIconPanel === "team_chat") {
      return <TeamChatPage onClose={() => setActiveIconPanel(null)} />;
    }
    if (activeIconPanel) {
      const panel = ICON_PANELS.find(p => p.id === activeIconPanel);
      if (!panel) {
        setTimeout(() => setActiveIconPanel(null), 0);
        return <HomeScreen />;
      }
      return (
        <PanelWrapper title={panel.label || ""} onClose={() => setActiveIconPanel(null)}>
          <ResultViewer tabId={activeIconPanel} />
        </PanelWrapper>
      );
    }

    return <HomeScreen />;
  };

  // authChecked=false → 로딩 스피너
  if (!authChecked) {
    return (
      <div className="h-screen w-screen flex items-center justify-center"
        style={{ background: "var(--bg-root)" }}>
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
          <span className="text-sm font-medium text-slate-400">초기화 중...</span>
        </div>
      </div>
    );
  }

  // authToken=false → LoginScreen
  if (!authToken) {
    return <LoginScreen isFirstRun={hasUsers === false} />;
  }

  // isTeam=false → TeamCreateScreen
  if (!currentUser?.team_id) {
    return <TeamCreateScreen />;
  }

  // isPaid=true → PricingScreen (유료 플랜 첫 로그인 안내, 또는 스킵)
  const isPaid = userPlan && userPlan !== "free";
  if (isPaid && !pricingDismissed) {
    return (
      <PricingScreen
        onContinue={() => {
          localStorage.setItem("pricing_dismissed", "1");
          setPricingDismissed(true);
        }}
      />
    );
  }

  return (
    <div className={`h-screen w-screen flex flex-col overflow-hidden ${!isDarkMode ? "light" : ""}`}
      style={{ background: "var(--bg-root)", color: "var(--text-primary)" }}>

      <TopBar
        activeOutputId={activeOutputId}
        activateOutputTab={activateOutputTab}
        hasProgress={hasProgress}
        hasSaData={hasSaData}
        resultData={resultData}
        showSessions={showSessions}
        setShowSessions={(v) => { setShowSessions(v); }}
      />

      <div className="flex-1 min-h-0 overflow-hidden flex app-no-drag">
        {/* Slack-style Workspace Switcher */}
        <WorkspaceSwitcher
          showSessions={showSessions}
          setShowSessions={setShowSessions}
          isTeamChatOpen={activeIconPanel === "team_chat"}
          onOpenTeamChat={() => handleIconPanel("team_chat")}
          onOpenGithub={() => { setShowGithubModal(true); setShowSessions(false); }}
          onOpenPricing={() => { setShowPricingModal(true); }}
        />

        {showSessions && <LeftSidebar />}

        <div className="flex-1 flex flex-col bg-transparent min-h-0 overflow-hidden relative">
          <div className="flex-1 min-h-0 overflow-hidden">
            <GlobalErrorBoundary>
              {renderCenter()}
            </GlobalErrorBoundary>
          </div>
        </div>

        <div
          className={`relative h-full flex flex-col border-l border-[var(--border)] overflow-hidden transition-all duration-250 ease-out ${isStudioOpen ? "w-[240px]" : "w-[72px]"
            } ${isDarkMode ? "bg-[#0F1219]" : "bg-transparent"}`}
        >
          <div className={`h-14 flex items-center justify-center shrink-0 border-b ${isDarkMode ? "border-white/5" : "border-[var(--border)]"
            } ${isStudioOpen ? "px-6 !justify-between" : "w-full"}`}>
            {isStudioOpen ? (
              <div className="flex flex-col">
                <span className="text-[10px] font-black text-blue-400 uppercase tracking-[0.3em]">Workbench</span>
                <h3 className={`text-[15px] font-black tracking-tight ${isDarkMode ? "text-gradient" : "text-blue-600"}`}>STUDIO</h3>
              </div>
            ) : null}
            <button
              onClick={() => setIsStudioOpen((prev) => !prev)}
              className={`transition-all flex items-center justify-center rounded-xl ${isStudioOpen ? "w-10 h-10" : "w-full h-14"
                } ${isDarkMode ? "hover:bg-white/5 text-slate-400 hover:text-white" : "hover:bg-black/5 text-slate-600"
                }`}
            >
              {isStudioOpen ? (
                <PanelRightClose size={20} />
              ) : (
                <PanelRightOpen size={20} />
              )}
            </button>
          </div>

          <div className="flex-1 flex flex-col overflow-hidden relative">
            {isStudioOpen ? (
              <div className="flex-1 overflow-y-auto custom-scrollbar pb-24">
                <StudioSidebar
                  panels={ICON_PANELS}
                  activeIconPanel={activeIconPanel}
                  isDarkMode={isDarkMode}
                  hasProgress={hasProgress}
                  pipelineStatus={pipelineStatus}
                  onPanel={handleIconPanel}
                  onOpenProgress={() => { setActiveIconPanel(null); setShowSessions(false); setShowGithubModal(false); }}
                />
              </div>
            ) : (
              <div className="flex-1 flex flex-col items-center py-3 gap-2 w-full overflow-y-auto custom-scrollbar pb-24">
                {ICON_PANELS.map((panel) => {
                  const isActive = activeIconPanel === panel.id;
                  return (
                    <button
                      key={panel.id}
                      onClick={() => handleIconPanel(panel.id)}
                      title={panel.label}
                      className={`w-10 h-10 flex items-center justify-center rounded-xl transition-all ${
                        isActive ? "bg-[var(--accent)]/20 text-[var(--accent)]" : "text-slate-500 hover:text-white hover:bg-white/10"
                      }`}
                    >
                      <panel.Icon size={17} />
                    </button>
                  );
                })}
              </div>
            )}

          </div>
        </div>
      </div>

      <div className="app-no-drag shrink-0">
        <StatusBar />
      </div>

      {/* GitHub 연동 모달 */}
      {showGithubModal && (
        <div
          className="fixed inset-0 z-[8000] flex items-center justify-center animate-fade-in"
          style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(12px)" }}
          onClick={(e) => { if (e.target === e.currentTarget) setShowGithubModal(false); }}
        >
          <div className={`relative w-[500px] max-h-[80vh] rounded-2xl shadow-[0_32px_80px_rgba(0,0,0,0.5)] flex flex-col overflow-hidden border ${
            isDarkMode ? "bg-[#0f1219] border-white/[0.08]" : "bg-white border-slate-200"
          }`}>
            <div className={`flex items-center justify-between px-6 py-4 border-b shrink-0 ${isDarkMode ? "border-white/5" : "border-slate-100"}`}>
              <div className="flex items-center gap-2.5">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" className={isDarkMode ? "text-slate-300" : "text-slate-700"}>
                  <path d="M12 0C5.37 0 0 5.37 0 12c0 5.3 3.44 9.8 8.2 11.38.6.11.82-.26.82-.58v-2.03c-3.34.72-4.04-1.61-4.04-1.61-.55-1.39-1.34-1.76-1.34-1.76-1.08-.74.08-.73.08-.73 1.2.08 1.83 1.23 1.83 1.23 1.07 1.83 2.8 1.3 3.49 1 .11-.78.42-1.3.76-1.6-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.12-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 3-.4c1.02 0 2.04.14 3 .4 2.28-1.55 3.29-1.23 3.29-1.23.66 1.66.24 2.88.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.61-2.81 5.63-5.48 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.83.58C20.57 21.8 24 17.3 24 12c0-6.63-5.37-12-12-12z"/>
                </svg>
                <h2 className={`text-[14px] font-black ${isDarkMode ? "text-white" : "text-slate-900"}`}>GitHub 연동</h2>
              </div>
              <button
                onClick={() => setShowGithubModal(false)}
                className={`w-7 h-7 rounded-lg flex items-center justify-center transition-colors ${
                  isDarkMode ? "hover:bg-white/10 text-slate-500 hover:text-slate-300" : "hover:bg-slate-100 text-slate-400"
                }`}
              ><X size={13} /></button>
            </div>
            <div className="flex-1 overflow-y-auto custom-scrollbar">
              <SettingsPanel onShowPricing={() => { setShowGithubModal(false); setShowPricingModal(true); }} />
            </div>
          </div>
        </div>
      )}

      {/* 플랜 오버레이 모달 */}
      {showPricingModal && (
        <div
          className="fixed inset-0 z-[8500] flex items-center justify-center animate-fade-in"
          style={{ background: "rgba(0,0,0,0.65)", backdropFilter: "blur(12px)" }}
          onClick={(e) => { if (e.target === e.currentTarget) setShowPricingModal(false); }}
        >
          <div className={`relative w-[700px] max-h-[85vh] rounded-2xl shadow-[0_32px_80px_rgba(0,0,0,0.5)] flex flex-col overflow-hidden border ${
            isDarkMode ? "bg-[#0f1219] border-white/[0.08]" : "bg-white border-slate-200"
          }`}>
            <div className={`flex items-center justify-between px-6 py-4 border-b shrink-0 ${isDarkMode ? "border-white/5" : "border-slate-100"}`}>
              <h2 className={`text-[15px] font-black ${isDarkMode ? "text-white" : "text-slate-900"}`}>플랜</h2>
              <button
                onClick={() => setShowPricingModal(false)}
                className={`w-7 h-7 rounded-lg flex items-center justify-center transition-colors ${
                  isDarkMode ? "hover:bg-white/10 text-slate-500 hover:text-slate-300" : "hover:bg-slate-100 text-slate-400"
                }`}
              ><X size={13} /></button>
            </div>
            <div className="flex-1 overflow-y-auto">
              <PricingScreen
                onContinue={() => setShowPricingModal(false)}
                onClose={() => setShowPricingModal(false)}
              />
            </div>
          </div>
        </div>
      )}

      <ToastContainer />

    </div>
  );
}
