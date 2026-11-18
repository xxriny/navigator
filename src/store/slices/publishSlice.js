import { publishService } from "../../api/services/publishService";

export const createPublishSlice = (set, get) => ({
  // ── 상태 ─────────────────────────────────────────────────
  localResults: [],          // Publish 대상 후보 목록
  snapshots: [],             // 팀 공유 스냅샷 목록
  activeSnapshot: null,      // 현재 열람 중인 스냅샷 (상세)
  publishLoading: false,
  snapshotsLoading: false,
  publishError: null,

  // ── 로컬 결과 목록 로드 ───────────────────────────────────
  loadLocalResults: async () => {
    const { backendPort, authToken } = get();
    if (!backendPort) return;
    try {
      const res = await publishService.listLocalResults(backendPort, 50, authToken);
      if (res.status === "ok") set({ localResults: res.data });
    } catch (e) {
      console.error("[publishSlice] loadLocalResults failed", e);
    }
  },

  // ── Publish ───────────────────────────────────────────────
  publishResult: async ({ runId, title, description }) => {
    const { backendPort, authToken, currentUser } = get();
    set({ publishLoading: true, publishError: null });
    try {
      const teamId = currentUser?.team_id || null;
      const res = await publishService.publish(
        backendPort,
        { runId, title, description, teamId },
        authToken,
      );
      if (res.status === "ok") {
        // 목록 새로고침 (병렬)
        await Promise.all([get().loadSnapshots(), get().loadLocalResults()]);
        return { success: true, data: res.data };
      }
      set({ publishError: res.error || "Publish 실패" });
      return { success: false, error: res.error };
    } catch (e) {
      set({ publishError: String(e) });
      return { success: false, error: String(e) };
    } finally {
      set({ publishLoading: false });
    }
  },

  // ── 스냅샷 목록 ───────────────────────────────────────────
  loadSnapshots: async () => {
    const { backendPort, authToken, currentUser } = get();
    if (!backendPort) return;
    set({ snapshotsLoading: true });
    try {
      const teamId = currentUser?.team_id || null;
      const res = await publishService.listSnapshots(backendPort, { teamId }, authToken);
      if (res.status === "ok") set({ snapshots: res.data });
    } catch (e) {
      console.error("[publishSlice] loadSnapshots failed", e);
    } finally {
      set({ snapshotsLoading: false });
    }
  },

  // ── 스냅샷 상세 ───────────────────────────────────────────
  openSnapshot: async (snapshotId) => {
    const { backendPort, authToken } = get();
    set({ activeSnapshot: null, snapshotsLoading: true });
    try {
      const res = await publishService.getSnapshot(backendPort, snapshotId, authToken);
      if (res.status === "ok") set({ activeSnapshot: res.data });
    } catch (e) {
      console.error("[publishSlice] openSnapshot failed", e);
    } finally {
      set({ snapshotsLoading: false });
    }
  },

  closeSnapshot: () => set({ activeSnapshot: null }),

  // ── 스냅샷 삭제 ───────────────────────────────────────────
  deleteSnapshot: async (snapshotId) => {
    const { backendPort, authToken } = get();
    try {
      const res = await publishService.deleteSnapshot(backendPort, snapshotId, authToken);
      if (res.status === "ok") {
        set((s) => ({ snapshots: s.snapshots.filter((x) => x.id !== snapshotId) }));
        if (get().activeSnapshot?.id === snapshotId) set({ activeSnapshot: null });
        return true;
      }
    } catch (e) {
      console.error("[publishSlice] deleteSnapshot failed", e);
    }
    return false;
  },

  // ── Pull Snapshot ───────────────────────────────────────────
  pullSnapshot: async (snapshotId, runId) => {
    const { backendPort, authToken } = get();
    set({ publishLoading: true, publishError: null });
    try {
      const res = await publishService.pullSnapshot(backendPort, snapshotId, runId, authToken);
      if (res.status === "ok") {
        // 1. 결과 데이터를 store에 반영
        if (res.data && res.data.shaped_result) {
          get()._processResult(res.data.shaped_result);
          // _processResult의 memo 탭 이동을 override하고, SharedTab 패널도 닫아 overview로 전환
          set({ activeViewportTab: { kind: "output", id: "overview" }, activeIconPanel: null });
        }

        // 2. 로컬 결과 목록 새로고침 및 세션 영속화
        await get().loadLocalResults();
        if (get().saveCurrentSession) {
          get().saveCurrentSession();
        }

        return { success: true, data: res.data };
      }
      set({ publishError: res.error || "Pull 실패" });
      return { success: false, error: res.error };
    } catch (e) {
      set({ publishError: String(e) });
      return { success: false, error: String(e) };
    } finally {
      set({ publishLoading: false });
    }
  },
});

