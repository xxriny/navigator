import React, { useEffect, useState, useRef, useCallback } from "react";
import useAppStore from "../../store/useAppStore";
import { serverRequest } from "../../api/serverClient";
import { Plus, Loader2, Check, X, Copy, LogOut, UserPlus, Users, Edit3, Moon, Sun, CreditCard, Library, MessagesSquare } from "lucide-react";

export default function WorkspaceSwitcher({ onOpenGithub, onOpenPricing, onCreateTeam, showSessions, setShowSessions, onOpenTeamChat, isTeamChatOpen }) {
  const {
    myTeams, loadMyTeams, switchTeam, currentUser, isDarkMode, toggleDarkMode,
    teamWorkspaces, pipelineStatus,
    authToken, backendPort, userPlan,
    checkAuthStatus, logout,
  } = useAppStore();

  // 컨텍스트 메뉴
  const [ctx, setCtx] = useState(null);
  // 유저 팝업
  const [userPopup, setUserPopup] = useState(false);
  // 미니 모달
  const [modal, setModal] = useState(null);
  const [modalData, setModalData] = useState({});

  const ctxRef = useRef(null);
  const userPopupRef = useRef(null);

  useEffect(() => {
    if (!userPopup) return;
    const close = (e) => {
      if (userPopupRef.current && !userPopupRef.current.contains(e.target)) setUserPopup(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [userPopup]);

  useEffect(() => { loadMyTeams(); }, []);

  // 컨텍스트 메뉴 외부 클릭 닫기
  useEffect(() => {
    if (!ctx) return;
    const close = (e) => {
      if (ctxRef.current && !ctxRef.current.contains(e.target)) setCtx(null);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [ctx]);

  const openCtx = (e, team = null) => {
    e.preventDefault();
    e.stopPropagation();
    setCtx({ x: e.clientX, y: e.clientY, team });
  };

  const closeCtx = () => setCtx(null);

  const openModal = (type, data = {}) => {
    closeCtx();
    setModal(type);
    setModalData(data);
  };

  const closeModal = () => {
    setModal(null);
    setModalData({});
  };

  const getInitials = (name) => name ? name.substring(0, 2).toUpperCase() : "?";

  // ── 컨텍스트 메뉴 항목 정의 ─────────────────────────────
  const teamMenuItems = (team) => {
    const isTeamPm = team.role === "pm"; // 해당 팀에서의 역할
    const items = [];
    if (isTeamPm) {
      items.push({ icon: Edit3,    label: "팀 이름 변경",    action: () => openModal("rename", { team }) });
      items.push({ icon: UserPlus, label: "초대 코드 생성",  action: () => openModal("invite", { team }) });
      items.push({ icon: Users,    label: "팀원 관리",       action: () => openModal("members", { team }) });
      items.push({ divider: true });
    }
    items.push({ icon: LogOut, label: "팀 나가기", danger: true, action: () => openModal("leave", { team }) });
    return items;
  };

  const plusMenuItems = [
    { icon: Plus,     label: "새 팀 만들기",    action: () => openModal("create") },
    { icon: UserPlus, label: "초대 코드로 합류", action: () => openModal("join") },
  ];

  return (
    <div className={`w-[72px] h-full flex flex-col items-center py-4 gap-3 shrink-0 border-r ${
      isDarkMode ? "bg-[#090b10] border-white/5" : "bg-slate-100/50 border-[var(--border)]"
    }`}>

      {/* 팀 버튼 목록 */}
      {myTeams.map((team) => {
        const isActive = team.id === currentUser?.team_id;
        const teamPipelineStatus = isActive ? pipelineStatus : teamWorkspaces[team.id]?.pipelineStatus;
        const isRunning = teamPipelineStatus === "running";

        return (
          <button
            key={team.id}
            onClick={() => switchTeam(team.id)}
            onContextMenu={(e) => openCtx(e, team)}
            title={`${team.name} (${team.role})`}
            className={`relative group w-12 h-12 flex items-center justify-center rounded-2xl transition-all duration-200 ${
              isActive
                ? "bg-blue-600 text-white shadow-lg shadow-blue-500/30"
                : isDarkMode
                ? "bg-white/10 text-slate-300 hover:bg-white/20 hover:scale-105"
                : "bg-white text-slate-600 shadow-sm border border-slate-200 hover:bg-slate-50 hover:scale-105"
            }`}
          >
            <span className="text-[14px] font-black tracking-tighter">{getInitials(team.name)}</span>
            {isRunning && (
              <span className="absolute -top-1 -right-1 flex items-center justify-center w-4 h-4 rounded-full bg-blue-500">
                <Loader2 size={10} className="animate-spin text-white" />
              </span>
            )}
            {isActive && <div className="absolute -left-3 w-1.5 h-8 bg-blue-500 rounded-r-full" />}
            {!isActive && <div className="absolute -left-3 w-1.5 h-0 bg-slate-400 rounded-r-full transition-all duration-200 group-hover:h-5" />}
          </button>
        );
      })}

      <div className={`w-8 border-t my-1 ${isDarkMode ? "border-white/10" : "border-slate-200"}`} />

      {/* + 버튼 */}
      <button
        onContextMenu={(e) => openCtx(e, null)}
        onClick={(e) => openCtx(e, null)}
        title="팀 만들기 / 합류"
        className={`w-12 h-12 flex items-center justify-center rounded-2xl transition-all ${
          isDarkMode
            ? "bg-transparent border border-white/10 text-slate-400 hover:text-white hover:bg-white/5"
            : "bg-transparent border border-slate-300 text-slate-500 hover:bg-slate-100"
        } hover:border-dashed`}
      >
        <Plus size={20} />
      </button>

      <button
        onClick={onOpenTeamChat}
        title="팀 채팅"
        aria-label="팀 채팅"
        className={`w-10 h-10 rounded-xl flex items-center justify-center transition-all ${
          isTeamChatOpen
            ? "bg-blue-600 text-white shadow-lg shadow-blue-500/30"
            : isDarkMode
            ? "text-slate-400 hover:text-white hover:bg-white/5"
            : "text-slate-500 hover:bg-slate-100"
        }`}
      >
        <MessagesSquare size={18} />
      </button>

      {/* 하단 유저 아바타 및 라이브러리 단축 버튼 */}
      <div className="mt-auto flex flex-col items-center gap-3">
        <button
          onClick={() => setShowSessions?.(!showSessions)}
          title="라이브러리 (대화 기록)"
          className={`w-10 h-10 rounded-xl flex items-center justify-center transition-all ${
            showSessions
              ? "bg-blue-600 text-white shadow-lg shadow-blue-500/30"
              : isDarkMode
              ? "bg-transparent text-slate-400 hover:text-white hover:bg-white/5"
              : "bg-transparent text-slate-500 hover:bg-slate-100"
          }`}
        >
          <Library size={18} />
        </button>

        <div className={`w-8 border-t ${isDarkMode ? "border-white/10" : "border-slate-200"}`} />

        <div className="relative" ref={userPopupRef}>
          <button
            onClick={() => setUserPopup((v) => !v)}
            title={currentUser?.name || currentUser?.email}
            className={`w-10 h-10 rounded-full flex items-center justify-center text-[13px] font-black transition-all ring-2 ${
              userPopup ? "ring-blue-500" : "ring-transparent hover:ring-blue-500/40"
            } ${isDarkMode ? "bg-white/10 text-slate-300" : "bg-slate-200 text-slate-700"}`}
          >
            {currentUser?.name ? currentUser.name.substring(0, 2).toUpperCase() : "?"}
          </button>

        {userPopup && (
          <div className={`absolute bottom-12 left-full ml-2 z-[9000] w-[220px] rounded-2xl shadow-2xl border overflow-hidden py-1 ${
            isDarkMode ? "bg-[#1a1f2e] border-white/10" : "bg-white border-slate-200"
          }`}>
            {/* 유저 정보 */}
            <div className={`px-4 py-3 border-b ${isDarkMode ? "border-white/5" : "border-slate-100"}`}>
              <p className={`text-sm font-black truncate ${isDarkMode ? "text-white" : "text-slate-900"}`}>
                {currentUser?.name || "사용자"}
              </p>
              <p className={`text-[11px] truncate mt-0.5 ${isDarkMode ? "text-slate-400" : "text-slate-500"}`}>
                {currentUser?.email}
              </p>
            </div>
            {/* 메뉴 항목 */}
            <button
              onClick={() => { setUserPopup(false); onOpenGithub?.(); }}
              className={`w-full flex items-center gap-2.5 px-4 py-2.5 text-xs font-semibold transition-colors ${
                isDarkMode ? "text-slate-300 hover:bg-white/8" : "text-slate-700 hover:bg-slate-50"
              }`}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" className="opacity-70 shrink-0">
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.3 3.44 9.8 8.2 11.38.6.11.82-.26.82-.58v-2.03c-3.34.72-4.04-1.61-4.04-1.61-.55-1.39-1.34-1.76-1.34-1.76-1.08-.74.08-.73.08-.73 1.2.08 1.83 1.23 1.83 1.23 1.07 1.83 2.8 1.3 3.49 1 .11-.78.42-1.3.76-1.6-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.12-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 3-.4c1.02 0 2.04.14 3 .4 2.28-1.55 3.29-1.23 3.29-1.23.66 1.66.24 2.88.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.61-2.81 5.63-5.48 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.83.58C20.57 21.8 24 17.3 24 12c0-6.63-5.37-12-12-12z"/>
              </svg>
              GitHub 연동
            </button>
            <button
              onClick={() => { setUserPopup(false); onOpenPricing?.(); }}
              className={`w-full flex items-center justify-between gap-2.5 px-4 py-2.5 text-xs font-semibold transition-colors ${
                isDarkMode ? "text-slate-300 hover:bg-white/8" : "text-slate-700 hover:bg-slate-50"
              }`}
            >
              <span className="flex items-center gap-2.5">
                <CreditCard size={13} className="opacity-70" /> 플랜
              </span>
              <span className={`text-[10px] font-black uppercase tracking-wider px-2 py-0.5 rounded-full ${
                userPlan === "free" ? "bg-slate-500/20 text-slate-400"
                : userPlan === "pro" ? "bg-blue-500/20 text-blue-400"
                : "bg-purple-500/20 text-purple-400"
              }`}>{userPlan || "free"}</span>
            </button>

            <button
              onClick={() => toggleDarkMode()}
              className={`w-full flex items-center gap-2.5 px-4 py-2.5 text-xs font-semibold transition-colors ${
                isDarkMode ? "text-slate-300 hover:bg-white/8" : "text-slate-700 hover:bg-slate-50"
              }`}
            >
              {isDarkMode ? <Sun size={13} className="opacity-70" /> : <Moon size={13} className="opacity-70" />}
              {isDarkMode ? "라이트 모드" : "다크 모드"}
            </button>

            <div className={`my-1 border-t ${isDarkMode ? "border-white/5" : "border-slate-100"}`} />
            <button
              onClick={() => { setUserPopup(false); logout?.(); }}
              className="w-full flex items-center gap-2.5 px-4 py-2.5 text-xs font-semibold text-red-400 hover:bg-red-500/10 transition-colors"
            >
              <LogOut size={13} className="opacity-70" /> 로그아웃
            </button>
          </div>
        )}
        </div>
      </div>

      {/* ── 컨텍스트 메뉴 ─────────────────────────────── */}
      {ctx && (
        <ContextMenu
          ref={ctxRef}
          x={ctx.x}
          y={ctx.y}
          items={ctx.team ? teamMenuItems(ctx.team) : plusMenuItems}
          isDarkMode={isDarkMode}
        />
      )}

      {/* ── 미니 모달 ─────────────────────────────────── */}
      {modal && (
        <MiniModal
          type={modal}
          data={modalData}
          isDarkMode={isDarkMode}
          onClose={closeModal}
          authToken={authToken}
          backendPort={backendPort}
          currentUser={currentUser}
          onSuccess={async () => {
            closeModal();
            await checkAuthStatus();
            await loadMyTeams();
          }}
        />
      )}
    </div>
  );
}

// ── 컨텍스트 메뉴 ──────────────────────────────────────────
const ContextMenu = React.forwardRef(({ x, y, items, isDarkMode }, ref) => {
  // 화면 오른쪽/아래로 넘치지 않게 보정
  const safeX = Math.min(x, window.innerWidth - 200);
  const safeY = Math.min(y, window.innerHeight - items.length * 38 - 16);

  return (
    <div
      ref={ref}
      className={`fixed z-[9000] min-w-[180px] rounded-xl shadow-2xl border overflow-hidden py-1 ${
        isDarkMode ? "bg-[#1a1f2e] border-white/10" : "bg-white border-slate-200"
      }`}
      style={{ left: safeX, top: safeY }}
    >
      {items.map((item, i) =>
        item.divider ? (
          <div key={i} className={`my-1 border-t ${isDarkMode ? "border-white/10" : "border-slate-100"}`} />
        ) : (
          <button
            key={i}
            onClick={item.action}
            className={`w-full flex items-center gap-2.5 px-3 py-2 text-xs font-semibold transition-colors ${
              item.danger
                ? "text-red-400 hover:bg-red-500/10"
                : isDarkMode
                ? "text-slate-300 hover:bg-white/8"
                : "text-slate-700 hover:bg-slate-50"
            }`}
          >
            <item.icon size={13} className="shrink-0 opacity-70" />
            {item.label}
          </button>
        )
      )}
    </div>
  );
});
ContextMenu.displayName = "ContextMenu";

// ── 미니 모달 ──────────────────────────────────────────────
function MiniModal({ type, data, isDarkMode, onClose, authToken, backendPort, currentUser, onSuccess }) {
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(""); // invite code
  const [inviteRole, setInviteRole] = useState("software_engineer");
  const [members, setMembers] = useState([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [roleUpdating, setRoleUpdating] = useState(null); // userId being updated
  const port = backendPort || 8765;

  useEffect(() => {
    if (type !== "members") return;
    setMembersLoading(true);
    serverRequest(`/auth/teams/${currentUser?.team_id}/members`, {
      headers: { Authorization: `Bearer ${authToken}` },
    })
      .then((d) => setMembers(d.members || []))
      .catch(() => {})
      .finally(() => setMembersLoading(false));
  }, [type]);

  const bg    = isDarkMode ? "#161B22" : "#FFFFFF";
  const border= isDarkMode ? "rgba(255,255,255,0.1)" : "#E2E8F0";
  const text  = isDarkMode ? "#E6EDF3" : "#1E293B";
  const muted = isDarkMode ? "#8B949E" : "#64748B";
  const inputCls = `w-full px-3 py-2 text-sm rounded-lg outline-none border transition-colors ${
    isDarkMode ? "bg-black/30 border-white/10 text-white placeholder-white/30 focus:border-blue-500"
               : "bg-slate-50 border-slate-200 text-slate-900 focus:border-blue-500"
  }`;

  const CONFIGS = {
    rename: {
      title: "팀 이름 변경",
      placeholder: "새 팀 이름",
      submit: "변경",
      action: async () => {
        await serverRequest(`/auth/teams/${currentUser?.team_id}`, {
          method: "PATCH",
          headers: { Authorization: `Bearer ${authToken}` },
          body: JSON.stringify({ name: value.trim() }),
        });
      },
    },
    invite: {
      title: "초대 코드 생성",
      submit: "생성",
      action: async () => {
        const d = await serverRequest(`/auth/teams/${currentUser?.team_id}/invites`, {
          method: "POST",
          headers: { Authorization: `Bearer ${authToken}` },
          body: JSON.stringify({ role: inviteRole, max_uses: 1, expires_in_days: 7 }),
        });
        setResult(d.code);
      },
      noSuccessClose: true,
    },
    join: {
      title: "초대 코드로 합류",
      placeholder: "초대 코드 입력",
      submit: "합류",
      action: async () => {
        await serverRequest(`/auth/invites/${value.trim()}/join`, {
          method: "POST",
          headers: { Authorization: `Bearer ${authToken}` },
        });
        await useAppStore.getState().loadMyTeams();
      },
    },
    create: {
      title: "새 팀 만들기",
      placeholder: "팀 이름",
      submit: "만들기",
      action: async () => {
        await useAppStore.getState().createTeam(value.trim());
      },
    },
    members: {
      title: `${data.team?.name} 팀원 관리`,
      noSubmit: true,
    },
    leave: {
      title: `"${data.team?.name}" 팀 나가기`,
      submit: "나가기",
      danger: true,
      description: "팀에서 나가면 해당 팀의 데이터에 접근할 수 없게 됩니다.",
      action: async () => {
        // TODO: leave team endpoint 추가 후 구현
        throw new Error("팀 나가기 기능은 아직 준비 중입니다.");
      },
    },
  };

  const cfg = CONFIGS[type];
  if (!cfg) return null;

  const handleSubmit = async (e) => {
    e?.preventDefault();
    if (!cfg.action) return;
    setLoading(true); setError("");
    try {
      await cfg.action();
      if (!cfg.noSuccessClose) onSuccess?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[9500] flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(3px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className={`${type === "members" ? "w-[380px]" : "w-[320px]"} rounded-2xl shadow-2xl p-5 flex flex-col gap-4`}
        style={{ background: bg, border: `1px solid ${border}` }}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-black" style={{ color: text }}>{cfg.title}</h3>
          <button onClick={onClose} className="p-1 rounded-lg opacity-40 hover:opacity-70 transition-opacity" style={{ color: text }}>
            <X size={14} />
          </button>
        </div>

        {/* 설명 */}
        {cfg.description && (
          <p className="text-xs" style={{ color: muted }}>{cfg.description}</p>
        )}

        {/* 역할 선택 (invite 전용) */}
        {type === "invite" && !result && (
          <select
            value={inviteRole}
            onChange={(e) => setInviteRole(e.target.value)}
            style={{ colorScheme: isDarkMode ? "dark" : "light" }}
            className={inputCls}
          >
            <option value="software_engineer">Software Engineer</option>
            <option value="backend">Backend</option>
            <option value="frontend">Frontend</option>
            <option value="devops">DevOps</option>
            <option value="pm">PM</option>
          </select>
        )}

        {/* 팀원 관리 */}
        {type === "members" && (
          <div className="flex flex-col gap-2 max-h-[320px] overflow-y-auto">
            {membersLoading ? (
              <div className="flex justify-center py-6"><Loader2 size={18} className="animate-spin opacity-40" /></div>
            ) : members.length === 0 ? (
              <p className="text-xs text-center py-4" style={{ color: muted }}>팀원이 없습니다.</p>
            ) : members.map((m) => (
              <div key={m.id} className={`flex items-center gap-2 px-2 py-1.5 rounded-xl ${isDarkMode ? "bg-white/5" : "bg-slate-50"}`}>
                <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-black shrink-0 ${m.id === currentUser?.id ? "bg-blue-600 text-white" : isDarkMode ? "bg-white/15 text-slate-300" : "bg-slate-200 text-slate-600"}`}>
                  {m.name ? m.name.substring(0, 2).toUpperCase() : "?"}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold truncate" style={{ color: text }}>{m.name}{m.id === currentUser?.id && <span className="ml-1 text-blue-400 font-normal">(나)</span>}</p>
                  <p className="text-[10px] truncate" style={{ color: muted }}>{m.email}</p>
                </div>
                {m.id !== currentUser?.id ? (
                  <select
                    value={m.role}
                    disabled={roleUpdating === m.id}
                    onChange={async (e) => {
                      const newRole = e.target.value;
                      setRoleUpdating(m.id);
                      try {
                        await serverRequest(`/auth/users/${m.id}/role`, {
                          method: "PATCH",
                          headers: { Authorization: `Bearer ${authToken}` },
                          body: JSON.stringify({ role: newRole }),
                        });
                        setMembers((prev) => prev.map((x) => x.id === m.id ? { ...x, role: newRole } : x));
                      } catch {
                        setError("역할 변경 실패");
                      } finally {
                        setRoleUpdating(null);
                      }
                    }}
                    style={{ colorScheme: isDarkMode ? "dark" : "light" }}
                    className={`text-[10px] px-1.5 py-1 rounded-lg border outline-none transition-colors ${isDarkMode ? "bg-black/30 border-white/10 text-white" : "bg-white border-slate-200 text-slate-700"}`}
                  >
                    <option value="pm">PM</option>
                    <option value="software_engineer">SWE</option>
                    <option value="backend">Backend</option>
                    <option value="frontend">Frontend</option>
                    <option value="devops">DevOps</option>
                  </select>
                ) : (
                  <span className={`text-[10px] px-2 py-1 rounded-lg ${isDarkMode ? "bg-blue-600/20 text-blue-400" : "bg-blue-50 text-blue-600"}`}>{m.role}</span>
                )}
              </div>
            ))}
            {error && <p className="text-xs text-red-400">{error}</p>}
            <button onClick={onClose} className="mt-1 w-full py-2 rounded-xl text-xs font-bold text-white" style={{ background: "linear-gradient(135deg, #2563EB, #4F46E5)" }}>닫기</button>
          </div>
        )}

        {/* 초대 코드 결과 표시 */}
        {type === "invite" && result ? (
          <div className="flex flex-col gap-2">
            <p className="text-xs" style={{ color: muted }}>팀원에게 아래 코드를 공유하세요 (7일 / 1회용)</p>
            <div className="flex gap-2 items-center">
              <input
                readOnly value={result}
                className={`${inputCls} flex-1 font-mono tracking-widest text-center`}
              />
              <button
                onClick={() => { navigator.clipboard.writeText(result); }}
                className={`shrink-0 p-2 rounded-lg transition-colors ${
                  isDarkMode ? "bg-white/10 hover:bg-white/20 text-slate-300" : "bg-slate-100 hover:bg-slate-200 text-slate-600"
                }`}
              >
                <Copy size={14} />
              </button>
            </div>
            <button
              onClick={onClose}
              className="w-full py-2 rounded-xl text-xs font-bold text-white transition-all"
              style={{ background: "linear-gradient(135deg, #2563EB, #4F46E5)" }}
            >
              닫기
            </button>
          </div>
        ) : type !== "members" && (
          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            {/* 텍스트 입력 (해당 타입만) */}
            {cfg.placeholder && (
              <input
                autoFocus
                type="text"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={cfg.placeholder}
                className={inputCls}
              />
            )}

            {error && <p className="text-xs text-red-400">{error}</p>}

            <button
              type="submit"
              disabled={loading || (cfg.placeholder && !value.trim())}
              className={`w-full py-2.5 rounded-xl text-xs font-bold text-white transition-all disabled:opacity-50 ${
                cfg.danger ? "bg-red-500 hover:bg-red-600" : ""
              }`}
              style={cfg.danger ? {} : { background: "linear-gradient(135deg, #2563EB, #4F46E5)" }}
            >
              {loading ? <Loader2 size={14} className="animate-spin mx-auto" /> : cfg.submit}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

