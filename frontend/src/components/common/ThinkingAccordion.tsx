import React from "react";

interface ThinkingAccordionProps {
  content: string;
}

/** Collapsible accordion for <thinking> / <reasoning> blocks. */
export function ThinkingAccordion({ content }: ThinkingAccordionProps) {
  return (
    <details className="thinking-accordion">
      <summary className="thinking-summary">Thinking...</summary>
      <div className="thinking-content">
        <pre>{content}</pre>
      </div>
    </details>
  );
}

/**
 * Extract <thinking>...</thinking> or <reasoning>...</reasoning> blocks
 * from text. Returns { cleanText, thinkingBlocks }.
 */
export function extractThinkingBlocks(text: string): {
  cleanText: string;
  thinkingBlocks: string[];
} {
  const thinkingBlocks: string[] = [];
  const cleanText = text.replace(
    /<(thinking|reasoning)>([\s\S]*?)<\/\1>/gi,
    (_match, _tag, content: string) => {
      thinkingBlocks.push(content.trim());
      return "";
    }
  );
  return { cleanText: cleanText.trim(), thinkingBlocks };
}
