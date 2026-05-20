/**
 * Placeholder bubble shown after the user sends a prompt but before the
 * model emits its first token. Bridges the visual gap between
 * "request sent" and "streaming response".
 */
export function ThinkingIndicator() {
  return (
    <div
      className="chat-bubble chat-bubble--assistant chat-bubble--thinking"
      role="status"
      aria-live="polite"
      aria-label="Assistant is thinking"
    >
      <div className="chat-bubble__avatar">A</div>
      <div className="chat-bubble__content">
        <span className="thinking-dots" aria-hidden="true">
          <span className="thinking-dots__dot" />
          <span className="thinking-dots__dot" />
          <span className="thinking-dots__dot" />
        </span>
      </div>
    </div>
  );
}
