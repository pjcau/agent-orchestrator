import React, { useEffect, useRef, memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import hljs from "highlight.js";
import { extractThinkingBlocks, ThinkingAccordion } from "./ThinkingAccordion";

interface MarkdownRendererProps {
  content: string;
  /** When true, renders without heavy post-processing (for streaming) */
  streaming?: boolean;
}

/** Code block with syntax highlighting via highlight.js */
function CodeBlock({
  className,
  children,
}: {
  className?: string;
  children?: React.ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  const lang = /language-(\w+)/.exec(className ?? "")?.[1];

  useEffect(() => {
    if (ref.current && lang && lang !== "mermaid") {
      hljs.highlightElement(ref.current);
    }
  }, [children, lang]);

  if (lang === "mermaid") {
    return <MermaidBlock code={String(children ?? "")} />;
  }

  return (
    <pre className="md-code-block">
      <code ref={ref} className={className}>
        {children}
      </code>
    </pre>
  );
}

/** Renders a mermaid diagram using the global mermaid instance. */
function MermaidBlock({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const idRef = useRef(`mermaid-${Math.random().toString(36).slice(2)}`);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // Use global mermaid if available (loaded from CDN)
    const mermaid = (window as unknown as { mermaid?: { run: (opts: { nodes: Element[] }) => Promise<void> } }).mermaid;
    if (mermaid) {
      el.textContent = code;
      el.removeAttribute("data-processed");
      mermaid.run({ nodes: [el] }).catch(() => {
        // Fallback: show raw code
        el.textContent = code;
      });
    } else {
      el.textContent = code;
    }
  }, [code]);

  return (
    <div className="mermaid" id={idRef.current} ref={ref}>
      {code}
    </div>
  );
}

const remarkPlugins = [remarkGfm, remarkMath];
const rehypePlugins = [rehypeKatex];

/**
 * Unified markdown renderer with:
 * - react-markdown + remark-gfm
 * - rehype-katex for LaTeX math
 * - highlight.js for code blocks
 * - Mermaid diagram rendering
 * - <thinking>/<reasoning> accordion extraction
 */
export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  streaming = false,
}: MarkdownRendererProps) {
  const { cleanText, thinkingBlocks } = extractThinkingBlocks(content);

  return (
    <div className="md-content">
      {thinkingBlocks.map((block, i) => (
        <ThinkingAccordion key={i} content={block} />
      ))}
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={{
          // Code blocks with syntax highlighting
          code({ className, children, ...rest }) {
            const isBlock = !("inline" in rest) || !(rest as { inline?: boolean }).inline;
            if (isBlock) {
              return (
                <CodeBlock className={className}>{children}</CodeBlock>
              );
            }
            return (
              <code className="md-inline-code" {...rest}>
                {children}
              </code>
            );
          },
          // Open links in new tab
          a({ href, children }) {
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {streaming ? content : cleanText}
      </ReactMarkdown>
    </div>
  );
});
