import React, { useState, useRef, useCallback, useEffect } from "react";
import type { ModelsResponse } from "@/api/types";
import { WorkspaceFilePicker } from "@/components/files/WorkspaceFilePicker";
import { useAppStore } from "@/stores/useAppStore";
import apiClient from "@/api/client";
import type { AxiosError } from "axios";

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
    ragEnabled: boolean;
    ragNamespace: string;
  }) => void;
  onNewChat: () => void;
  /** When non-null, ChatInput sets its textarea to this value (from preset). */
  presetText?: string | null;
  /** Called after the preset text has been consumed so parent can clear it. */
  onPresetConsumed?: () => void;
  /** Notifies parent whenever the derived fileContext string changes. */
  onFileContextChange?: (ctx: string) => void;
}

/** Detect provider from model name */
function detectProvider(modelName: string): "openrouter" | "ollama" {
  return modelName.includes("/") ? "openrouter" : "ollama";
}

/** Format a byte count as a short human-readable string. */
export function formatBytes(n: number | undefined): string {
  if (!n || n <= 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Single-letter category for the chip badge. */
function fileKindBadge(kind: string | undefined, path: string): string {
  const k = (kind ?? "").toLowerCase();
  if (k === "pdf") return "PDF";
  if (k === "excel" || k === "xlsx" || k === "xls") return "XLS";
  if (k === "csv") return "CSV";
  if (k === "docx") return "DOC";
  if (k === "pptx") return "PPT";
  if (k === "html") return "HTML";
  if (k === "txt" || k.startsWith("text/")) return "TXT";
  if (k === "image" || k.startsWith("image/")) return "IMG";
  // Fallback to extension
  const m = path.match(/\.([a-z0-9]{1,5})$/i);
  return m ? m[1].toUpperCase() : "FILE";
}

export function ChatInput({
  models,
  isDisabled,
  onSend,
  onNewChat,
  presetText,
  onPresetConsumed,
  onFileContextChange,
}: ChatInputProps) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<ExecMode>("multi-agent");
  const [provider, setProvider] = useState<"openrouter" | "ollama">("openrouter");
  const [model, setModel] = useState("");
  const [useStreaming, setUseStreaming] = useState(true);
  // attachedFiles lives in the store so the global Reset action can clear it.
  const attachedFiles = useAppStore((s) => s.attachedFiles);
  const addAttachedFile = useAppStore((s) => s.addAttachedFile);
  const removeAttachedFileAt = useAppStore((s) => s.removeAttachedFileAt);
  const clearAttachedFiles = useAppStore((s) => s.clearAttachedFiles);
  // RAG preferences live in the store and survive Reset.
  const ragEnabled = useAppStore((s) => s.ragEnabled);
  const ragNamespace = useAppStore((s) => s.ragNamespace);
  const setRagEnabled = useAppStore((s) => s.setRagEnabled);
  const setRagNamespace = useAppStore((s) => s.setRagNamespace);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadingName, setUploadingName] = useState<string | null>(null);
  // Collapsed "advanced" controls (mode + provider) — exposed via a gear toggle
  // on mobile to free vertical space for the chat. Always-open on desktop via CSS.
  const [advOpen, setAdvOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-select first available model when provider changes or models load
  useEffect(() => {
    if (!models) return;
    const list = provider === "openrouter" ? models.openrouter : models.ollama;
    if (list.length > 0 && !model) {
      setModel(list[0].name);
    }
  }, [models, provider, model]);

  // Apply preset text into textarea when parent sets it
  useEffect(() => {
    if (presetText != null) {
      setText(presetText);
      onPresetConsumed?.();
      textareaRef.current?.focus();
    }
  }, [presetText, onPresetConsumed]);

  // Notify parent whenever the derived fileContext changes
  useEffect(() => {
    const ctx = attachedFiles
      .map((f) => `--- ${f.path} ---\n${f.content}`)
      .join("\n\n");
    onFileContextChange?.(ctx);
  }, [attachedFiles, onFileContextChange]);

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

    onSend({ text: trimmed, mode, model, provider, useStreaming, fileContext, ragEnabled, ragNamespace });
    setText("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [text, isDisabled, model, attachedFiles, onSend, mode, provider, useStreaming, ragEnabled, ragNamespace]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  /**
   * Upload a local file to /api/upload (multipart). The server runs the file
   * through DocumentConverter and returns markdown text suitable for the LLM
   * (PDFs, CSVs, .docx, .xlsx, …). Replaces the previous file.text() path
   * which was binary-unsafe.
   */
  const uploadFile = useCallback(async (file: File) => {
    setUploadError(null);
    setUploadingName(file.name);
    try {
      const form = new FormData();
      form.append("file", file);
      const resp = await apiClient.post<{
        success: boolean;
        filename: string;
        file_type?: string;
        markdown_content?: string;
        markdown_path?: string;
        page_count?: number | null;
        row_count?: number | null;
        error?: string;
      }>("/api/upload", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      const data = resp.data;
      if (!data.success) {
        setUploadError(data.error ?? "Upload failed");
        return;
      }
      addAttachedFile({
        path: data.filename ?? file.name,
        content: data.markdown_content ?? "",
        source: "upload",
        kind: data.file_type ?? file.type,
        bytes: file.size,
        truncated: false,
      });
    } catch (err) {
      const ax = err as AxiosError<{ error?: string }>;
      const serverMsg =
        (ax.response?.data && (ax.response.data as { error?: string }).error) ||
        ax.message ||
        String(err);
      setUploadError(serverMsg);
    } finally {
      setUploadingName(null);
    }
  }, [addAttachedFile]);

  const handleFileAttach = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.onchange = () => {
      const file = input.files?.[0];
      if (file) uploadFile(file);
    };
    input.click();
  }, [uploadFile]);

  const paidModels = models?.openrouter.filter((m) => !m.name.includes(":free")) ?? [];
  const freeModels = models?.openrouter.filter((m) => m.name.includes(":free")) ?? [];
  const ollamaModels = models?.ollama ?? [];

  return (
    <div className="chat-input">
      {/* File context bar */}
      {(attachedFiles.length > 0 || uploadingName || uploadError) && (
        <div className="chat-input__files">
          <span className="chat-input__files-label">Files</span>
          {attachedFiles.map((f, i) => {
            const sizeLabel = formatBytes(f.bytes);
            const sourceLabel = f.source === "workspace" ? "ws" : "up";
            const detail = [sizeLabel, sourceLabel].filter(Boolean).join(" · ");
            return (
              <span
                key={f.path}
                className="attached-file"
                title={`${f.path}${detail ? ` (${detail})` : ""}`}
                data-source={f.source ?? "upload"}
                data-kind={f.kind ?? ""}
              >
                <span className="attached-file__badge">{fileKindBadge(f.kind, f.path)}</span>
                <span className="attached-file__name">{f.path}</span>
                {sizeLabel && <span className="attached-file__size">{sizeLabel}</span>}
                {f.truncated && (
                  <span
                    className="attached-file__warn"
                    title="Content was truncated by the server"
                    aria-label="truncated"
                  >
                    !
                  </span>
                )}
                <button
                  className="attached-file__remove"
                  onClick={() => removeAttachedFileAt(i)}
                  aria-label={`Remove ${f.path}`}
                >
                  &times;
                </button>
              </span>
            );
          })}
          {uploadingName && (
            <span
              className="attached-file attached-file--uploading"
              role="status"
              aria-live="polite"
            >
              <span className="attached-file__spinner" />
              <span className="attached-file__name">{uploadingName}</span>
            </span>
          )}
          {uploadError && (
            <span
              className="attached-file attached-file--error"
              role="alert"
            >
              <span className="attached-file__name">Upload failed: {uploadError}</span>
              <button
                className="attached-file__remove"
                onClick={() => setUploadError(null)}
                aria-label="Dismiss error"
              >
                &times;
              </button>
            </span>
          )}
          {attachedFiles.length > 0 && (
            <button
              className="btn-text"
              onClick={clearAttachedFiles}
            >
              Clear
            </button>
          )}
        </div>
      )}

      {/* Controls row.
          On mobile (<=600px) the mode/provider selects collapse behind the
          gear button so the chat keeps as much vertical space as possible. */}
      <div className={`chat-input__controls ${advOpen ? "chat-input__controls--adv" : ""}`}>
        <button
          type="button"
          className="btn-icon chat-input__adv-toggle"
          onClick={() => setAdvOpen((o) => !o)}
          aria-label={advOpen ? "Hide advanced options" : "Show advanced options"}
          aria-expanded={advOpen}
          title="Advanced options"
        >
          ⚙
        </button>
        {/* Mobile-only segment controls (CSS-toggled). They replace the native
            <select> pickers for mode + provider which can render misaligned
            inside emulators / certain mobile browsers. The selects below
            remain the source of truth on desktop. */}
        <div
          className="chat-input__segment chat-input__segment--mode"
          role="radiogroup"
          aria-label="Execution mode"
        >
          {(
            [
              ["multi-agent", "Multi"],
              ["agent", "Single"],
              ["prompt", "Prompt"],
            ] as Array<[ExecMode, string]>
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={mode === value}
              className={`chat-input__segment-btn ${mode === value ? "active" : ""}`}
              onClick={() => setMode(value)}
              disabled={isDisabled}
            >
              {label}
            </button>
          ))}
        </div>
        <div
          className="chat-input__segment chat-input__segment--provider"
          role="radiogroup"
          aria-label="Provider"
        >
          {(
            [
              ["openrouter", "Cloud"],
              ["ollama", "Local"],
            ] as Array<["openrouter" | "ollama", string]>
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={provider === value}
              className={`chat-input__segment-btn ${provider === value ? "active" : ""}`}
              onClick={() => handleProviderChange(value)}
              disabled={isDisabled}
            >
              {label}
            </button>
          ))}
        </div>

        <select
          className="chat-input__select chat-input__select--adv"
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
          className="chat-input__select chat-input__select--adv"
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

        <label className="stream-toggle">
          <input
            type="checkbox"
            checked={ragEnabled}
            onChange={(e) => setRagEnabled(e.target.checked)}
          />
          <span>RAG</span>
        </label>

        {ragEnabled && (
          <input
            className="chat-input__rag-ns"
            type="text"
            value={ragNamespace}
            onChange={(e) => setRagNamespace(e.target.value)}
            placeholder="namespace"
            aria-label="RAG namespace"
            title="RAG namespace"
          />
        )}
      </div>

      {/* Input row */}
      <div className="chat-input__row">
        <button
          className="btn-icon"
          onClick={handleFileAttach}
          title="Upload local file (PDF, DOCX, XLSX, CSV, HTML, TXT)"
          disabled={isDisabled || uploadingName !== null}
        >
          +
        </button>
        <button
          className="btn-icon"
          onClick={() => setBrowseOpen(true)}
          title="Browse workspace files"
          disabled={isDisabled}
        >
          B
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

      {/* Workspace file picker modal */}
      <WorkspaceFilePicker
        open={browseOpen}
        onClose={() => setBrowseOpen(false)}
        onPick={(file) => addAttachedFile({ ...file, source: "workspace" })}
      />
    </div>
  );
}

export { detectProvider };
