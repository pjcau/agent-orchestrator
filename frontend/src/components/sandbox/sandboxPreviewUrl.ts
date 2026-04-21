/**
 * Build the preview URL for a sandbox port.
 *
 * Port validation is a security boundary: values flow from DOM input
 * into an ``<iframe src>``, so any non-numeric input must be rejected
 * (CodeQL js/xss-through-dom). The function returns ``about:blank`` on
 * any invalid input instead of embedding user-controlled data.
 */
export function sandboxPreviewUrl(hostPort: string): string {
  // Strict allowlist: port must be digits only, 1-65535.
  if (!/^\d{1,5}$/.test(hostPort)) {
    return "about:blank";
  }
  const portNum = Number(hostPort);
  if (portNum < 1 || portNum > 65535) {
    return "about:blank";
  }
  const safePort = String(portNum);
  const isDev =
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1";
  if (isDev) {
    return `http://localhost:${safePort}`;
  }
  // Production: proxy through nginx to avoid exposing extra ports.
  return `${window.location.origin}/sandbox-preview/${safePort}/`;
}
