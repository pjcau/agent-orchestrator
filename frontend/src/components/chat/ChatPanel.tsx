import { useEffect, useRef, useCallback, useState } from "react";
import { useAppStore } from "@/stores/useAppStore";
import { useModels, useNewConversation } from "@/api/hooks";
import { useWebSocketContext } from "@/hooks/useWebSocketContext";
import { ChatMessageItem } from "./ChatMessage";
import { StreamingMessage } from "./StreamingMessage";
import { ChatInput, type ExecMode } from "./ChatInput";
import { PresetsBar } from "@/components/prompts/PresetsBar";
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

  // Preset text injection: when a preset is applied, we store it and pass it
  // down to ChatInput so it can set its textarea value.
  const [presetText, setPresetText] = useState<string | null>(null);
  // fileContext is tracked here so PresetsBar can use it for {context} substitution.
  const [fileContext, setFileContext] = useState("");

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
      ragEnabled: boolean;
      ragNamespace: string;
    }) => {
      const { text, mode, model, provider, useStreaming, fileContext, ragEnabled, ragNamespace } = opts;

      // Auto-create a conversation on first send so multi-turn memory works
      // without the user having to click "New Chat" first. The id is then
      // persisted in localStorage by setConversationId.
      let activeConvId = conversationId;
      if (!activeConvId) {
        try {
          const created = await newConversation.mutateAsync();
          activeConvId = created.conversation_id;
          setConversationId(activeConvId);
        } catch (err) {
          console.warn("Auto-create conversation failed; sending without persistence", err);
        }
      }

      // Surface the files actually being sent so the user can confirm what
      // the model is seeing — D (transparency).
      const filesAtSend = useAppStore.getState().attachedFiles;
      if (filesAtSend.length > 0) {
        const summary = filesAtSend
          .map((f) => {
            const size = f.bytes
              ? f.bytes < 1024
                ? `${f.bytes} B`
                : f.bytes < 1024 * 1024
                  ? `${(f.bytes / 1024).toFixed(1)} KB`
                  : `${(f.bytes / (1024 * 1024)).toFixed(1)} MB`
              : "";
            const source = f.source === "workspace" ? "workspace" : "upload";
            return `${f.path}${size ? ` (${size})` : ""} [${source}]`;
          })
          .join(", ");
        addMessage({
          role: "system",
          content: `Sent with ${filesAtSend.length} file${filesAtSend.length > 1 ? "s" : ""}: ${summary}`,
          timestamp: Date.now(),
        });
      }

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
              conversation_id: activeConvId,
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
            // WebSocket streaming — RAG fields forwarded to backend
            sendStreamPrompt({
              prompt: fileContext ? `${text}\n\n\`\`\`\n${fileContext}\n\`\`\`` : text,
              model,
              provider,
              conversation_id: activeConvId,
              file_context: fileContext,
              ...(ragEnabled ? { rag_enabled: true, rag_namespace: ragNamespace, rag_k: 5 } : {}),
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
              rag?: {
                namespace: string;
                hits: number;
                embedding_model: string;
                scores: number[];
                error?: string;
              };
            }>("/api/prompt", {
              prompt: fileContext ? `${text}\n\n\`\`\`\n${fileContext}\n\`\`\`` : text,
              model,
              provider,
              graph_type: "chat",
              conversation_id: activeConvId,
              file_context: fileContext,
              ...(ragEnabled ? { rag_enabled: true, rag_namespace: ragNamespace, rag_k: 5 } : {}),
            });

            // Show RAG system bubble before the assistant reply
            if (resp.data.rag) {
              const r = resp.data.rag;
              if (r.error) {
                addMessage({
                  role: "system",
                  content: `RAG skipped: ${r.error}`,
                  timestamp: Date.now(),
                });
              } else {
                addMessage({
                  role: "system",
                  content: `RAG: ${r.namespace} · ${r.hits} chunk(s) retrieved (${r.embedding_model})`,
                  timestamp: Date.now(),
                });
              }
            }

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
    [
      addMessage,
      conversationId,
      newConversation,
      setConversationId,
      sendStreamPrompt,
      isStreamWsReady,
      setPendingTeamJob,
    ]
    // ragEnabled and ragNamespace come from opts parameter, not closure
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

      <PresetsBar onApply={setPresetText} fileContext={fileContext} />

      <ChatInput
        models={models}
        isDisabled={isRunning}
        onSend={handleSend}
        onNewChat={handleNewChat}
        presetText={presetText}
        onPresetConsumed={() => setPresetText(null)}
        onFileContextChange={setFileContext}
      />
    </div>
  );
}
