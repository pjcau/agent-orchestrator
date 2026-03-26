import React, { useState, useRef, useCallback, useEffect } from "react";
import type { ModelsResponse } from "@/api/types";

export type ExecMode = "multi-agent" | "agent" | "prompt";

interface ChatInputProps {
  models: ModelsResponse | undefined;
  isDisabled: boolean;
  onSend: (opts: {
    text: string;
    mode: ExecMode;
    model: string;
    provider: "openrouter" | "ollama";
    useStreaming: boolean;
    fileContext: string;
  }) => void;
  onNewChat: () => void;
}

/** Detect provider from model name */
function detectProvider(modelName: string): "openrouter" | "ollama" {
  return modelName.includes("/") ? "openrouter" : "ollama";
}

/** Attached file state */
interface AttachedFile {
  path: string;
  content: string;
}

export function ChatInput({ models, isDisabled, onSend, onNewChat }: ChatInputProps) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<ExecMode>("multi-agent");
  const [provider, setProvider] = useState<"openrouter" | "ollama">("openrouter");
  const [model, setModel] = useState("");
  const [useStreaming, setUseStreaming] = useState(true);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-select first available model when provider changes or models load
  useEffect(() => {
    if (!models) return;
    const list = provider === "openrouter" ? models.openrouter : models.ollama;
    if (list.length > 0 && !model) {
      setModel(list[0].name);
    }
  }, [models, provider, model]);

  const handleProviderChange = (p: "openrouter" | "ollama") => {
    setProvider(p);
    const list = p === "openrouter" ? models?.openrouter : models?.ollama;
    if (list?.length) {
      setModel(list[0].name);
    }
  };

  const autoResizeTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, []);

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || isDisabled) return;
    if (!model) {
      alert("No model selected.");
      return;
    }

    const fileContext = attachedFiles
      .map((f) => `--- ${f.path} ---\n${f.content}`)
      .join("\n\n");

    onSend({ text: trimmed, mode, model, provider, useStreaming, fileContext });
    setText("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [text, isDisabled, model, attachedFiles, onSend, mode, provider, useStreaming]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileAttach = async () => {
    // Simple file input trigger
    const input = document.createElement("input");
    input.type = "file";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      const content = await file.text();
      setAttachedFiles((prev) => [
        ...prev.filter((f) => f.path !== file.name),
        { path: file.name, content },
      ]);
    };
    input.click();
  };

  const paidModels = models?.openrouter.filter((m) => !m.name.includes(":free")) ?? [];
  const freeModels = models?.openrouter.filter((m) => m.name.includes(":free")) ?? [];
  const ollamaModels = models?.ollama ?? [];

  return (
    <div className="chat-input">
      {/* File context bar */}
      {attachedFiles.length > 0 && (
        <div className="chat-input__files">
          <span className="chat-input__files-label">Files</span>
          {attachedFiles.map((f, i) => (
            <span key={f.path} className="attached-file">
              <span className="attached-file__name">{f.path}</span>
              <button
                className="attached-file__remove"
                onClick={() =>
                  setAttachedFiles((prev) => prev.filter((_, j) => j !== i))
                }
              >
                &times;
              </button>
            </span>
          ))}
          <button
            className="btn-text"
            onClick={() => setAttachedFiles([])}
          >
            Clear
          </button>
        </div>
      )}

      {/* Controls row */}
      <div className="chat-input__controls">
        <select
          className="chat-input__select"
          value={mode}
          onChange={(e) => setMode(e.target.value as ExecMode)}
          disabled={isDisabled}
          title="Execution mode"
        >
          <option value="multi-agent">Multi-Agent</option>
          <option value="agent">Single Agent</option>
          <option value="prompt">Simple Prompt</option>
        </select>

        <select
          className="chat-input__select"
          value={provider}
          onChange={(e) => handleProviderChange(e.target.value as "openrouter" | "ollama")}
          disabled={isDisabled}
          title="Provider"
        >
          <option value="openrouter">Cloud (OpenRouter)</option>
          <option value="ollama">Local (Ollama)</option>
        </select>

        <select
          className="chat-input__select chat-input__select--model"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          disabled={isDisabled}
          title="Model"
        >
          {provider === "openrouter" ? (
            <>
              {paidModels.length > 0 && (
                <optgroup label="Paid models">
                  {paidModels.map((m) => (
                    <option key={m.name} value={m.name} style={{ color: "#f0b060" }}>
                      {m.name} ({m.size})
                    </option>
                  ))}
                </optgroup>
              )}
              {freeModels.length > 0 && (
                <optgroup label="Free models">
                  {freeModels.map((m) => (
                    <option key={m.name} value={m.name} style={{ color: "#7ee07e" }}>
                      {m.name} ({m.size})
                    </option>
                  ))}
                </optgroup>
              )}
            </>
          ) : (
            ollamaModels.map((m) => (
              <option key={m.name} value={m.name}>
                {m.name} ({m.size})
              </option>
            ))
          )}
          {!model && <option value="">Loading...</option>}
        </select>

        {mode === "prompt" && (
          <label className="stream-toggle">
            <input
              type="checkbox"
              checked={useStreaming}
              onChange={(e) => setUseStreaming(e.target.checked)}
            />
            <span>Stream</span>
          </label>
        )}
      </div>

      {/* Input row */}
      <div className="chat-input__row">
        <button
          className="btn-icon"
          onClick={handleFileAttach}
          title="Attach file"
          disabled={isDisabled}
        >
          +
        </button>
        <textarea
          ref={textareaRef}
          className="chat-input__textarea"
          rows={1}
          placeholder="Describe what you need..."
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            autoResizeTextarea();
          }}
          onKeyDown={handleKeyDown}
          disabled={isDisabled}
        />
        <button
          className="btn-send"
          onClick={handleSend}
          disabled={isDisabled || !text.trim()}
          title="Send (Enter)"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </div>

      {/* Action bar */}
      <div className="chat-input__actions">
        <button className="btn-text" onClick={onNewChat} disabled={isDisabled}>
          New Chat
        </button>
      </div>
    </div>
  );
}

export { detectProvider };
