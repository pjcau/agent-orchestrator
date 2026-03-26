import { useEffect, useRef, useCallback } from "react";
import { useAppStore } from "@/stores/useAppStore";
import { useModels, useNewConversation } from "@/api/hooks";
import { useWebSocketContext } from "@/hooks/useWebSocketContext";
import { ChatMessageItem } from "./ChatMessage";
import { StreamingMessage } from "./StreamingMessage";
import { ChatInput, type ExecMode } from "./ChatInput";
import apiClient from "@/api/client";
import type { ChatMessage } from "@/api/types";

export function ChatPanel() {
  const {
    messages,
    isStreaming,
    streamBuffer,
    conversationId,
    addMessage,
    setConversationId,
    setPendingTeamJob,
  } = useAppStore();

  const { data: models } = useModels();
  const newConversation = useNewConversation();
  const { sendStreamPrompt, isStreamWsReady } = useWebSocketContext();
  const chatBottomRef = useRef<HTMLDivElement>(null);
  const isRunning = useAppStore((s) => s.isStreaming);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isStreaming, streamBuffer]);

  const handleNewChat = useCallback(async () => {
    try {
      const resp = await newConversation.mutateAsync();
      setConversationId(resp.conversation_id);
      useAppStore.getState().clearMessages();
      addMessage({ role: "system", content: "New conversation started", timestamp: Date.now() });
    } catch (err) {
      console.error("Failed to start new conversation:", err);
    }
  }, [newConversation, setConversationId, addMessage]);

  const handleSend = useCallback(
    async (opts: {
      text: string;
      mode: ExecMode;
      model: string;
      provider: "openrouter" | "ollama";
      useStreaming: boolean;
      fileContext: string;
    }) => {
      const { text, mode, model, provider, useStreaming, fileContext } = opts;

      // Add user message to chat
      addMessage({ role: "user", content: text, timestamp: Date.now() });
      useAppStore.setState({ isStreaming: true });

      try {
        if (mode === "multi-agent") {
          // Team run — async, WS events will handle rendering
          const resp = await apiClient.post<{ job_id: string; error?: string }>(
            "/api/team/run",
            {
              task: text,
              model,
              provider,
              conversation_id: conversationId,
            }
          );
          if (resp.data.job_id) {
            setPendingTeamJob(resp.data.job_id, model);
            addMessage({
              role: "system",
              content: `Team started (job ${resp.data.job_id}). Streaming results...`,
              timestamp: Date.now(),
            });
          } else {
            throw new Error(resp.data.error ?? "No job_id returned");
          }
        } else if (mode === "agent") {
          // Single agent run
          addMessage({ role: "system", content: "Running single agent...", timestamp: Date.now() });
          const resp = await apiClient.post<{
            success: boolean;
            output?: string;
            error?: string;
            total_tokens?: number;
            elapsed_s?: number;
          }>("/api/agent/run", {
            agent: "team-lead",
            task: text,
            model,
            provider,
            conversation_id: conversationId,
          });

          if (resp.data.success) {
            addMessage({
              role: "assistant",
              content: {
                steps: [{ node: "agent", output: resp.data.output ?? "" }],
                usage: { output_tokens: resp.data.total_tokens, model },
                elapsed_s: resp.data.elapsed_s,
              } as import("@/api/types").AssistantContent,
              timestamp: Date.now(),
            } as ChatMessage);
          } else {
            addMessage({
              role: "assistant",
              content: `Agent error: ${resp.data.error ?? "Failed"}`,
              timestamp: Date.now(),
            });
          }
          useAppStore.setState({ isStreaming: false });
        } else {
          // Simple prompt
          if (useStreaming && isStreamWsReady()) {
            // WebSocket streaming
            sendStreamPrompt({
              prompt: fileContext ? `${text}\n\n\`\`\`\n${fileContext}\n\`\`\`` : text,
              model,
              provider,
              conversation_id: conversationId,
              file_context: fileContext,
            });
            // isStreaming stays true until stream finishes
          } else {
            // Non-streaming graph prompt
            const resp = await apiClient.post<{
              success: boolean;
              output?: string;
              error?: string;
              usage?: { input_tokens?: number; output_tokens?: number; model?: string };
              elapsed_s?: number;
            }>("/api/prompt", {
              prompt: fileContext ? `${text}\n\n\`\`\`\n${fileContext}\n\`\`\`` : text,
              model,
              provider,
              graph_type: "chat",
              conversation_id: conversationId,
              file_context: fileContext,
            });

            if (resp.data.success) {
              addMessage({
                role: "assistant",
                content: resp.data.output ?? "",
                model: resp.data.usage?.model,
                timestamp: Date.now(),
              });
            } else {
              addMessage({
                role: "assistant",
                content: `Error: ${resp.data.error ?? "Unknown error"}`,
                timestamp: Date.now(),
              });
            }
            useAppStore.setState({ isStreaming: false });
          }
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addMessage({ role: "assistant", content: `Request failed: ${msg}`, timestamp: Date.now() });
        useAppStore.setState({ isStreaming: false });
      }
    },
    [addMessage, conversationId, sendStreamPrompt, isStreamWsReady, setPendingTeamJob]
  );

  return (
    <div className="chat-panel">
      <div className="chat-panel__messages" role="log" aria-live="polite">
        {messages.length === 0 && (
          <div className="chat-panel__empty">
            <p>Send a message to start</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <ChatMessageItem
            key={i}
            message={msg as ChatMessage & {
              hitlType?: "options" | "interrupt";
              options?: string[];
              runId?: string;
              streaming?: boolean;
            }}
          />
        ))}
        {isStreaming && streamBuffer && (
          <StreamingMessage buffer={streamBuffer} />
        )}
        <div ref={chatBottomRef} />
      </div>

      <ChatInput
        models={models}
        isDisabled={isRunning}
        onSend={handleSend}
        onNewChat={handleNewChat}
      />
    </div>
  );
}
