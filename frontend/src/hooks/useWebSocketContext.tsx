import React, { createContext, useContext, type ReactNode } from "react";
import { useWebSocket } from "./useWebSocket";

interface WebSocketContextValue {
  sendStreamPrompt: (payload: {
    prompt: string;
    model: string;
    provider: string;
    conversation_id?: string | null;
    file_context?: string;
  }) => boolean;
  isStreamWsReady: () => boolean;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

/** Provider that initialises WebSocket connections once and shares the API. */
export function WebSocketProvider({ children }: { children: ReactNode }) {
  const ws = useWebSocket();
  return (
    <WebSocketContext.Provider value={ws}>{children}</WebSocketContext.Provider>
  );
}

/** Hook to access the shared WebSocket API from any child component. */
export function useWebSocketContext(): WebSocketContextValue {
  const ctx = useContext(WebSocketContext);
  if (!ctx) {
    throw new Error("useWebSocketContext must be used inside <WebSocketProvider>");
  }
  return ctx;
}
