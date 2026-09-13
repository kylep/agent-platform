import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { FormDialog } from "@ap/ui/dialog";
import { Input } from "@ap/ui/field";
import type { Memory } from "../../api";
import { slugify, SLUG_RE, WikiWriteError, type WikiPage } from "../../lib/wiki";

// Promoting a memory into a page (docs/design/21). A memory is one agent's
// private note; a page is a fact every agent can read and every human can
// correct. Promotion is the moment somebody decides a note has hardened into
// the second kind of thing — which is why it is a click and never a job
// guessing.
//
// The memory itself is left exactly as it was. Nothing is moved: the agent
// keeps its note, and the wiki gains a page that says where it came from.

/** One promotion, with the refusal left intact.
 *
 * Deliberately not `api()`: that helper flattens a failure into
 * `409: {json…}` — the JSON on screen, which is the one thing a dialog must
 * never show — and what this form needs out of a 409 is whether it carried a
 * version, since that is the difference between "the name is taken" and "the
 * page has moved on since". The shape is `lib/wiki`'s own `write`, down to
 * sending a 401 to the login page. */
async function promote(body: { memory_id: string; slug: string; title: string }):
  Promise<WikiPage> {
  const res = await fetch("/api/wiki/promote", {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new WikiWriteError(401, "not signed in");
  }
  const text = await res.text();
  let parsed: unknown = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { /* not json */ }
  if (res.ok) return parsed as WikiPage;
  const payload = (parsed ?? {}) as Record<string, unknown>;
  // FastAPI answers a validation failure with a LIST under `detail`; a string
  // is what every refusal this dialog can cause looks like. Anything else is
  // reported as the status, never as a JSON blob on screen.
  const detail = typeof payload.detail === "string"
    ? payload.detail : `the wiki refused this promotion (${res.status})`;
  throw new WikiWriteError(res.status, detail, {
    version: typeof payload.current_version === "number" ? payload.current_version : null,
  });
}

/** What a refused promotion says. The three the server actually produces are
 * three different next moves — it is not yours, the name is taken, the page
 * has been edited since — and a sentence that does not say which one leaves
 * the reader pressing the same button again. */
function refusal(err: unknown, slug: string): ReactNode {
  if (!(err instanceof WikiWriteError)) {
    return err instanceof Error ? err.message : "The memory was not promoted.";
  }
  if (err.status === 403) return "You can only promote your own memories.";
  if (err.status === 409 && err.currentVersion === null) {
    return <>A page called <code>{slug}</code> already exists — pick another slug.</>;
  }
  if (err.status === 409) {
    return (
      <>
        That page was edited since it was promoted —{" "}
        <Link to={`/wiki/${slug}`}>edit it directly</Link>.
      </>
    );
  }
  return err.message;
}

/** What the page will be called, before anybody edits either field. The slug
 * is the backend's `slugify` of the key, so a page promoted here and the same
 * memory promoted by the agent itself land on ONE page rather than two. */
function seed(memory: Memory | null): { slug: string; title: string } {
  const key = memory?.key ?? "";
  return { slug: slugify(key), title: key };
}

export function PromoteDialog({ memory, onClose, onPromoted }: {
  /** The memory being promoted; null when the dialog is closed. */
  memory: Memory | null;
  onClose: () => void;
  /** The page the server created or updated — the only honest thing to badge
   * the table with, since it carries the slug the server actually chose. */
  onPromoted: (page: WikiPage) => void;
}) {
  const [slug, setSlug] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ReactNode>(null);
  // Focused when the slug is the thing that was refused: the field IS the fix,
  // and a dialog that says "pick another slug" and leaves the caret elsewhere
  // makes the reader hunt for it.
  const slugField = useRef<HTMLInputElement | null>(null);
  // Read by the effect below without subscribing it to the value: what the
  // dialog seeds from is the row that was clicked, once.
  const row = useRef(memory);
  row.current = memory;
  const open = memory !== null;

  // Re-seeded when the dialog OPENS and not once more, so a re-render never
  // wipes a slug somebody is halfway through typing.
  useEffect(() => {
    if (!open) return;
    const from = seed(row.current);
    setSlug(from.slug);
    setTitle(from.title);
    setError(null);
  }, [open]);

  // A slug is a URL and a `[[link]]` target, so the grammar is the same one
  // the backend enforces — refused here rather than after a round trip.
  const ok = SLUG_RE.test(slug.trim());

  async function submit() {
    if (!memory || !ok) return;
    setBusy(true);
    setError(null);
    const named = slug.trim();
    try {
      onPromoted(await promote({
        memory_id: memory.id, slug: named, title: title.trim() || named,
      }));
      onClose();
    } catch (err) {
      setError(refusal(err, named));
      // A taken name is the one refusal the reader fixes in this box, so the
      // dialog stays open on the field rather than closing over the answer.
      if (err instanceof WikiWriteError && err.status === 409
          && err.currentVersion === null) {
        slugField.current?.focus();
        slugField.current?.select();
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <FormDialog open={open} title="Promote to wiki"
                submitLabel={busy ? "Promoting…" : "Promote"}
                disabled={busy || !ok} onSubmit={submit} onCancel={onClose}>
      <p className="muted">
        {memory?.agent ?? "The agent"} keeps this memory either way. The wiki gains a
        page saying where the fact came from, which every agent can read and cite as{" "}
        <code>[[{slug || "slug"}]]</code>.
      </p>
      <label className="field-label" htmlFor="promote-slug">Slug</label>
      <Input id="promote-slug" ref={slugField} value={slug} maxLength={64}
             onChange={(e) => setSlug(e.target.value)}
             placeholder="lower-case-words-joined-by-hyphens" />
      <label className="field-label" htmlFor="promote-title">Page title</label>
      <Input id="promote-title" value={title} maxLength={120}
             onChange={(e) => setTitle(e.target.value)}
             placeholder="what the page is called" />
      {slug.trim() && !ok && (
        <p className="muted">
          A slug is lower-case letters, digits and hyphens — it has to survive being
          typed into a URL and into <code>[[brackets]]</code>.
        </p>
      )}
      {error && <div className="error">{error}</div>}
    </FormDialog>
  );
}
