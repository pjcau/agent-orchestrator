import type { ChatMessage as ChatMessageType, AssistantContent } from "@/api/types";
import { MarkdownRenderer } from "@/components/common/MarkdownRenderer";
import { HITLOptions, HITLInterrupt } from "@/components/common/HITLButtons";
import { ChatMessageActions } from "./ChatMessageActions";

interface ChatMessageProps {
  message: ChatMessageType & {
    hitlType?: "options" | "interrupt";
    options?: string[];
    runId?: string;
    streaming?: boolean;
  };
  /**
   * Called when the user clicks the Regenerate action on this assistant
   * bubble. Wired by ChatPanel — when omitted (e.g. in tests or read-only
   * views), the regenerate button is hidden.
   */
  onRegenerate?: () => void;
}

function formatNumber(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}

function formatElapsed(s: number): string {
  if (s < 1) return `${Math.round(s * 1000)}ms`;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s - m * 60);
  return `${m}m${r.toString().padStart(2, "0")}s`;
}

function formatCost(c: number): string {
  if (c <= 0) return "$0";
  // Avoid rounding non-zero costs to "$0" — short prompts can legitimately
  // cost a fraction of a cent.
  if (c < 0.0001) return "<$0.0001";
  if (c < 0.01) return `$${c.toFixed(4)}`;
  if (c < 1) return `$${c.toFixed(3)}`;
  return `$${c.toFixed(2)}`;
}

function AgentStepMessage({ content }: { content: AssistantContent }) {
  const steps = content.steps ?? [];
  const costs = content.agent_costs ?? {};

  return (
    <div className="chat-message__agent-steps">
      {steps.map((step, i) => {
        const cost = costs[step.node];
        return (
          <div key={i} className="chat-step">
            <div className="chat-step__header">
              <span className="chat-step__label">{step.node}</span>
              {cost && (
                <span className="chat-step__cost">
                  {formatNumber(cost.tokens ?? 0)} tok &middot; $
                  {(cost.cost_usd ?? 0).toFixed(4)}
                </span>
              )}
            </div>
            <div className="chat-step__content">
              <MarkdownRenderer content={step.output} />
            </div>
          </div>
        );
      })}
      {(content.usage || content.elapsed_s) && (() => {
        const aggregatedCost = Object.values(costs).reduce(
          (s, c) => s + (c.cost_usd ?? 0),
          0,
        );
        const totalCost =
          aggregatedCost > 0 ? aggregatedCost : (content.usage?.cost_usd ?? 0);
        return (
          <div className="chat-usage" title="tokens · tok/s · model · elapsed · cost (input+output)">
            {content.usage?.output_tokens ?? 0} tok &middot;{" "}
            {content.elapsed_s && content.usage?.output_tokens
              ? `${(content.usage.output_tokens / content.elapsed_s).toFixed(1)} tok/s`
              : "-"}{" "}
            &middot; {content.usage?.model ?? ""} &middot;{" "}
            {formatElapsed(content.elapsed_s ?? 0)}
            {totalCost > 0 && ` · ${formatCost(totalCost)}`}
          </div>
        );
      })()}
    </div>
  );
}

/** Renders a TOOL_CALL or TOOL_RESULT system message as a tool card. */
function ToolCallMessage({ content }: { content: string }) {
  if (content.startsWith("TOOL_CALL:")) {
    try {
      const data = JSON.parse(content.slice("TOOL_CALL:".length)) as {
        agent: string;
        toolName: string;
        arguments: Record<string, unknown>;
        toolCallId: string;
      };
      return (
        <div className="chat-tool-call" id={`tc-${data.toolCallId}`}>
          <div className="chat-tool-call__header">
            <span className="chat-tool-call__agent">{data.agent}</span>
            <span className="chat-tool-call__arrow">&rarr;</span>
            <span className="chat-tool-call__name">{data.toolName}</span>
          </div>
          <pre className="chat-tool-call__args">
            {JSON.stringify(data.arguments ?? {}, null, 2)}
          </pre>
        </div>
      );
    } catch {
      return <div className="chat-system-msg">{content}</div>;
    }
  }

  if (content.startsWith("TOOL_RESULT:")) {
    try {
      const data = JSON.parse(content.slice("TOOL_RESULT:".length)) as {
        toolCallId: string;
        success: boolean;
        output: string;
      };
      return (
        <div
          className={`chat-tool-result ${data.success ? "chat-tool-result--ok" : "chat-tool-result--err"}`}
        >
          {data.output}
        </div>
      );
    } catch {
      return <div className="chat-system-msg">{content}</div>;
    }
  }

  return <div className="chat-system-msg">{content}</div>;
}

export function ChatMessageItem({ message, onRegenerate }: ChatMessageProps) {
  const { role, content } = message;
  const hitlType = (message as ChatMessageProps["message"]).hitlType;
  const options = (message as ChatMessageProps["message"]).options;
  const runId = (message as ChatMessageProps["message"]).runId ?? "";

  if (role === "system") {
    const str = typeof content === "string" ? content : "";
    if (str.startsWith("TOOL_CALL:") || str.startsWith("TOOL_RESULT:")) {
      return <ToolCallMessage content={str} />;
    }
    return <div className="chat-system-bubble">{str}</div>;
  }

  if (role === "user") {
    return (
      <div className="chat-bubble chat-bubble--user">
        <div className="chat-bubble__avatar">U</div>
        <div className="chat-bubble__content">
          <p>{typeof content === "string" ? content : ""}</p>
        </div>
      </div>
    );
  }

  // Assistant message
  const isStreaming = (message as ChatMessageProps["message"]).streaming;

  return (
    <div className="chat-bubble chat-bubble--assistant">
      <div className="chat-bubble__avatar">A</div>
      <div className="chat-bubble__content">
        {typeof content === "string" ? (
          <>
            <MarkdownRenderer content={content} streaming={isStreaming} />
            {hitlType === "options" && options && (
              <HITLOptions runId={runId} options={options} />
            )}
            {hitlType === "interrupt" && <HITLInterrupt runId={runId} />}
          </>
        ) : (
          <AgentStepMessage content={content as AssistantContent} />
        )}
        {(message.model || message.elapsed_s != null || message.cost_usd != null) && (
          <div className="chat-bubble__meta" title="model · elapsed · cost (input+output)">
            {message.model && <span className="chat-bubble__meta-model">{message.model}</span>}
            {message.elapsed_s != null && (
              <span className="chat-bubble__meta-time">{formatElapsed(message.elapsed_s)}</span>
            )}
            {message.cost_usd != null && (
              <span className="chat-bubble__meta-cost">{formatCost(message.cost_usd)}</span>
            )}
          </div>
        )}
        {!isStreaming && typeof content === "string" && content.length > 0 && (
          <ChatMessageActions
            messageId={String(message.timestamp ?? "")}
            content={content}
            onRegenerate={onRegenerate}
          />
        )}
      </div>
    </div>
  );
}
