"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

const components: Components = {
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  ul: ({ children }) => (
    <ul className="mb-2 ml-4 list-disc space-y-1 last:mb-0">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-2 ml-4 list-decimal space-y-1 last:mb-0">{children}</ol>
  ),
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="underline underline-offset-2 hover:no-underline"
    >
      {children}
    </a>
  ),
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  blockquote: ({ children }) => (
    <blockquote className="mb-2 border-l-2 border-current/30 pl-3 italic opacity-90 last:mb-0">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-2 border-current/20" />,
  h1: ({ children }) => <p className="mb-1 font-semibold">{children}</p>,
  h2: ({ children }) => <p className="mb-1 font-semibold">{children}</p>,
  h3: ({ children }) => <p className="mb-1 font-semibold">{children}</p>,
  table: ({ children }) => (
    <div className="mb-2 overflow-x-auto last:mb-0">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-current/20 px-2 py-1 text-left font-semibold">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-current/20 px-2 py-1">{children}</td>
  ),
  pre: ({ children }) => <pre className="mb-2 last:mb-0">{children}</pre>,
  code: ({ className, children, ...props }) => {
    const isBlock = /language-(\w+)/.test(className || "");
    if (isBlock) {
      return (
        <code
          className={cn(
            "block overflow-x-auto rounded-lg bg-black/5 p-2 font-mono text-xs",
            className,
          )}
          {...props}
        >
          {children}
        </code>
      );
    }
    return (
      <code className="rounded bg-black/5 px-1 py-0.5 font-mono text-xs" {...props}>
        {children}
      </code>
    );
  },
};

// Renders LLM-authored chat replies as markdown (bold/lists/links/code/tables
// via GFM) instead of raw asterisks — only ever used for assistant messages;
// a doctor's/patient's own typed text stays plain so their input is never
// silently reinterpreted as formatting.
export function ChatMarkdown({ content, className }: { content: string; className?: string }) {
  return (
    <div className={cn("text-sm leading-relaxed [&>*:last-child]:mb-0", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
