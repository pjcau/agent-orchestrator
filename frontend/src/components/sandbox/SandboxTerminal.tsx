import { useEffect, useRef, useState } from "react";

/**
 * Interactive terminal connected to a sandbox container via WebSocket.
 * Uses a simple <pre> + <input> interface (no xterm.js dependency needed).
 * Connects to WS /ws/sandbox/{sessionId}/terminal.
 */
export function SandboxTerminal({ sessionId }: { sessionId: string }) {
  const [output, setOutput] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const outputRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (!sessionId) return;

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const apiKey = localStorage.getItem("apiKey") ?? "";
    const url = `${proto}//${window.location.host}/ws/sandbox/${encodeURIComponent(sessionId)}/terminal?api_key=${encodeURIComponent(apiKey)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setOutput(["Connected to sandbox terminal.\n"]);
    };

    ws.onmessage = (e) => {
      setOutput((prev) => [...prev.slice(-1000), e.data]);
    };

    ws.onclose = () => {
      setConnected(false);
      setOutput((prev) => [...prev, "\n[Disconnected]\n"]);
    };

    ws.onerror = () => {
      setConnected(false);
    };

    return () => {
      ws.close();
    };
  }, [sessionId]);

  // Auto-scroll to bottom
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [output]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(input + "\n");
    setInput("");
  };

  return (
    <div className="sandbox-terminal">
      <div className="sandbox-terminal__header">
        <span
          className={`sandbox-terminal__dot sandbox-terminal__dot--${connected ? "on" : "off"}`}
        />
        <span className="sandbox-terminal__title">
          {connected ? "Terminal" : "Disconnected"}
        </span>
      </div>
      <pre className="sandbox-terminal__output" ref={outputRef}>
        {output.join("")}
      </pre>
      <form className="sandbox-terminal__input-row" onSubmit={handleSubmit}>
        <span className="sandbox-terminal__prompt">$</span>
        <input
          className="sandbox-terminal__input"
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={connected ? "Type a command..." : "Not connected"}
          disabled={!connected}
          autoFocus
        />
      </form>
    </div>
  );
}
