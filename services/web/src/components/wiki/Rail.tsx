import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ChipButton } from "@ap/ui/chip";
import { Input } from "@ap/ui/field";
import { api } from "../../api";
import { participantLabel } from "../../lib/relay";
import { ago } from "../../lib/time";
import { STALE_DAYS, type WikiPage } from "../../lib/wiki";
import { Face } from "../relay/Face";
import type { WikiIndex } from "./useWiki";

// The wiki's index, down one side: what you are looking for, what is happening
// to it, what it is missing and what it has forgotten. Everything here is a
// link into the wiki — the rail is navigation, never a second copy of a page.

// How long the search box waits before asking. The API ranks a real search
// (postgres tsvector), so this is a request per phrase rather than per letter.
const SEARCH_SETTLE_MS = 220;
// Enough to scan without becoming a second page.
const SHOWN = 8;

/** Search, tags, recent changes, wanted and stale. */
export function Rail({ wiki }: { wiki: WikiIndex }) {
  const [term, setTerm] = useState("");
  const [tag, setTag] = useState<string | null>(null);
  const [hits, setHits] = useState<WikiPage[] | null>(null);
  const search = useRef<HTMLInputElement>(null);
  // Searches overlap and can answer in either order — a slow query for "dep"
  // landing after a fast one for "deploying" would put the wrong list under
  // the word on screen. Only the newest request may write.
  const issued = useRef(0);

  // `/` is the search key everywhere else on the web, and a wiki is a place
  // you arrive at looking for something.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement as HTMLElement | null;
      // A focused control gets its own keystrokes — a `/` typed into the page
      // editor is part of what somebody is writing.
      if (el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable)) return;
      e.preventDefault();
      search.current?.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // The server answers both, because it can rank a search and this cannot: the
  // listing the hook holds is capped, so filtering it in the browser would
  // silently search only the most recent 200 pages.
  useEffect(() => {
    const q = term.trim();
    if (!q && !tag) { setHits(null); return; }
    const id = setTimeout(() => {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (tag) params.set("tag", tag);
      const mine = ++issued.current;
      api<WikiPage[]>(`/api/wiki/pages?${params}`)
        .then((rows) => { if (mine === issued.current) setHits(rows); })
        .catch(() => { if (mine === issued.current) setHits([]); });
    }, q ? SEARCH_SETTLE_MS : 0);
    return () => clearTimeout(id);
  }, [term, tag]);

  return (
    <aside className="wiki-rail" aria-label="Wiki index">
      <Input ref={search} type="search" className="wiki-search"
             aria-label="Search the wiki" placeholder="Search the wiki  /"
             value={term} onChange={(e) => setTerm(e.target.value)} />

      {wiki.tags.length > 0 && (
        <section className="wiki-rail-block" aria-label="Tags">
          <h2 className="wiki-rail-head">Tags</h2>
          <div className="wiki-tags">
            {wiki.tags.map((t) => (
              <ChipButton key={t} variant={t === tag ? "accent" : "neutral"}
                          aria-pressed={t === tag}
                          onClick={() => setTag((now) => (now === t ? null : t))}>
                {t}
              </ChipButton>
            ))}
          </div>
        </section>
      )}

      {hits !== null && (
        <section className="wiki-rail-block" aria-label="Search results">
          <h2 className="wiki-rail-head">
            {hits.length} {hits.length === 1 ? "page" : "pages"}
          </h2>
          {hits.length === 0
            ? <p className="muted">Nothing matches. A page nobody has written is a page worth writing.</p>
            : (
              <ul className="wiki-list">
                {hits.slice(0, SHOWN).map((p) => (
                  <li key={p.slug}>
                    <Link to={`/wiki/${p.slug}`}>{p.title}</Link>
                    <span className="muted wiki-hit-summary">{p.summary}</span>
                  </li>
                ))}
              </ul>
            )}
        </section>
      )}

      <RecentChanges wiki={wiki} />

      <section className="wiki-rail-block" aria-label="Wanted pages">
        <h2 className="wiki-rail-head">Wanted</h2>
        {wiki.wanted.length === 0
          ? <p className="muted">Nothing is missing — every link goes somewhere.</p>
          : (
            <ul className="wiki-list wiki-wanted">
              {/* Most-asked first: this is the wiki's to-do list, not a
                  broken-link report. */}
              {[...wiki.wanted]
                .sort((a, b) => b.linked_from.length - a.linked_from.length
                  || a.slug.localeCompare(b.slug))
                .slice(0, SHOWN)
                .map((w) => (
                  <li key={w.slug}>
                    <Link to={`/wiki/${w.slug}`} className="wiki-red">{w.slug}</Link>
                    <span className="muted"> · asked for by {w.linked_from.length}</span>
                  </li>
                ))}
            </ul>
          )}
      </section>

      <section className="wiki-rail-block" aria-label="Stale pages">
        <h2 className="wiki-rail-head">Stale</h2>
        {wiki.stale.length === 0
          ? <p className="muted">Nothing has been left for {STALE_DAYS} days.</p>
          : (
            <ul className="wiki-list">
              {wiki.stale.slice(0, SHOWN).map((p) => (
                <li key={p.slug}>
                  <Link to={`/wiki/${p.slug}`}>{p.title}</Link>
                  <span className="muted"> · {ago(p.updated_at)}</span>
                </li>
              ))}
            </ul>
          )}
        {/* The API counts staleness over every live page; the list above is
            read off a capped listing, so on a big wiki the number is the
            honest one and the list is a sample of it. */}
        {wiki.stats && wiki.stats.stale > wiki.stale.length && (
          <p className="muted">{wiki.stats.stale} in all.</p>
        )}
      </section>
    </aside>
  );
}

/** What the wiki has been doing, live. One row per edit: who, which page, why,
 * and how many lines moved. */
function RecentChanges({ wiki }: { wiki: WikiIndex }) {
  return (
    <section className="wiki-rail-block" aria-label="Recent changes">
      <h2 className="wiki-rail-head">Recent changes</h2>
      {wiki.recent.length === 0
        ? <p className="muted">{wiki.loaded ? "Nothing has changed yet." : "Loading…"}</p>
        : (
          <ul className="wiki-changes">
            {wiki.recent.map((c) => (
              <li key={c.key}>
                {/* No face map here: the stream's payload carries the page
                    without one, so every face is derived from the author the
                    way lib/face derives the rest. */}
                <Face participant={c.author} size={20} />
                <span className="wiki-change-what">
                  <Link to={`/wiki/${c.slug}`}>{c.slug}</Link>
                  <span className="muted"> v{c.version}</span>
                  {c.reason && <span className="wiki-change-why"> — “{c.reason}”</span>}
                  <span className="muted wiki-change-who">
                    {" "}{participantLabel(c.author, wiki.me)} · {ago(c.at)}
                    {/* Everything an agent wrote links to the run it wrote it
                        in — the wiki's provenance is the whole point. */}
                    {c.run_id && <> · <Link to={`/runs/${c.run_id}`}>run ↗</Link></>}
                  </span>
                </span>
                {c.added !== null && c.removed !== null && (
                  <span className="wiki-lines">
                    <span className="wiki-added">+{c.added}</span>{" "}
                    <span className="wiki-removed">−{c.removed}</span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
    </section>
  );
}
