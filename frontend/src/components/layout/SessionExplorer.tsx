import { useState, useEffect } from "react";
import apiClient from "@/api/client";
import type { FileItem } from "@/api/types";

interface SessionExplorerProps {
  sessionId: string;
  onClose: () => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fileIcon(name: string): string {
  if (name.endsWith(".py")) return "\u{1F40D}";
  if (name.endsWith(".json")) return "{}";
  if (name.endsWith(".md")) return "\u{1F4DD}";
  if (name.endsWith(".txt") || name.endsWith(".log")) return "\u{1F4C4}";
  return "\u{1F4CE}";
}

/** Inline file explorer for a specific session, rendered inside the history sidebar. */
export function SessionExplorer({ sessionId, onClose }: SessionExplorerProps) {
  const [files, setFiles] = useState<FileItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // Fetch files on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await apiClient.get<{ files: FileItem[] }>(
          `/api/jobs/${encodeURIComponent(sessionId)}/files`
        );
        if (!cancelled) setFiles(resp.data.files ?? []);
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Failed to load files");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const handleSelectFile = async (filename: string) => {
    setSelectedFile(filename);
    setPreviewLoading(true);
    setPreview(null);
    try {
      const resp = await apiClient.get<{ content: string }>(
        `/api/jobs/${encodeURIComponent(sessionId)}/files/${encodeURIComponent(filename)}`
      );
      setPreview(resp.data.content ?? "");
    } catch {
      setPreview("(failed to load file content)");
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleDownloadSession = () => {
    window.open(
      `/api/jobs/${encodeURIComponent(sessionId)}/download`,
      "_blank"
    );
  };

  const handleDownloadFile = (filename: string) => {
    window.open(
      `/api/jobs/${encodeURIComponent(sessionId)}/files/${encodeURIComponent(filename)}?download=1`,
      "_blank"
    );
  };

  return (
    <div className="session-explorer">
      <div className="session-explorer__header">
        <span>Files &middot; {files.length}</span>
        <div style={{ display: "flex", gap: "4px" }}>
          <button
            className="btn-explorer-action"
            onClick={handleDownloadSession}
            title="Download all as ZIP"
          >
            Download ZIP
          </button>
          <button className="btn-explorer-action" onClick={onClose}>
            Close
          </button>
        </div>
      </div>

      {loading && (
        <div className="session-explorer__empty">Loading files...</div>
      )}
      {error && (
        <div className="session-explorer__empty">Error: {error}</div>
      )}

      {!loading && !error && files.length === 0 && (
        <div className="session-explorer__empty">No files in this session</div>
      )}

      {files.length > 0 && (
        <div className="session-explorer__files">
          {files.map((f) => (
            <div
              key={f.name}
              className={`session-explorer__file ${selectedFile === f.name ? "session-explorer__file--active" : ""}`}
              onClick={() => handleSelectFile(f.name)}
            >
              <span className="session-explorer__file-icon">
                {fileIcon(f.name)}
              </span>
              <span className="session-explorer__file-name">{f.name}</span>
              <span className="session-explorer__file-size">
                {formatSize(f.size)}
              </span>
              <button
                className="btn-explorer-action"
                onClick={(e) => {
                  e.stopPropagation();
                  handleDownloadFile(f.name);
                }}
                title="Download file"
              >
                DL
              </button>
            </div>
          ))}
        </div>
      )}

      {selectedFile && (
        <div className="session-explorer__preview">
          {previewLoading ? (
            <div className="session-explorer__empty">Loading preview...</div>
          ) : (
            <pre>{preview}</pre>
          )}
        </div>
      )}
    </div>
  );
}
