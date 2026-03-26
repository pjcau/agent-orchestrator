import React, { useState } from "react";
import apiClient from "@/api/client";

interface HITLOptionsProps {
  runId: string;
  options: string[];
}

/** Renders clickable pill buttons for clarification options. */
export function HITLOptions({ runId, options }: HITLOptionsProps) {
  const [selected, setSelected] = useState<string | null>(null);

  const handleSelect = async (opt: string) => {
    setSelected(opt);
    try {
      await apiClient.post(`/api/runs/${encodeURIComponent(runId)}/resume`, {
        value: opt,
      });
    } catch (err) {
      console.error("Failed to send HITL response:", err);
    }
  };

  return (
    <div className="hitl-options">
      {options.map((opt) => (
        <button
          key={opt}
          className={`hitl-option-btn ${selected === opt ? "hitl-selected" : ""}`}
          disabled={selected !== null}
          onClick={() => handleSelect(opt)}
        >
          {opt}
        </button>
      ))}
    </div>
  );
}

interface HITLInterruptProps {
  runId: string;
}

/** Renders Approve / Reject buttons for interrupt events. */
export function HITLInterrupt({ runId }: HITLInterruptProps) {
  const [selected, setSelected] = useState<"approved" | "rejected" | null>(null);

  const handleClick = async (value: "approved" | "rejected") => {
    setSelected(value);
    try {
      await apiClient.post(`/api/runs/${encodeURIComponent(runId)}/resume`, {
        value,
      });
    } catch (err) {
      console.error("Failed to send HITL interrupt response:", err);
    }
  };

  return (
    <div className="hitl-interrupt">
      <button
        className={`hitl-approve-btn ${selected === "approved" ? "hitl-selected" : ""}`}
        disabled={selected !== null}
        onClick={() => handleClick("approved")}
      >
        Approve
      </button>
      <button
        className={`hitl-reject-btn ${selected === "rejected" ? "hitl-selected" : ""}`}
        disabled={selected !== null}
        onClick={() => handleClick("rejected")}
      >
        Reject
      </button>
    </div>
  );
}
