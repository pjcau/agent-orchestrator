import React from "react";
import type { ChatMessage as ChatMessageType, AssistantContent } from "@/api/types";
import { MarkdownRenderer } from "@/components/common/MarkdownRenderer";
import { HITLOptions, HITLInterrupt } from "@/components/common/HITLButtons";

interface ChatMessageProps {
  message: ChatMessageType & {
    hitlType?: "options" | "interrupt";
    options?: string[];
    runId?: string;
    streaming?: boolean;
  };
}

function formatNumber(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
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
      {(content.usage || content.elapsed_s) && (
        <div className="chat-usage">
          {content.usage?.output_tokens ?? 0} tok &middot;{" "}
          {content.elapsed_s && content.usage?.output_tokens
            ? `${((content.usage.output_tokens) / content.elapsed_s).toFixed(1)} tok/s`
            : "-"}{" "}
          &middot; {content.usage?.model ?? ""} &middot; {content.elapsed_s ?? 0}s
          {Object.keys(costs).length > 0 &&
            ` · $${Object.values(costs)
              .reduce((s, c) => s + (c.cost_usd ?? 0), 0)
              .toFixed(4)}`}
        </div>
      )}
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

export function ChatMessageItem({ message }: ChatMessageProps) {
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
        {message.model && (
          <div className="chat-bubble__meta">
            {message.model}
          </div>
        )}
      </div>
    </div>
  );
}
