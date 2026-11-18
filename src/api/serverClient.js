/**
 * serverClient — Cloud Run navigator-server 전용 HTTP 클라이언트
 */

export const SERVER_URL = import.meta.env.VITE_SERVER_URL || "https://navigator-server-640700885251.asia-northeast3.run.app";

export async function serverRequest(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30_000);
  const url = `${SERVER_URL}${path}`;
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });

    if (response.status === 401) {
      const { default: useAppStore } = await import("../store/useAppStore.js");
      useAppStore.getState().clearAuth();
      throw new Error("세션이 만료되었습니다. 다시 로그인해주세요.");
    }

    if (!response.ok) {
      const errorBody = await response.json().catch(() => ({}));
      throw new Error(errorBody.detail || errorBody.message || `Server Error: ${response.status}`);
    }

    return response.json();
  } catch (e) {
    if (e.name === "AbortError") throw new Error("요청 시간이 초과되었습니다.");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}
