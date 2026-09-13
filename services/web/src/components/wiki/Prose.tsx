import { useEffect, useMemo, useState, type MouseEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Markdown } from "@ap/ui/markdown";
import { api } from "../../api";
import { linkChips } from "../../lib/chips";
import type { WikiPage } from "../../lib/wiki";

// Prose written somewhere that is not the wiki — a room message, a ticket's
// description — with `OPS-12` and `[[deploying]]` turned into chips
// (docs/design/21). The rewrite itself lives in `lib/chips`; what lives here is
// the one thing those callers cannot answer on their own: which slugs exist.
//
// The wiki pages are asked for ONCE per page load and cached at module level,
// exactly as `Message.tsx` caches the ticket prefixes: every message asks, and
// the answer is the same list for all of them. Not `useWiki`, which opens an
// EventSource — a room already has its own stream, and a second one per page
// to colour a handful of links is a socket nobody needs.

// The list route's own cap, and the wiki's: a chip is coloured against what is
// listed, so the two have to agree.
const LIST_LIMIT = 200;

let known: Promise<WikiPage[] | null> | null = null;

function wikiPages(): Promise<WikiPage[] | null> {
  // A failure answers "unknown" rather than "no pages" — an empty set would
  // paint every link on the page red, which is a claim, not an absence. And it
  // FORGETS itself, so one blip does not cost the session its chips.
  known ??= api<WikiPage[]>(`/api/wiki/pages?limit=${LIST_LIMIT}`)
    .catch(() => { known = null; return null; });
  return known;
}

/** Drop the cached listing, so the next reader asks again. Called after a write
 * that changes what exists — a promotion — since a page created a moment ago
 * must not still read as wanted. */
export function forgetWikiPages(): void {
  known = null;
}

/** Every live page, or null while the listing is in flight (or after it
 * failed). One request per page load, however many components ask. */
export function useWikiPages(): WikiPage[] | null {
  const [pages, setPages] = useState<WikiPage[] | null>(null);
  useEffect(() => {
    let on = true;
    wikiPages().then((rows) => { if (on) setPages(rows); });
    return () => { on = false; };
  }, []);
  return pages;
}

/** The live slug set — what makes a `[[link]]` blue rather than red. Null is
 * "not known yet", never "nothing exists". */
export function useWikiSlugs(): Set<string> | null {
  const pages = useWikiPages();
  return useMemo(() => (pages ? new Set(pages.map((p) => p.slug)) : null), [pages]);
}

/** Markdown with both chip passes over it. `prefixes` are the ticket projects
 * that actually exist — a key whose project does not is somebody's version
 * number, not a reference. */
export function Prose({ text, prefixes, className }: {
  text: string;
  prefixes: string[];
  className?: string;
}) {
  const slugs = useWikiSlugs();
  const navigate = useNavigate();
  // One rewrite per body, not one per render: this walks the whole document,
  // and the page around it re-renders on every frame that lands.
  const source = useMemo(() => linkChips(text, prefixes, slugs), [text, prefixes, slugs]);

  // The chips are anchors inside rendered markdown — `Markdown` hands back
  // HTML, not elements — so one delegated click turns what would be a full
  // page load back into a router navigation. Modified clicks are left alone:
  // "open that in a new tab" is a thing people do.
  function onClick(e: MouseEvent<HTMLDivElement>) {
    const href = (e.target as HTMLElement).closest?.("a")?.getAttribute("href") ?? "";
    const ours = href.startsWith("/tickets/") || href.startsWith("/wiki/");
    if (!ours || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    navigate(href);
  }

  // The caller's class goes on the OUTER element — the one its page lays out.
  // `.ticket-main > .ticket-body` orders the description against the fields and
  // the thread on a phone, and a delegating wrapper slipped in between would
  // silently drop the description to the bottom of that column. The markdown
  // itself always carries `.md`, which is what every chip and typography rule
  // actually hangs off.
  return (
    <div className={className} onClick={onClick}>
      <Markdown text={source} />
    </div>
  );
}
