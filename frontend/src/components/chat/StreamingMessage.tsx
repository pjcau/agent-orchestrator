import React from "react";
import { MarkdownRenderer } from "@/components/common/MarkdownRenderer";

interface StreamingMessageProps {
  buffer: string;
}

/**
 * Streaming message bubble — shows progressive markdown rendering
 * with a blinking cursor indicator.
 */
export function StreamingMessage({ buffer }: StreamingMessageProps) {
  return (
    <div className="chat-bubble chat-bubble--assistant chat-bubble--streaming">
      <div className="chat-bubble__avatar">A</div>
      <div className="chat-bubble__content">
        <MarkdownRenderer content={buffer} streaming />
        <span className="stream-cursor" aria-hidden="true" />
      </div>
    </div>
  );
}
