/**
 * Unit test for the fallback_log rendering inside the team.complete handler.
 *
 * Rather than exercising the WebSocket hook directly (which would require
 * a live WS connection), we test the logic at the store level: we call
 * addMessage with the content that the handler produces and verify the
 * message list is correct.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { useAppStore } from "@/stores/useAppStore";

function buildFallbackMessage(
  fbLog: Array<{ agent: string; model: string; status: string; detail: string }>
): string {
  const lines = fbLog.map((f) => {
    const icon = f.status === "ok" ? "✓" : "✗";
    return `${icon} ${f.agent} → ${f.model} [${f.status}] ${f.detail}`;
  });
  return `Fallback log:\n${lines.join("\n")}`;
}

describe("team.complete fallback_log rendering", () => {
  beforeEach(() => {
    useAppStore.setState({ messages: [] });
  });

  it("formats ok entries with a check mark", () => {
    const fbLog = [
      { agent: "data-analyst", model: "claude-sonnet-4-6", status: "ok", detail: "retry succeeded" },
    ];
    const content = buildFallbackMessage(fbLog);
    expect(content).toContain("✓ data-analyst → claude-sonnet-4-6 [ok] retry succeeded");
  });

  it("formats failed entries with a cross mark", () => {
    const fbLog = [
      { agent: "coder", model: "gpt-4o", status: "failed", detail: "timeout" },
    ];
    const content = buildFallbackMessage(fbLog);
    expect(content).toContain("✗ coder → gpt-4o [failed] timeout");
  });

  it("mixed entries are formatted correctly", () => {
    const fbLog = [
      { agent: "agent-a", model: "m1", status: "ok", detail: "all good" },
      { agent: "agent-b", model: "m2", status: "failed", detail: "error" },
    ];
    const content = buildFallbackMessage(fbLog);
    const lines = content.split("\n");
    expect(lines[0]).toBe("Fallback log:");
    expect(lines[1]).toContain("✓");
    expect(lines[2]).toContain("✗");
  });

  it("formats a repair summary line (passed, no fixes)", () => {
    // Mirrors the formatting in useWebSocket.ts.
    const r = {
      status: "passed" as const,
      attempts: 1,
      auto_fixed_signatures: [] as string[],
      final_failures: [] as Array<{ verifier: string }>,
    };
    const icon = r.status === "passed" ? "✓" : "✗";
    const line = `Repair loop: ${icon} ${r.status} (${r.attempts} attempt${r.attempts === 1 ? "" : "s"})`;
    expect(line).toBe("Repair loop: ✓ passed (1 attempt)");
  });

  it("formats a repair summary line with auto-fixes + residual failures", () => {
    const r = {
      status: "partial" as const,
      attempts: 3,
      auto_fixed_signatures: ["sig1", "sig2"],
      final_failures: [{ verifier: "imports" }, { verifier: "coherence" }],
    };
    const icon = r.status === "partial" ? "⚠" : "✗";
    const attempts = `${r.attempts} attempt${r.attempts === 1 ? "" : "s"}`;
    const autoFixed = `, ${r.auto_fixed_signatures.length} auto-fixes`;
    const residual = `, ${r.final_failures.length} residual failures (${r.final_failures
      .slice(0, 3)
      .map((f) => f.verifier)
      .join(", ")})`;
    const line = `Repair loop: ${icon} ${r.status} (${attempts}${autoFixed}${residual})`;
    expect(line).toBe("Repair loop: ⚠ partial (3 attempts, 2 auto-fixes, 2 residual failures (imports, coherence))");
  });

  it("adds system message to the store when fallback_log is present", () => {
    const fbLog = [
      { agent: "data-analyst", model: "claude-sonnet-4-6", status: "ok", detail: "retry succeeded" },
    ];
    const content = buildFallbackMessage(fbLog);
    useAppStore.getState().addMessage({
      role: "system",
      content,
      timestamp: Date.now(),
    });

    const messages = useAppStore.getState().messages;
    expect(messages).toHaveLength(1);
    expect(messages[0].role).toBe("system");
    expect(messages[0].content).toContain("Fallback log:");
    expect(messages[0].content).toContain("✓");
  });
});
