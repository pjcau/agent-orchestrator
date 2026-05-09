import { useState } from "react";
import { useModels } from "@/api/hooks";
import { useAppStore } from "@/stores/useAppStore";
import apiClient from "@/api/client";
import type { PromptResponse } from "@/api/types";

/** Detect provider from model name (mirrors vanilla detectProvider). */
function detectProvider(modelName: string): "openrouter" | "ollama" {
  return modelName.includes("/") ? "openrouter" : "ollama";
}

interface CompareResult {
  output: string;
  tokensPerSec: string;
  elapsedS: number;
}

/**
 * Side-by-side model comparison panel.
 * Ported from vanilla app.js runComparison() (lines 991-1029).
 */
export function ComparePanel() {
  const { data: models } = useModels();
  const messages = useAppStore((s) => s.messages);

  const allModels = [
    ...(models?.openrouter ?? []),
    ...(models?.ollama ?? []),
  ];

  const [modelA, setModelA] = useState("");
  const [modelB, setModelB] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resultA, setResultA] = useState<CompareResult | null>(null);
  const [resultB, setResultB] = useState<CompareResult | null>(null);

  const lastUserMsg =
    [...messages].reverse().find((m) => m.role === "user")?.content ?? "";
  const prompt =
    typeof lastUserMsg === "string" ? lastUserMsg : "";

  const handleRun = async () => {
    if (!modelA || !modelB) {
      setError("Select 2 models.");
      return;
    }
    if (!prompt) {
      setError("Send a message first — the last user message will be compared.");
      return;
    }
    setError(null);
    setResultA(null);
    setResultB(null);
    setRunning(true);

    try {
      const [respA, respB] = await Promise.all([
        apiClient.post<PromptResponse>("/api/prompt", {
          prompt,
          model: modelA,
          provider: detectProvider(modelA),
          graph_type: "chat",
        }),
        apiClient.post<PromptResponse>("/api/prompt", {
          prompt,
          model: modelB,
          provider: detectProvider(modelB),
          graph_type: "chat",
        }),
      ]);

      const makeResult = (resp: PromptResponse): CompareResult => {
        const tokens = resp.usage?.output_tokens ?? 0;
        const elapsed = resp.elapsed_s ?? 0;
        const tokPerSec =
          elapsed > 0 ? (tokens / elapsed).toFixed(1) : "-";
        return {
          output: resp.output ?? resp.error ?? "(no output)",
          tokensPerSec: tokPerSec,
          elapsedS: elapsed,
        };
      };

      setResultA(makeResult(respA.data));
      setResultB(makeResult(respB.data));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="compare-panel">
      <div className="compare-controls">
        <select
          className="compare-select"
          value={modelA}
          onChange={(e) => setModelA(e.target.value)}
          disabled={running}
          aria-label="Model A"
        >
          <option value="">Model A</option>
          {allModels.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
            </option>
          ))}
        </select>

        <select
          className="compare-select"
          value={modelB}
          onChange={(e) => setModelB(e.target.value)}
          disabled={running}
          aria-label="Model B"
        >
          <option value="">Model B</option>
          {allModels.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
            </option>
          ))}
        </select>

        <button
          className="btn-primary"
          onClick={handleRun}
          disabled={running || !modelA || !modelB}
        >
          {running ? "Running..." : "Go"}
        </button>
      </div>

      {error && (
        <div className="compare-error empty-state">{error}</div>
      )}

      {running && !resultA && (
        <div className="empty-state">Running comparison...</div>
      )}

      {(resultA || resultB) && (
        <div className="compare-results">
          {resultA && (
            <div className="compare-col">
              <div className="compare-model-label">{modelA}</div>
              <div className="compare-stats">
                {resultA.tokensPerSec} tok/s &middot; {resultA.elapsedS}s
              </div>
              <pre className="compare-output">{resultA.output}</pre>
            </div>
          )}
          {resultB && (
            <div className="compare-col">
              <div className="compare-model-label">{modelB}</div>
              <div className="compare-stats">
                {resultB.tokensPerSec} tok/s &middot; {resultB.elapsedS}s
              </div>
              <pre className="compare-output">{resultB.output}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
