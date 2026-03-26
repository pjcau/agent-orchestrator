import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from "axios";

// Base URL — empty string means same origin (works with Vite proxy in dev and direct in prod)
const BASE_URL = "";

function getApiKey(): string | null {
  try {
    return localStorage.getItem("api_key");
  } catch {
    return null;
  }
}

export function setApiKey(key: string): void {
  try {
    localStorage.setItem("api_key", key);
  } catch {
    // Ignore storage errors
  }
}

export function clearApiKey(): void {
  try {
    localStorage.removeItem("api_key");
  } catch {
    // Ignore storage errors
  }
}

const apiClient: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 120_000, // 2 minutes — team runs can be long
  headers: {
    "Content-Type": "application/json",
  },
  withCredentials: true, // Send session cookies for OAuth auth
});

// Request interceptor — inject API key header if available
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const apiKey = getApiKey();
    if (apiKey) {
      config.headers = config.headers ?? {};
      config.headers["X-API-Key"] = apiKey;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor — handle auth errors globally
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Session expired or not authenticated — could redirect to login
      console.warn("Authentication required");
    }
    return Promise.reject(error);
  }
);

export default apiClient;

// Convenience helpers
export function buildWsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const apiKey = getApiKey();
  const query = apiKey ? `?api_key=${encodeURIComponent(apiKey)}` : "";
  return `${proto}//${location.host}${path}${query}`;
}
