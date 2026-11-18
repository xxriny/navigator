import { request } from "../apiClient";

export const publishService = {
  /** 로컬 분석 결과 목록 (Publish 전 선택용) */
  async listLocalResults(port, limit = 50, authToken = "") {
    return request(port, `/api/local-results?limit=${limit}`, {
      headers: { Authorization: `Bearer ${authToken}` },
    });
  },

  /** 로컬 결과를 공유 DB에 Publish */
  async publish(port, { runId, title, description = "", teamId = null }, authToken) {
    return request(port, "/api/publish", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      },
      body: JSON.stringify({ run_id: runId, title, description, team_id: teamId }),
    });
  },

  /** 팀 공유 스냅샷 목록 */
  async listSnapshots(port, { teamId = null, limit = 30, offset = 0 } = {}, authToken) {
    const params = new URLSearchParams({ limit, offset });
    if (teamId) params.append("team_id", teamId);
    return request(port, `/api/snapshots?${params}`, {
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
    });
  },

  /** 스냅샷 상세 (데이터 포함) */
  async getSnapshot(port, snapshotId, authToken) {
    return request(port, `/api/snapshots/${snapshotId}`, {
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
    });
  },

  /** 스냅샷 삭제 */
  async deleteSnapshot(port, snapshotId, authToken) {
    return request(port, `/api/snapshots/${snapshotId}`, {
      method: "DELETE",
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
    });
  },

  /** 스냅샷을 로컬 결과(run_id)로 Pull (덮어쓰기) */
  async pullSnapshot(port, snapshotId, runId, authToken) {
    return request(port, `/api/snapshots/${snapshotId}/pull`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      },
      body: JSON.stringify({ run_id: runId }),
    });
  },
};

