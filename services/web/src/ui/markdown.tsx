import DOMPurify from "dompurify";
import { marked } from "marked";
import { useMemo } from "react";
import { cn } from "../lib/cn";

// Agent output is markdown — render it (sanitized) instead of showing raw
// asterisks and heading hashes. Used by chat bubbles, the run final reply,
// and AI change summaries. GFM line breaks: agents write chat-style newlines.
marked.setOptions({ gfm: true, breaks: true });

export function Markdown({ text, className }: { text: string; className?: string }) {
  const html = useMemo(
    () => DOMPurify.sanitize(marked.parse(text, { async: false }) as string,
                             { FORBID_TAGS: ["style", "img"] }),
    [text]);
  return (
    <div className={cn("md", className)}
         // eslint-disable-next-line react/no-danger
         dangerouslySetInnerHTML={{ __html: html }} />
  );
}
