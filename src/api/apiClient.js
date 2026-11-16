/**
 * API Client — HTTP 요청 공통 처리 및 베이스 설정
 */

const DEFAULT_BACKEND_PORT = 8765;

function stripTrailingSlash(value) {
  return value ? value.replace(/\/+$/, "") : "";
}

export function apiBaseUrl(port = DEFAULT_BACKEND_PORT) {
  const configured = stripTrailingSlash(import.meta.env.VITE_API_BASE_URL || "");
  if (configured) return configured;

  if (typeof window !== "undefined" && !window.electronAPI) {
    return window.location.origin;
  }

  return `http://127.0.0.1:${port || DEFAULT_BACKEND_PORT}`;
}

export function apiUrl(path, port) {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${apiBaseUrl(port)}${normalizedPath}`;
}

export function wsUrl(path, port = DEFAULT_BACKEND_PORT) {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const configured = stripTrailingSlash(import.meta.env.VITE_WS_BASE_URL || "");
  if (configured) return `${configured}${normalizedPath}`;

  if (typeof window !== "undefined" && !window.electronAPI) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}${normalizedPath}`;
  }

  return `ws://127.0.0.1:${port || DEFAULT_BACKEND_PORT}${normalizedPath}`;
}

export async function request(port, path, options = {}) {
  const url = apiUrl(path, port);
  const { default: useAppStore } = await import("../store/useAppStore.js");
  const token = useAppStore.getState().authToken;
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(errorBody.message || `API Error: ${response.status}`);
  }

  return response.json();
}
