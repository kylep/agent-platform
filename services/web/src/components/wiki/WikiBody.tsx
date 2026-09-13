import { useMemo, type MouseEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Markdown } from "@ap/ui/markdown";
import { linkWiki } from "../../lib/chips";
import { demoteHeadings } from "../../lib/wiki";

// A page's text, with `[[slug]]` turned into chips. Blue for a page that
// exists, red for one nobody has written yet — which is a link all the same:
// following it opens the editor on that slug, which is how a wanted page
// becomes a page (docs/design/21).

export function WikiBody({ text, slugs, className = "wiki-body" }: {
  text: string;
  /** The live page slugs, or null while the listing is still in flight. */
  slugs: Set<string> | null;
  className?: string;
}) {
  const navigate = useNavigate();
  // One rewrite per body, not one per render: a page re-renders whenever a
  // frame lands, and this walks the whole document.
  const source = useMemo(() => demoteHeadings(linkWiki(text, slugs)), [text, slugs]);

  // The chips are anchors inside rendered markdown — `Markdown` hands back
  // HTML, not elements — so one delegated click turns what would be a full
  // page load back into a router navigation. Modified clicks are left alone:
  // "open that page in a new tab" is a thing people do.
  function onClick(e: MouseEvent<HTMLDivElement>) {
    const href = (e.target as HTMLElement).closest?.("a")?.getAttribute("href") ?? "";
    if (!href.startsWith("/wiki/") || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    navigate(href);
  }

  return (
    <div onClick={onClick}>
      <Markdown text={source} className={className} flow />
    </div>
  );
}
