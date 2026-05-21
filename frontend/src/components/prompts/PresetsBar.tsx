import { useState } from "react";
import { usePresets } from "@/api/hooks";
import { useAppStore } from "@/stores/useAppStore";

interface PresetsBarProps {
  /** Called with the substituted prompt text when a preset is applied. */
  onApply: (promptText: string) => void;
  /** File context text (from attached files). Used for {context} substitution. */
  fileContext: string;
}

/**
 * Horizontal bar of preset prompt buttons above ChatInput.
 * Ported from vanilla app.js loadPresets() (lines 467-486).
 */
export function PresetsBar({ onApply, fileContext }: PresetsBarProps) {
  const { data, isLoading } = usePresets();
  const [notice, setNotice] = useState<string | null>(null);
  const presetsHidden = useAppStore((s) => s.presetsHidden);

  if (presetsHidden) return null;
  if (isLoading || !data?.presets?.length) return null;

  const handleClick = (prompt: string) => {
    if (prompt.includes("{context}") && !fileContext) {
      setNotice("Attach a file first");
      setTimeout(() => setNotice(null), 2500);
      return;
    }
    const resolved = fileContext
      ? prompt.replace("{context}", fileContext)
      : prompt;
    onApply(resolved);
  };

  return (
    <div className="presets-bar">
      {data.presets.map((preset) => (
        <button
          key={preset.label}
          className="preset-btn"
          title={preset.label}
          onClick={() => handleClick(preset.prompt)}
        >
          {preset.icon && (
            <span className="preset-icon" aria-hidden="true">
              {preset.icon}
            </span>
          )}
          <span>{preset.label}</span>
        </button>
      ))}
      {notice && (
        <span className="presets-bar__notice" role="status">
          {notice}
        </span>
      )}
    </div>
  );
}
