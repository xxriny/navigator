import {
  Table2, Layers, Globe, Database,
  ShieldCheck, FolderTree, GitBranch, Github, ClipboardList, Share2, GitPullRequest, MessagesSquare,
} from "lucide-react";

export const ICON_PANELS = [
  { id: "rtm", label: "RTM", Icon: Table2, group: "pm", color: "text-cyan-400", bg: "bg-cyan-500/10" },
  { id: "stack", label: "Stack", Icon: Layers, group: "pm", color: "text-indigo-400", bg: "bg-indigo-500/10" },
  { id: "sa_components", label: "Components", Icon: Layers, group: "sa", color: "text-teal-400", bg: "bg-teal-500/10" },
  { id: "sa_api", label: "API Spec", Icon: Globe, group: "sa", color: "text-blue-400", bg: "bg-blue-500/10" },
  { id: "sa_db", label: "Database", Icon: Database, group: "sa", color: "text-rose-400", bg: "bg-rose-500/10" },
  { id: "sa_test_strategy", label: "Test Strategy", Icon: ShieldCheck, group: "sa", color: "text-emerald-400", bg: "bg-emerald-500/10" },
  { id: "project_structure", label: "Proj Structure", Icon: FolderTree, group: "sa", color: "text-violet-400", bg: "bg-violet-500/10" },
  { id: "agile_impact", label: "Impact Analysis", Icon: GitBranch, group: "collab", color: "text-blue-400", bg: "bg-blue-500/10" },
  { id: "team_chat", label: "팀 채팅", Icon: MessagesSquare, group: "collab", color: "text-blue-400", bg: "bg-blue-500/10" },
  { id: "github_dashboard", label: "GitHub", Icon: Github, group: "collab", color: "text-slate-400", bg: "bg-slate-500/10" },
  { id: "task_approval", label: "Tasks", Icon: ClipboardList, group: "collab", color: "text-amber-400", bg: "bg-amber-500/10" },
  // author: xxrin
  // PR 기반 Dev Tracking을 UI에서 직접 실행하고 결과를 볼 수 있는 운영 탭이다.
  { id: "dev_tracking", label: "Dev Tracking", Icon: GitPullRequest, group: "collab", color: "text-emerald-400", bg: "bg-emerald-500/10" },
  { id: "shared", label: "Shared", Icon: Share2, group: "collab", color: "text-purple-400", bg: "bg-purple-500/10" },
];

export const THEME_MAP = {
  slate: { bg: "bg-slate-50/50", activeBg: "bg-slate-100", text: "text-slate-700", activeText: "text-slate-900", border: "border-slate-100", iconBg: "bg-slate-500/10" },
  blue: { bg: "bg-blue-50/50", activeBg: "bg-blue-100/60", text: "text-blue-700", activeText: "text-blue-800", border: "border-blue-100", iconBg: "bg-blue-500/10" },
  sky: { bg: "bg-sky-50/50", activeBg: "bg-sky-100/60", text: "text-sky-600", activeText: "text-sky-800", border: "border-sky-100", iconBg: "bg-sky-500/10" },
  yellow: { bg: "bg-yellow-50/50", activeBg: "bg-yellow-100/60", text: "text-yellow-700", activeText: "text-yellow-800", border: "border-yellow-100", iconBg: "bg-yellow-500/10" },
  cyan: { bg: "bg-cyan-50/50", activeBg: "bg-cyan-100/60", text: "text-cyan-700", activeText: "text-cyan-800", border: "border-cyan-100", iconBg: "bg-cyan-500/10" },
  green: { bg: "bg-green-50/50", activeBg: "bg-green-100/60", text: "text-green-700", activeText: "text-green-800", border: "border-green-100", iconBg: "bg-green-500/10" },
  amber: { bg: "bg-amber-50/50", activeBg: "bg-amber-100/60", text: "text-amber-700", activeText: "text-amber-800", border: "border-amber-100", iconBg: "bg-amber-500/10" },
  indigo: { bg: "bg-indigo-50/50", activeBg: "bg-indigo-100/60", text: "text-indigo-700", activeText: "text-indigo-800", border: "border-indigo-100", iconBg: "bg-indigo-500/10" },
  teal: { bg: "bg-teal-50/50", activeBg: "bg-teal-100/60", text: "text-teal-700", activeText: "text-teal-800", border: "border-teal-100", iconBg: "bg-teal-500/10" },
  rose: { bg: "bg-rose-50/50", activeBg: "bg-rose-100/60", text: "text-rose-700", activeText: "text-rose-800", border: "border-rose-100", iconBg: "bg-rose-500/10" },
};
