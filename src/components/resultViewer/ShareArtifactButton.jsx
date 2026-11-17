import React, { useEffect, useState } from "react";
import { Share2, X } from "lucide-react";
import useAppStore from "../../store/useAppStore";
import { request } from "../../api/apiClient";
import { serverRequest } from "../../api/serverClient";
import { artifactMarker } from "../chat/artifactShare";

export default function ShareArtifactButton({ kind, runId }) {
  const backendPort = useAppStore((s) => s.backendPort);
  const authToken = useAppStore((s) => s.authToken);
  const currentUser = useAppStore((s) => s.currentUser);
  const addNotification = useAppStore((s) => s.addNotification);
  const [open, setOpen] = useState(false);
  const [targets, setTargets] = useState([]);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [snapshotId, setSnapshotId] = useState("");
  const label = kind === "components" ? "컴포넌트" : "API Spec";
  const headers = { Authorization: `Bearer ${authToken}` };

  useEffect(() => { setSnapshotId(""); }, [kind, runId]);

  const showTargets = async () => {
    setOpen(true);
    setError("");
    try {
      const [channelData, memberData] = await Promise.all([
        serverRequest("/messages/channels", { headers }),
        serverRequest(`/auth/teams/${currentUser.team_id}/members`, { headers }),
      ]);
      const options = [
        ...(channelData.channels || []).map((channel) => ({ value: `channel:${channel.id}`, label: `# ${channel.name}` })),
        ...(memberData.members || []).filter((member) => member.id !== currentUser.id)
          .map((member) => ({ value: `dm:${member.id}`, label: `1:1 ${member.name || member.email}` })),
      ];
      setTargets(options);
      setTarget(options[0]?.value || "");
      if (!options.length) setError("공유할 채널이나 팀원이 없습니다.");
    } catch (cause) {
      setError(cause.message || "공유 대상을 불러오지 못했습니다.");
    }
  };

  const share = async () => {
    if (!target || busy) return;
    setBusy(true);
    setError("");
    try {
      let id = snapshotId;
      if (!id) {
        const response = await request(backendPort, "/api/share-artifact", {
          method: "POST", body: JSON.stringify({ run_id: runId, kind }),
        });
        if (response.status !== "ok" || !response.data?.id) throw new Error("공유 결과를 저장하지 못했습니다.");
        id = response.data.id;
        setSnapshotId(id);
      }
      const [type, targetId] = target.split(":");
      await serverRequest(type === "channel" ? `/messages/channels/${targetId}/messages` : "/messages", {
        method: "POST", headers,
        body: JSON.stringify(type === "channel"
          ? { content: artifactMarker(kind, id) }
          : { receiver_id: targetId, content: artifactMarker(kind, id) }),
      });
      setOpen(false);
      setSnapshotId("");
      addNotification(`${label}를 팀 채팅에 공유했습니다.`, "success");
    } catch (cause) {
      setError(cause.message || "공유하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return <>
    <button type="button" onClick={showTargets} disabled={!runId || !currentUser?.team_id}
      title={!runId ? "저장된 분석 결과에서 공유할 수 있습니다." : undefined}
      className="flex items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40">
      <Share2 size={15} /> 팀 채팅에 공유
    </button>
    {open && <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4" role="dialog" aria-modal="true" aria-label={`${label} 공유`}>
      <div className="w-full max-w-sm rounded-xl border border-slate-700 bg-slate-900 p-5 text-white shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-semibold">{label} 공유</h2>
          <button type="button" onClick={() => setOpen(false)} aria-label="닫기"><X size={18} /></button>
        </div>
        <label className="block text-sm" htmlFor="artifact-share-target">공유할 채널 또는 팀원</label>
        <select id="artifact-share-target" value={target} onChange={(event) => setTarget(event.target.value)}
          className="mt-2 w-full rounded-lg border border-slate-600 bg-slate-800 p-2 text-sm text-white">
          {targets.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        {error && <p role="alert" className="mt-3 text-xs text-red-400">{error}</p>}
        <button type="button" onClick={share} disabled={!target || busy}
          className="mt-5 w-full rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium hover:bg-blue-500 disabled:opacity-40">
          {busy ? "공유 중..." : "공유하기"}
        </button>
      </div>
    </div>}
  </>;
}
