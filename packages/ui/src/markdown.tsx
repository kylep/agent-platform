import DOMPurify from "dompurify";
import { marked } from "marked";
import { useMemo } from "react";
import { cn } from "./cn";

// Agent output is markdown — render it (sanitized) instead of showing raw
// asterisks and heading hashes. Two modes:
//  - default (chat): single newlines become <br> — agents write chat-style.
//  - flow: single newlines flow into paragraphs — for PROSE documents whose
//    source is hard-wrapped (the Help pages render repo docs; keeping their
//    ~78-col wraps as breaks makes a ragged mess).
export function Markdown({ text, className, flow = false }: {
  text: string; className?: string; flow?: boolean;
}) {
  const html = useMemo(
    () => DOMPurify.sanitize(
      marked.parse(text, { async: false, gfm: true, breaks: !flow }) as string,
      { FORBID_TAGS: ["style", "img"] }),
    [text, flow]);
  return (
    <div className={cn("md", className)}
         // eslint-disable-next-line react/no-danger
         dangerouslySetInnerHTML={{ __html: html }} />
  );
}
