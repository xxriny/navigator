import { create } from "zustand";
import { debounce } from "./debounce";
import { createUiSlice } from "./slices/uiSlice";
import { createFileSlice } from "./slices/fileSlice";
import { createPipelineSlice } from "./slices/pipelineSlice";
import { createSessionSlice } from "./slices/sessionSlice";
import { createWsSlice } from "./slices/wsSlice";
import { createConfigSlice } from "./slices/configSlice";
import { createNotificationSlice } from "./slices/notificationSlice";
import { createAuthSlice } from "./slices/authSlice";
import { createGithubSlice } from "./slices/githubSlice";
import { createPublishSlice } from './slices/publishSlice';
import { apiBaseUrl } from '../api/apiClient';

/**
 * NAVIGATOR — Global Store (Zustand)
 * Slice Pattern을 사용하여 기능별로 모듈화됨.
 */
const useAppStore = create((set, get) => {
  const debouncedSave = debounce(() => get().saveCurrentSession(), 500);

  // 상태 변경 시마다 자동 저장 트리거
  const setWithSave = (partial, replace) => {
    set(partial, replace);
    debouncedSave();
  };

  return {
    ...createUiSlice(setWithSave, get),
    ...createFileSlice(setWithSave, get),
    ...createPipelineSlice(setWithSave, get),
    ...createSessionSlice(setWithSave, get),
    ...createWsSlice(setWithSave, get),
    ...createConfigSlice(setWithSave, get),
    ...createNotificationSlice(setWithSave, get),
    ...createAuthSlice(setWithSave, get),
    ...createGithubSlice(setWithSave, get),
    ...createPublishSlice(setWithSave, get),

    deleteSession: async (id) => {
      const { backendPort, sessions, currentSessionId } = get();
      const session = sessions.find((s) => s.id === id);
      const runId = session?.resultData?.run_id;

      if (backendPort && runId) {
        try {
          await fetch(`${apiBaseUrl(backendPort)}/api/session/${runId}`, { method: "DELETE", headers: get().getAuthHeader() });
        } catch (e) { console.error("[DeleteSession] API Failed:", e); }
      }

      setWithSave((state) => {
        const nextSessions = state.sessions.filter((s) => s.id !== id);
        return {
          sessions: nextSessions,
          currentSessionId: currentSessionId === id ? null : currentSessionId
        };
      });
    },
  };
});

export default useAppStore;
