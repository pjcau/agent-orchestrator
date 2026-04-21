import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { sandboxPreviewUrl } from "./sandboxPreviewUrl";

/**
 * Unit tests for the sandbox preview URL builder.
 *
 * Locks in the js/xss-through-dom sanitizer: any non-numeric or
 * out-of-range port input must return ``about:blank`` instead of
 * producing an iframe URL that embeds user-controlled data.
 */

function setHostname(hostname: string, origin: string) {
  // jsdom lets us mutate window.location directly for tests.
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { hostname, origin } as Location,
  });
}

describe("sandboxPreviewUrl — dev (localhost)", () => {
  beforeEach(() => setHostname("localhost", "http://localhost:3001"));
  afterEach(() => setHostname("localhost", "http://localhost:3001"));

  it("builds a direct http://localhost URL for a valid port", () => {
    expect(sandboxPreviewUrl("9001")).toBe("http://localhost:9001");
  });

  it("accepts the lowest valid port (1)", () => {
    expect(sandboxPreviewUrl("1")).toBe("http://localhost:1");
  });

  it("accepts the highest valid port (65535)", () => {
    expect(sandboxPreviewUrl("65535")).toBe("http://localhost:65535");
  });
});

describe("sandboxPreviewUrl — production", () => {
  beforeEach(() => setHostname("agents-orchestrator.com", "https://agents-orchestrator.com"));

  it("uses the nginx proxy path", () => {
    expect(sandboxPreviewUrl("9002")).toBe(
      "https://agents-orchestrator.com/sandbox-preview/9002/",
    );
  });

  it("127.0.0.1 hostname falls back to the dev branch (points at localhost)", () => {
    setHostname("127.0.0.1", "http://127.0.0.1");
    // Dev branch hard-codes "localhost" regardless of whether the
    // browser is running on 127.0.0.1; both resolve to the same host.
    expect(sandboxPreviewUrl("9002")).toBe("http://localhost:9002");
  });
});

describe("sandboxPreviewUrl — sanitizer", () => {
  beforeEach(() => setHostname("localhost", "http://localhost:3001"));

  it.each([
    "",
    "abc",
    "80a",
    " 80",
    "80 ",
    "-1",
    "0",
    "65536",
    "999999",
    "1.5",
    "1,2",
    "1/2",
  ])("rejects invalid port %j with about:blank", (bad) => {
    expect(sandboxPreviewUrl(bad)).toBe("about:blank");
  });

  it("rejects an injection payload attempting to break out of the URL", () => {
    const attack = "80/evil.com?x=<script>";
    expect(sandboxPreviewUrl(attack)).toBe("about:blank");
  });

  it("rejects a javascript: scheme payload", () => {
    expect(sandboxPreviewUrl("javascript:alert(1)")).toBe("about:blank");
  });

  it("rejects CRLF payload", () => {
    expect(sandboxPreviewUrl("80\r\nX-Injected: yes")).toBe("about:blank");
  });

  it("rejects unicode digits (non-ASCII)", () => {
    // Arabic-Indic digit 9 — not allowed by the ASCII-only regex.
    expect(sandboxPreviewUrl("\u0669")).toBe("about:blank");
  });
});
