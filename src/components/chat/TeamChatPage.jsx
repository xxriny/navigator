import React, { useEffect, useRef, useState } from "react";
import { ArrowUp, Hash, Loader2, MessagesSquare, Plus, X } from "lucide-react";
import useAppStore from "../../store/useAppStore";
import { serverRequest } from "../../api/serverClient";

const clock = (value) => {
  if (!value) return "";
  const date = new Date(value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });
};

export default function TeamChatPage({ onClose }) {
  const authToken = useAppStore((s) => s.authToken);
  const currentUser = useAppStore((s) => s.currentUser);
  const myTeams = useAppStore((s) => s.myTeams);
  const isDarkMode = useAppStore((s) => s.isDarkMode);
  const teamId = currentUser?.team_id;
  const team = myTeams.find((item) => item.id === teamId);
  const [channels, setChannels] = useState([]);
  const [members, setMembers] = useState([]);
  const [unreadByPeer, setUnreadByPeer] = useState({});
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [newChannel, setNewChannel] = useState("");
  const [creating, setCreating] = useState(false);
  const [channelSaving, setChannelSaving] = useState(false);
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef(null);
  const roomKey = selected ? `${selected.type}:${selected.id}` : "";
  const headers = { Authorization: `Bearer ${authToken}` };

  useEffect(() => {
    setSelected(null);
    setChannels([]);
    setMembers([]);
    setMessages([]);
    setUnreadByPeer({});
    setError("");
    if (!teamId || !authToken) return;
    let active = true;
    const refresh = async () => {
      try {
        const [channelData, memberData, inboxData] = await Promise.all([
          serverRequest("/messages/channels", { headers }),
          serverRequest(`/auth/teams/${teamId}/members`, { headers }),
          serverRequest("/messages/inbox", { headers }),
        ]);
        if (!active) return;
        const nextChannels = channelData.channels || [];
        setChannels(nextChannels);
        setMembers((memberData.members || []).filter((member) => member.id !== currentUser.id));
        const unread = {};
        for (const message of inboxData.messages || []) {
          if (!message.is_read) unread[message.sender_id] = (unread[message.sender_id] || 0) + 1;
        }
        setUnreadByPeer(unread);
        setSelected((before) => before || (nextChannels[0] ? { type: "channel", id: nextChannels[0].id } : null));
        setError("");
      } catch (cause) {
        if (active) setError(cause.message || "채팅 목록을 불러오지 못했습니다.");
      }
    };
    refresh();
    // 개발용 서비스에서는 짧은 주기로 새 메시지를 확인한다.
    const timer = setInterval(refresh, 5000);
    return () => { active = false; clearInterval(timer); };
  }, [teamId, authToken, currentUser?.id]);

  useEffect(() => {
    setMessages([]);
    setDraft("");
    if (!roomKey || !authToken) return;
    let active = true;
    const refresh = async () => {
      try {
        const path = selected.type === "channel"
          ? `/messages/channels/${selected.id}/messages`
          : `/messages/threads/${selected.id}`;
        const data = await serverRequest(path, { headers });
        if (!active) return;
        setMessages(data.messages || []);
        if (selected.type === "dm") setUnreadByPeer((before) => ({ ...before, [selected.id]: 0 }));
        setError("");
      } catch (cause) {
        if (active) setError(cause.message || "대화를 불러오지 못했습니다.");
      } finally {
        if (active) setLoading(false);
      }
    };
    setLoading(true);
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => { active = false; clearInterval(timer); };
  }, [roomKey, authToken]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const createChannel = async (event) => {
    event.preventDefault();
    if (!newChannel.trim() || channelSaving) return;
    setChannelSaving(true);
    setError("");
    try {
      const data = await serverRequest("/messages/channels", {
        method: "POST", headers, body: JSON.stringify({ name: newChannel.trim() }),
      });
      setChannels((before) => [...before, data.channel]);
      setSelected({ type: "channel", id: data.channel.id });
      setNewChannel("");
      setCreating(false);
    } catch (cause) {
      setError(cause.message || "채널을 만들지 못했습니다.");
    } finally {
      setChannelSaving(false);
    }
  };

  const send = async (event) => {
    event.preventDefault();
    if (!selected || !draft.trim() || sending) return;
    setSending(true);
    setError("");
    try {
      const isChannel = selected.type === "channel";
      const path = isChannel ? `/messages/channels/${selected.id}/messages` : "/messages";
      await serverRequest(path, {
        method: "POST", headers,
        body: JSON.stringify(isChannel ? { content: draft.trim() } : { receiver_id: selected.id, content: draft.trim() }),
      });
      setDraft("");
      const data = await serverRequest(isChannel ? `${path}` : `/messages/threads/${selected.id}`, { headers });
      setMessages(data.messages || []);
    } catch (cause) {
      setError(cause.message || "메시지를 보내지 못했습니다.");
    } finally {
      setSending(false);
    }
  };

  const selectedChannel = channels.find((item) => selected?.type === "channel" && item.id === selected.id);
  const selectedMember = members.find((item) => selected?.type === "dm" && item.id === selected.id);
  const title = selectedChannel ? `# ${selectedChannel.name}` : selectedMember?.name || selectedMember?.email || "팀 채팅";
  const panel = isDarkMode ? "bg-[#11151e] border-white/[0.08] text-slate-100" : "bg-white border-slate-200 text-slate-900";
  const muted = isDarkMode ? "text-slate-400" : "text-slate-500";
  const hover = isDarkMode ? "hover:bg-white/[0.06]" : "hover:bg-slate-100";

  return (
    <div className={`flex h-full min-h-0 w-full overflow-hidden ${panel}`}>
      <aside className={`w-[230px] shrink-0 flex flex-col border-r ${panel}`} aria-label="채팅 목록">
        <div className={`h-[72px] px-4 flex items-center gap-3 border-b ${isDarkMode ? "border-white/[0.08]" : "border-slate-200"}`}>
          <span className="w-9 h-9 rounded-xl bg-blue-600 text-white grid place-items-center font-bold">{(team?.name || "N").slice(0, 1)}</span>
          <div className="min-w-0"><div className="text-sm font-bold truncate">{team?.name || "팀 채팅"}</div><div className={`text-[11px] ${muted}`}>현재 팀의 대화</div></div>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-2 py-4">
          <div className={`px-3 mb-2 text-[11px] font-bold uppercase tracking-wider ${muted}`}>채널</div>
          {channels.map((channel) => (
            <button key={channel.id} onClick={() => setSelected({ type: "channel", id: channel.id })}
              className={`w-full rounded-lg px-3 py-2 flex items-center gap-2 text-sm text-left ${selected?.type === "channel" && selected.id === channel.id ? "bg-blue-600/15 text-blue-500 font-semibold" : hover}`}>
              <Hash size={15} className="shrink-0" /><span className="truncate flex-1">{channel.name}</span>
              {channel.unread_count > 0 && <span className="min-w-5 h-5 rounded-full bg-blue-600 text-white text-[10px] grid place-items-center px-1">{channel.unread_count}</span>}
            </button>
          ))}
          {creating ? (
            <form onSubmit={createChannel} className="mt-2 px-2 flex gap-1">
              <input autoFocus value={newChannel} onChange={(event) => setNewChannel(event.target.value)} maxLength={50} placeholder="채널 이름"
                className={`min-w-0 flex-1 rounded-lg border px-2 py-1.5 text-xs outline-none ${panel}`} />
              <button type="submit" disabled={channelSaving} aria-label="채널 만들기" className="text-blue-500 disabled:opacity-40"><ArrowUp size={16} /></button>
              <button type="button" onClick={() => { setCreating(false); setNewChannel(""); }} aria-label="취소" className={muted}><X size={16} /></button>
            </form>
          ) : <button onClick={() => setCreating(true)} className={`w-full flex items-center gap-2 px-3 py-2 text-xs ${muted} ${hover}`}><Plus size={14} /> 채널 만들기</button>}
          <div className={`px-3 mt-6 mb-2 text-[11px] font-bold uppercase tracking-wider ${muted}`}>1:1 대화</div>
          {members.map((member) => (
            <button key={member.id} onClick={() => setSelected({ type: "dm", id: member.id })}
              className={`w-full rounded-lg px-3 py-2 flex items-center gap-2 text-sm text-left ${selected?.type === "dm" && selected.id === member.id ? "bg-blue-600/15 text-blue-500 font-semibold" : hover}`}>
              <span className="w-6 h-6 rounded-full bg-indigo-500/20 text-indigo-400 grid place-items-center text-[11px] font-bold shrink-0">{(member.name || member.email || "?").slice(0, 1)}</span>
              <span className="truncate flex-1">{member.name || member.email}</span>
              {unreadByPeer[member.id] > 0 && <span className="min-w-5 h-5 rounded-full bg-blue-600 text-white text-[10px] grid place-items-center px-1">{unreadByPeer[member.id]}</span>}
            </button>
          ))}
        </div>
      </aside>
      <section className="flex-1 min-w-0 min-h-0 flex flex-col" aria-label="대화 내용">
        <header className={`h-[72px] shrink-0 px-5 flex items-center justify-between border-b ${isDarkMode ? "border-white/[0.08]" : "border-slate-200"}`}>
          <div><h2 className="text-sm font-bold">{title}</h2><p className={`text-[11px] mt-1 ${muted}`}>{selectedChannel ? "팀원 모두가 볼 수 있는 대화" : selectedMember ? "1:1 대화" : "대화를 선택해 주세요"}</p></div>
          <button onClick={onClose} aria-label="팀 채팅 닫기" className={`p-2 rounded-lg ${muted} ${hover}`}><X size={17} /></button>
        </header>
        <div className="flex-1 min-h-0 overflow-y-auto px-5 py-5 space-y-5">
          {loading && <div className={`flex items-center gap-2 text-xs ${muted}`}><Loader2 size={14} className="animate-spin" /> 대화를 불러오는 중</div>}
          {!loading && messages.length === 0 && <div className={`h-full flex flex-col items-center justify-center gap-2 ${muted}`}><MessagesSquare size={28} /><p className="text-sm">첫 메시지를 보내 대화를 시작하세요.</p></div>}
          {messages.map((message) => (
            <article key={message.id} className="flex gap-3 items-start">
              <span className={`w-8 h-8 rounded-full shrink-0 grid place-items-center text-xs font-bold ${message.sender_id === currentUser?.id ? "bg-blue-600 text-white" : "bg-indigo-500/20 text-indigo-400"}`}>{(message.sender_name || "?").slice(0, 1)}</span>
              <div className="min-w-0"><div className="flex items-baseline gap-2"><strong className="text-xs">{message.sender_id === currentUser?.id ? "나" : message.sender_name}</strong><time className={`text-[10px] ${muted}`}>{clock(message.created_at)}</time></div><p className="text-sm mt-1 whitespace-pre-wrap break-words leading-relaxed">{message.content}</p></div>
            </article>
          ))}
          <div ref={bottomRef} />
        </div>
        {error && <p role="alert" className="text-xs text-red-400 px-5 pb-2">{error}</p>}
        <form onSubmit={send} className={`shrink-0 mx-5 mb-5 rounded-xl border p-2 flex items-center gap-2 ${panel}`}>
          <input value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={5000} disabled={!selected || sending}
            aria-label="메시지 입력" placeholder={selected ? `${title}에 메시지 보내기` : "대화를 선택해 주세요"}
            className="flex-1 min-w-0 bg-transparent border-0 outline-none px-2 py-1 text-sm placeholder:text-slate-500" />
          <button type="submit" disabled={!selected || !draft.trim() || sending} aria-label="메시지 보내기" className="w-8 h-8 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white grid place-items-center"><ArrowUp size={16} /></button>
        </form>
      </section>
    </div>
  );
}
