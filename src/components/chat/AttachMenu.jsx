import React, { useRef, useState, useEffect } from "react";
import {
  Paperclip, Github, FileText, X, GitBranch,
  FolderGit2, ChevronRight, ScanSearch,
} from "lucide-react";
import useAppStore from "../../store/useAppStore";
import { apiBaseUrl } from "../../api/apiClient";

export default function AttachMenu({ isDarkMode, disabled, onAttach }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [repoModalOpen, setRepoModalOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setMenuOpen(false);
    e.target.value = "";
    const backendPort = useAppStore.getState().backendPort;
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
    <div className="relative" ref={menuRef}>
      {/* 클립 버튼 */}
      <button
        onClick={() => !disabled && setMenuOpen((v) => !v)}
        disabled={disabled}
        title="파일 또는 레포지토리 첨부"
        className={`w-11 h-11 flex items-center justify-center rounded-2xl transition-all duration-150 ${
          menuOpen
            ? isDarkMode
              ? "bg-white/[0.1] text-white"
              : "bg-slate-200 text-slate-800"
            : isDarkMode
              ? "bg-white/[0.05] text-slate-500 hover:bg-white/[0.09] hover:text-slate-300"
              : "bg-slate-100 text-slate-400 hover:bg-slate-200 hover:text-slate-600"
        } disabled:opacity-30 disabled:cursor-not-allowed`}
      >
        <Paperclip size={16} strokeWidth={2} />
      </button>

      {/* 팝업 */}
      {menuOpen && (
        <div className={`absolute bottom-[52px] left-0 w-[220px] rounded-2xl border shadow-[0_8px_32px_rgba(0,0,0,0.4)] z-50 animate-fade-in overflow-hidden ${
          isDarkMode
            ? "bg-[#0e1218] border-white/[0.08]"
            : "bg-white border-slate-200/80"
        }`}>
          {/* 헤더 */}
          <div className={`px-4 pt-3.5 pb-2 ${isDarkMode ? "border-b border-white/[0.05]" : "border-b border-slate-100"}`}>
            <p className={`text-[10px] font-black uppercase tracking-[0.22em] ${isDarkMode ? "text-slate-600" : "text-slate-400"}`}>
              컨텍스트 첨부
            </p>
          </div>

          {/* 항목들 */}
          <div className="p-2 space-y-0.5">
            {/* 레포지토리 */}
            <button
              onClick={() => { setMenuOpen(false); setRepoModalOpen(true); }}
              className={`w-full flex items-center gap-3 px-2.5 py-2.5 rounded-xl transition-all text-left group ${
                isDarkMode ? "hover:bg-white/[0.06]" : "hover:bg-slate-50"
              }`}
            >
              <div className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 transition-colors ${
                isDarkMode
                  ? "bg-slate-800 group-hover:bg-blue-500/20 text-slate-400 group-hover:text-blue-400"
                  : "bg-slate-100 group-hover:bg-blue-50 text-slate-500 group-hover:text-blue-600"
              }`}>
                <FolderGit2 size={14} />
              </div>
              <div className="flex-1 min-w-0">
                <p className={`text-[13px] font-semibold ${isDarkMode ? "text-slate-200" : "text-slate-700"}`}>
                  레포지토리
                </p>
                <p className={`text-[11px] ${isDarkMode ? "text-slate-600" : "text-slate-400"}`}>
                  GitHub repo · 브랜치
                </p>
              </div>
              <ChevronRight size={12} className={isDarkMode ? "text-slate-700 group-hover:text-slate-500" : "text-slate-300 group-hover:text-slate-400"} />
            </button>

            {/* 문서 업로드 */}
            <label className={`w-full flex items-center gap-3 px-2.5 py-2.5 rounded-xl transition-all text-left cursor-pointer group ${
              isDarkMode ? "hover:bg-white/[0.06]" : "hover:bg-slate-50"
            }`}>
              <div className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 transition-colors ${
                isDarkMode
                  ? "bg-slate-800 group-hover:bg-violet-500/20 text-slate-400 group-hover:text-violet-400"
                  : "bg-slate-100 group-hover:bg-violet-50 text-slate-500 group-hover:text-violet-600"
              }`}>
                <FileText size={14} />
              </div>
              <div className="flex-1 min-w-0">
                <p className={`text-[13px] font-semibold ${isDarkMode ? "text-slate-200" : "text-slate-700"}`}>
                  문서 업로드
                </p>
                <p className={`text-[11px] ${isDarkMode ? "text-slate-600" : "text-slate-400"}`}>
                  PDF · Word · TXT · MD
                </p>
              </div>
              <ChevronRight size={12} className={isDarkMode ? "text-slate-700 group-hover:text-slate-500" : "text-slate-300 group-hover:text-slate-400"} />
              <input
                type="file"
                accept=".pdf,.doc,.docx,.txt,.md"
                className="hidden"
                onChange={handleFileChange}
              />
            </label>
          </div>

          {/* 푸터 힌트 */}
          <div className={`px-4 py-2.5 ${isDarkMode ? "border-t border-white/[0.05]" : "border-t border-slate-100"}`}>
            <p className={`text-[10px] leading-relaxed ${isDarkMode ? "text-slate-700" : "text-slate-400"}`}>
              첨부 후 에이전트가 분석 여부를 확인합니다
            </p>
          </div>
        </div>
      )}

      {repoModalOpen && (
        <RepoPickerModal
          isDarkMode={isDarkMode}
          onClose={() => setRepoModalOpen(false)}
          onConfirm={(payload) => { setRepoModalOpen(false); onAttach(payload); }}
        />
      )}
    </div>
  );
}

/* ── 레포 선택 모달 ──────────────────────────────── */
function RepoPickerModal({ isDarkMode, onClose, onConfirm }) {
  const githubOwner  = useAppStore((s) => s.githubOwner);
  const githubRepo   = useAppStore((s) => s.githubRepo);
  const githubBranch = useAppStore((s) => s.githubBranch);

  const [owner,  setOwner]  = useState(githubOwner  || "");
  const [repo,   setRepo]   = useState(githubRepo   || "");
  const [branch, setBranch] = useState(githubBranch || "main");

  const canConfirm = owner.trim() && repo.trim() && branch.trim();

  // ESC 닫기
  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center px-4 animate-fade-in">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-md" onClick={onClose} />

      <div className={`relative w-full max-w-[400px] rounded-3xl border shadow-[0_24px_80px_rgba(0,0,0,0.6)] overflow-hidden ${
        isDarkMode ? "bg-[#0c1017] border-white/[0.07]" : "bg-white border-slate-200"
      }`}>

        {/* 헤더 */}
        <div className="relative px-6 pt-6 pb-5">
          <button
            onClick={onClose}
            className={`absolute top-5 right-5 w-7 h-7 flex items-center justify-center rounded-full transition-colors ${
              isDarkMode ? "hover:bg-white/[0.08] text-slate-500 hover:text-slate-300" : "hover:bg-slate-100 text-slate-400 hover:text-slate-600"
            }`}
          >
            <X size={13} />
          </button>

          <div className="flex items-center gap-3.5">
            <div className={`w-11 h-11 rounded-2xl flex items-center justify-center shrink-0 ${
              isDarkMode ? "bg-white/[0.06]" : "bg-slate-100"
            }`}>
              <Github size={20} className={isDarkMode ? "text-slate-300" : "text-slate-600"} />
            </div>
            <div>
              <p className={`text-[16px] font-black tracking-tight ${isDarkMode ? "text-white" : "text-slate-900"}`}>
                레포지토리 선택
              </p>
              <p className={`text-[12px] mt-0.5 ${isDarkMode ? "text-slate-500" : "text-slate-400"}`}>
                분석할 GitHub 레포와 브랜치를 입력하세요
              </p>
            </div>
          </div>
        </div>

        {/* 구분선 */}
        <div className={`mx-6 h-px ${isDarkMode ? "bg-white/[0.05]" : "bg-slate-100"}`} />

        {/* 입력 영역 */}
        <div className="px-6 py-5 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <RepoField
              label="Owner"
              placeholder="octocat"
              value={owner}
              onChange={setOwner}
              isDarkMode={isDarkMode}
              autoFocus
            />
            <RepoField
              label="Repository"
              placeholder="my-project"
              value={repo}
              onChange={setRepo}
              isDarkMode={isDarkMode}
            />
          </div>
          <RepoField
            label="Branch"
            placeholder="main"
            value={branch}
            onChange={setBranch}
            isDarkMode={isDarkMode}
            prefix={<GitBranch size={12} className={isDarkMode ? "text-slate-500" : "text-slate-400"} />}
          />

          {/* 프리뷰 */}
          <div className={`flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl text-[12px] border transition-all ${
            canConfirm
              ? isDarkMode
                ? "bg-blue-500/[0.07] border-blue-500/20 text-blue-300"
                : "bg-blue-50 border-blue-200 text-blue-700"
              : isDarkMode
                ? "bg-white/[0.03] border-white/[0.05] text-slate-600"
                : "bg-slate-50 border-slate-200 text-slate-400"
          }`}>
            <Github size={12} />
            <span className="font-semibold">{owner || "owner"}/{repo || "repo"}</span>
            <span className="opacity-40">·</span>
            <GitBranch size={11} />
            <span>{branch || "main"}</span>
          </div>
        </div>

        {/* 푸터 */}
        <div className={`px-6 py-4 flex items-center justify-between border-t ${
          isDarkMode ? "border-white/[0.05] bg-white/[0.015]" : "border-slate-100 bg-slate-50/60"
        }`}>
          <p className={`text-[11px] ${isDarkMode ? "text-slate-600" : "text-slate-400"}`}>
            <ScanSearch size={11} className="inline mr-1.5 mb-0.5" />
            역분석 파이프라인이 실행됩니다
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className={`px-3.5 py-2 rounded-xl text-[12px] font-semibold transition-colors ${
                isDarkMode
                  ? "text-slate-400 hover:text-slate-200 hover:bg-white/[0.06]"
                  : "text-slate-500 hover:text-slate-700 hover:bg-slate-100"
              }`}
            >
              취소
            </button>
            <button
              onClick={() => canConfirm && onConfirm({
                type: "repo",
                owner: owner.trim(),
                repo: repo.trim(),
                branch: branch.trim(),
              })}
              disabled={!canConfirm}
              className={`px-4 py-2 rounded-xl text-[12px] font-bold transition-all ${
                canConfirm
                  ? "bg-white text-slate-900 hover:bg-slate-100 shadow-lg active:scale-[0.97]"
                  : isDarkMode
                    ? "bg-white/[0.04] text-slate-600 cursor-not-allowed"
                    : "bg-slate-100 text-slate-400 cursor-not-allowed"
              }`}
            >
              선택 완료
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function RepoField({ label, placeholder, value, onChange, isDarkMode, prefix, autoFocus }) {
  return (
    <div>
      <label className={`block text-[10px] font-black uppercase tracking-[0.2em] mb-2 ${
        isDarkMode ? "text-slate-600" : "text-slate-400"
      }`}>
        {label}
      </label>
      <div className={`flex items-center gap-2 px-3.5 py-2.5 rounded-xl border transition-all ${
        isDarkMode
          ? "bg-white/[0.04] border-white/[0.08] focus-within:border-white/20 focus-within:bg-white/[0.06]"
          : "bg-white border-slate-200 focus-within:border-slate-400 focus-within:shadow-sm"
      }`}>
        {prefix}
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoFocus={autoFocus}
          className={`w-full bg-transparent text-[13px] outline-none ${
            isDarkMode
              ? "text-slate-200 placeholder:text-slate-700"
              : "text-slate-700 placeholder:text-slate-400"
          }`}
        />
      </div>
    </div>
  );
}
