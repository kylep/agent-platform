import { useEffect, useState } from "react";
import { Button } from "@ap/ui/button";
import { CodeEditor, Input } from "@ap/ui/field";
import {
  createPage, savePage, WikiWriteError, type WikiPage,
} from "../../lib/wiki";

// Writing a page. The whole body in a textarea, because a wiki page IS its
// markdown and a rich editor would be a second, lossier source of truth.
//
// The version the reader opened is carried through the write. That is the one
// thing this form must not lose: two people editing the same page is the
// normal case here — one of them is usually an agent — and a write that does
// not say what it read silently erases whoever got there first.

/** What the page is at now and what it says — or, when a CREATE lost the race,
 * neither, because the writer never named a version to be told about. Both are
 * the same refusal: somebody got there first, and nothing was overwritten. */
type Conflict = { version: number | null; summary: string; created?: boolean };

export function Editor({ slug, page, onSaved, onCancel, onReload, onDirty }: {
  slug: string;
  /** The page being rewritten, or null when this slug is still a wanted page. */
  page: WikiPage | null;
  onSaved: (page: WikiPage) => void;
  onCancel: () => void;
  /** Re-read the page from the server and hand back what it says now — how
   * "reload and keep my text" gets a base version it can write against. */
  onReload: () => Promise<WikiPage | null>;
  /** Called the first time the reader changes anything. The host uses it to
   * decide whether it may close this editor over what is in it. */
  onDirty?: () => void;
}) {
  const [title, setTitle] = useState(page?.title ?? slug);
  const [body, setBody] = useState(page?.body ?? "");
  const [tags, setTags] = useState((page?.tags ?? []).join(", "));
  const [reason, setReason] = useState("");
  const [base, setBase] = useState(page?.version ?? 0);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // A create only while there is still nothing to write against. Once a base
  // version has been adopted — the page turned up, or a lost create told us
  // where it got to — this is an edit, and sending a second create would be
  // asking for the same refusal twice.
  const creating = page === null && base === 0;

  // The page turned up while this editor was open on a slug that had none —
  // an agent wrote it in the meantime. The draft stays; what changes is what
  // it will be written against, because a create is no longer possible and a
  // write with no base is the one thing the API will not take.
  useEffect(() => {
    if (page && base === 0) setBase(page.version);
  }, [page, base]);

  function touch<T>(set: (value: T) => void) {
    return (value: T) => { onDirty?.(); set(value); };
  }

  async function submit() {
    setBusy(true);
    setError(null);
    const draft = {
      title: title.trim() || slug, body, reason: reason.trim(),
      tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
    };
    try {
      onSaved(creating ? await createPage(slug, draft) : await savePage(slug, draft, base));
      setConflict(null);
    } catch (err) {
      // Every refusal is a sentence somebody can act on, never a JSON blob:
      // a lost race is the banner above the form, a rule or an exhausted
      // budget is the line under it.
      if (err instanceof WikiWriteError && err.status === 409) {
        // A create that lost its race carries no version: there is nothing to
        // merge against, only a page to go and read.
        setConflict({ version: err.currentVersion, summary: err.currentSummary,
                      created: err.currentVersion === null });
      } else {
        setError(err instanceof WikiWriteError ? err.message
          : "The wiki could not be written to. Try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  /** Take the page as it is now and keep the draft. The reader's words are the
   * one thing that cannot be fetched again, so they stay in the box and only
   * the base version moves — the next save is then a real write against what
   * the page actually says. */
  async function reloadKeeping() {
    const fresh = await onReload();
    if (fresh) setBase(fresh.version);
    else if (conflict?.version) setBase(conflict.version);
    setConflict(null);
  }

  return (
    <form className="wiki-editor" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      {conflict && (
        <div className="wiki-conflict" role="alert">
          <p>
            {conflict.created
              ? <strong>This page was just created — reload to see it.</strong>
              : (
                <>
                  <strong>This page changed (now v{conflict.version})</strong> while you
                  were writing. Nothing was overwritten.
                </>
              )}
          </p>
          {conflict.summary && <p className="muted">It now says: “{conflict.summary}”</p>}
          <Button type="button" variant="secondary" onClick={reloadKeeping}>
            Reload and keep my text
          </Button>
        </div>
      )}

      <label className="field-label" htmlFor="wiki-title">Title</label>
      <Input id="wiki-title" value={title} onChange={(e) => touch(setTitle)(e.target.value)}
             maxLength={120} />

      <label className="field-label" htmlFor="wiki-body">Page body</label>
      <CodeEditor id="wiki-body" rows={18} value={body}
                  onChange={(e) => touch(setBody)(e.target.value)}
                  placeholder="Markdown. Link another page with [[its-slug]]." />

      <div className="wiki-editor-row">
        <span>
          <label className="field-label" htmlFor="wiki-reason">Reason</label>
          <Input id="wiki-reason" value={reason} maxLength={200}
                 onChange={(e) => touch(setReason)(e.target.value)}
                 placeholder="what changed, in a line" />
        </span>
        <span>
          <label className="field-label" htmlFor="wiki-tags">Tags</label>
          <Input id="wiki-tags" value={tags} onChange={(e) => touch(setTags)(e.target.value)}
                 placeholder="comma, separated" />
        </span>
      </div>

      {error && <p className="error wiki-editor-error">{error}</p>}

      <div className="wiki-editor-actions">
        <Button type="submit" disabled={busy}>{creating ? "Create page" : "Save"}</Button>
        <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>
        {!creating && <span className="muted">editing v{base}</span>}
      </div>
    </form>
  );
}
