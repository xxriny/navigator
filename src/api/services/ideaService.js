import { request } from "../apiClient";

export const ideaService = {
  /**
   * Pre-flight 요약 추출 — 날것의 chat_history + 미반영 memos를 보내서
   * 노이즈(번복·취소된 발화)를 걸러낸 최종 확정 요구사항 마크다운만 받는다.
   * 응답: { status, summary_markdown, dropped_points, error? }
   */
  async extractFinalIdea(port, payload) {
    return request(port, "/api/extract-final-idea", {
      method: "POST",
      headers: { Authorization: `Bearer ${payload.auth_token || ""}` },
      body: JSON.stringify(payload),
    });
  },
};
