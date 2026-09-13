import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { Editor } from "../components/wiki/Editor";
import { History } from "../components/wiki/History";
import { WikiBody } from "../components/wiki/WikiBody";
import { useWiki, useWikiPage } from "../components/wiki/useWiki";
import { Face } from "../components/relay/Face";
import { participantLabel } from "../lib/relay";
import { ago } from "../lib/time";
import { useTitle } from "../lib/title";
import {
  restoreVersion, SLUG_RE, type WikiCitations, type WikiPage as Page,
} from "../lib/wiki";

// One page (docs/design/21). Everything on it is a link back into where it
// came from: a `[[slug]]` to another page, a citation to the message that
// quoted it, a version to the run that wrote it. A wiki whose provenance is
// invisible is a wiki nobody can check.

export default function WikiPage() {
  const { slug = "" } = useParams<{ slug: string }>();
  const wiki = useWiki();
  const page = useWikiPage(slug);
  const [editing, setEditing] = useState(false);
  // Whether the editor has words in it that only exist in this browser. It is
  // the one thing on the page that cannot be fetched again, so nothing — not
  // even the page turning up under the create editor — closes the editor over
  // it.
  const [dirty, setDirty] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const detail = page.detail;
  const row = detail?.page ?? null;
  useTitle(row?.title ?? slug, "Wiki");

  // A slug nobody has written is not a missing page, it is an invitation: the
  // editor opens on it, pre-filled, which is how a red chip becomes a page.
  // When it stops being missing — somebody else wrote it while this was open —
  // the page takes over, unless there is a draft in the box.
  useEffect(() => {
    setEditing((was) => (page.missing ? true : (was && dirty)));
    setShowHistory(false);
    // `dirty` is deliberately not a dependency: this runs when the page's
    // existence changes, and reads the draft state as it is at that moment.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page.missing, slug]);

  useEffect(() => { setDirty(false); }, [slug]);

  // Live, out of the index's own subscription rather than a second stream:
  // when a frame says this page has moved past what is on screen, re-read it.
  // A frame is the row, but an edit is also a new version and a new set of
  // links, so the page re-reads itself rather than patching what it holds.
  const streamed = wiki.pages.find((p) => p.slug === slug)?.version ?? 0;
  // 0 when this slug is still a wanted page, which is exactly the case that
  // matters: a frame for a slug we are looking at the blank of means somebody
  // has just written it, and the reader should be reading it rather than
  // typing a second copy. Gated on `loaded` so the index's own listing does
  // not race the first read of the page.
  const version = row?.version ?? 0;
  const reload = page.reload;
  useEffect(() => {
    if (page.loaded && streamed > version) reload();
  }, [page.loaded, streamed, version, reload]);

  const markDirty = useCallback(() => setDirty(true), []);

  function openHistory() {
    setShowHistory(true);
    page.loadHistory();
  }

  function saved(written: Page) {
    page.absorb(written);
    setDirty(false);
    setEditing(false);
    // The backlinks and the citations are the server's to recompute — the
    // write answered with the page alone.
    page.reload();
    wiki.reload();
  }

  async function restore(version: number) {
    setBusy(true);
    setError(null);
    try {
      saved(await restoreVersion(slug, version));
      page.loadHistory();
      page.showDiff(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That version could not be restored.");
    } finally {
      setBusy(false);
    }
  }

  if (!SLUG_RE.test(slug)) {
    return (
      <div className="page page-wiki-page">
        <h1>{slug}</h1>
        <p className="muted">
          That is not a wiki slug — pages are lower-case words joined by hyphens.
          The wiki is under <Link to="/wiki">Wiki</Link>.
        </p>
      </div>
    );
  }

  if (!page.loaded) {
    return <div className="page page-wiki-page"><p className="muted">Loading…</p></div>;
  }

  if (page.error && !row) {
    return (
      <div className="page page-wiki-page">
        <h1>{slug}</h1>
        <div className="error">{page.error}</div>
      </div>
    );
  }

  return (
    <div className="page page-wiki-page">
      <div className="wiki-page-head">
        <div>
          <h1>{row?.title ?? slug}</h1>
          <Meta row={row} slug={slug} cited={detail?.cited_in ?? null} me={wiki.me} />
        </div>
        {row && !editing && (
          <div className="wiki-page-actions">
            <Button onClick={() => setEditing(true)}>Edit</Button>
            <Button variant="secondary" aria-expanded={showHistory}
                    onClick={() => (showHistory ? setShowHistory(false) : openHistory())}>
              History
            </Button>
          </div>
        )}
      </div>

      {error && <div className="error">{error}</div>}

      {page.missing && !editing
        ? (
          <div className="wiki-missing">
            <p className="muted">
              Nobody has written <code>{slug}</code> yet.
            </p>
            <Button onClick={() => setEditing(true)}>Write this page</Button>
          </div>
        )
        : editing
        ? (
          <>
            {page.missing && (
              <p className="muted">
                Nobody has written <code>{slug}</code> yet. Pages point at it, which is
                why it is on the wanted list — write it and every one of those links
                turns blue.
              </p>
            )}
            <Editor slug={slug} page={row} onSaved={saved} onDirty={markDirty}
                    onReload={() => page.reload().then((d) => d?.page ?? null)}
                    onCancel={() => { setDirty(false); setEditing(false); }} />
          </>
        )
        : (
          <div className="wiki-layout">
            {/* A div, not a landmark: the console shell already wraps every
                page in its own <main>. */}
            <div className="wiki-main">
              <WikiBody text={row?.body ?? ""} slugs={wiki.slugs} />
            </div>
            <aside className="wiki-rail wiki-page-rail" aria-label="What points here">
              <section className="wiki-rail-block" aria-label="Backlinks">
                <h2 className="wiki-rail-head">Backlinks</h2>
                {detail && detail.backlinks.length > 0
                  ? (
                    <ul className="wiki-list">
                      {detail.backlinks.map((b) => (
                        <li key={b.slug}><Link to={`/wiki/${b.slug}`}>{b.title}</Link></li>
                      ))}
                    </ul>
                  )
                  : <p className="muted">No page links here yet.</p>}
              </section>
            </aside>
          </div>
        )}

      {showHistory && row && (
        <History rows={page.history} diff={page.diff} diffFor={page.diffFor}
                 diffError={page.diffError} current={row.version} me={wiki.me} busy={busy}
                 onSelect={page.showDiff} onRestore={restore} />
      )}
    </div>
  );
}

/** Who last touched this page, when, what it is filed under, and who has been
 * quoting it. */
function Meta({ row, slug, cited, me }: {
  row: Page | null; slug: string; cited: WikiCitations | null; me: string | null;
}) {
  if (!row) return <p className="muted wiki-meta">{slug} · not written yet</p>;
  return (
    <p className="wiki-meta">
      {row.tags.map((t) => <Chip key={t}>{t}</Chip>)}
      {row.source_memory_id && (
        // Where this page came from, said once. A promoted memory is a private
        // note somebody decided everybody should be able to cite.
        <Chip variant="accent" title={`promoted from memory ${row.source_memory_id}`}>
          📖 promoted from memory
        </Chip>
      )}
      <span className="wiki-version-n">v{row.version}</span>
      <span className="wiki-who">
        <Face participant={row.updated_by} face={row.updated_by_face} size={20} />
        {participantLabel(row.updated_by, me)}
      </span>
      <span className="muted">{ago(row.updated_at)}</span>
      {cited && cited.count > 0 && (
        <span className="wiki-cited">
          cited in {cited.count}{cited.count_capped ? "+" : ""}{" "}
          {cited.count === 1 && !cited.count_capped ? "message" : "messages"}
          {cited.last.map((c) => (
            <span key={c.message_id}>
              {" · "}
              <Link to={`/relay?channel=${encodeURIComponent(c.channel_id)}`
                        + `&thread=${encodeURIComponent(c.message_id)}`}>
                {participantLabel(c.author, me)} {ago(c.created_at)}
              </Link>
            </span>
          ))}
        </span>
      )}
    </p>
  );
}
