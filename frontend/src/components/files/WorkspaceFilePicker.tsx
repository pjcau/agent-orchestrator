import { useState } from "react";
import { useFiles, fetchFileContent } from "@/api/hooks";
import type { FileItem } from "@/api/types";

interface WorkspaceFilePickerProps {
  open: boolean;
  onClose: () => void;
  onPick: (file: { path: string; content: string }) => void;
}

/**
 * Modal for browsing server-side workspace files.
 * Ported from vanilla app.js openFilePicker / loadDirectory / attachFile (lines 491-544).
 */
export function WorkspaceFilePicker({
  open,
  onClose,
  onPick,
}: WorkspaceFilePickerProps) {
  const [currentPath, setCurrentPath] = useState("");
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const { data, isLoading } = useFiles(currentPath, { enabled: open });

  if (!open) return null;

  const breadcrumbs: Array<{ label: string; path: string }> = [
    { label: "root", path: "" },
  ];
  if (currentPath) {
    const parts = currentPath.split("/");
    let acc = "";
    parts.forEach((p) => {
      acc = acc ? `${acc}/${p}` : p;
      breadcrumbs.push({ label: p, path: acc });
    });
  }

  const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes}B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}kB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  };

  const handleItemClick = async (item: FileItem) => {
    if (item.is_dir) {
      setCurrentPath(item.path);
      return;
    }
    setLoading(true);
    setFetchError(null);
    try {
      const result = await fetchFileContent(item.path);
      if (result.error) {
        setFetchError(result.error);
        return;
      }
      onPick({ path: result.path, content: result.content });
      onClose();
    } catch (err) {
      setFetchError(
        err instanceof Error ? err.message : "Failed to read file"
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="file-picker-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Browse workspace files"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="file-picker-modal">
        <div className="file-picker-header">
          <h2 className="file-picker-title">Browse Workspace</h2>
          <button
            className="btn-text"
            onClick={onClose}
            aria-label="Close file picker"
          >
            Close
          </button>
        </div>

        {/* Breadcrumbs */}
        <div className="file-breadcrumbs" aria-label="File path">
          {breadcrumbs.map((crumb, i) => (
            <span key={crumb.path}>
              {i > 0 && (
                <span className="breadcrumb-sep" aria-hidden="true">
                  /
                </span>
              )}
              <button
                className="breadcrumb-btn"
                onClick={() => setCurrentPath(crumb.path)}
              >
                {crumb.label}
              </button>
            </span>
          ))}
        </div>

        {/* File list */}
        <div className="file-list" role="list">
          {(isLoading || loading) && (
            <div className="empty-state">Loading...</div>
          )}
          {!isLoading && !loading && fetchError && (
            <div className="empty-state file-picker-error">{fetchError}</div>
          )}
          {!isLoading &&
            !loading &&
            !fetchError &&
            (data?.items ?? []).map((item) => (
              <div
                key={item.path}
                className={`file-item${item.is_dir ? " file-item--dir" : " file-item--file"}`}
                role="listitem"
                onClick={() => handleItemClick(item)}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") handleItemClick(item);
                }}
              >
                <span className="file-item__icon" aria-hidden="true">
                  {item.is_dir ? "[D]" : "[F]"}
                </span>
                <span className="file-item__name">{item.name}</span>
                {!item.is_dir && (
                  <span className="file-item__size">{formatSize(item.size)}</span>
                )}
              </div>
            ))}
          {!isLoading &&
            !loading &&
            !fetchError &&
            (data?.items ?? []).length === 0 && (
              <div className="empty-state">Empty directory</div>
            )}
        </div>
      </div>
    </div>
  );
}
