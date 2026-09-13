import { Link } from "react-router-dom";
import { Button } from "@ap/ui/button";
import { participantLabel } from "../../lib/relay";
import { ago } from "../../lib/time";
import { diffLines, type WikiDiff, type WikiHistoryRow } from "../../lib/wiki";
import { Face } from "../relay/Face";

// Every version this page has had, and what each one did. The drawer is the
// wiki's answer to "who wrote that, and why" — which on a platform where most
// of the writing is done by agents is the question people actually have, so
// every row an agent wrote links to the run it wrote it in.

export function History({ rows, diff, diffFor, diffError, current, me, busy,
                         onSelect, onRestore }: {
  rows: WikiHistoryRow[];
  diff: WikiDiff | null;
  /** The version whose diff is open, if any. */
  diffFor: number | null;
  diffError: string | null;
  /** The version the page is at now — the one there is nothing to restore to. */
  current: number;
  me: string | null;
  busy: boolean;
  onSelect: (version: number | null) => void;
  onRestore: (version: number) => void;
}) {
  return (
    <section className="wiki-history" aria-label="History">
      <h2 className="wiki-rail-head">History</h2>
      {rows.length === 0
        ? <p className="muted">Loading…</p>
        : (
          <ul className="wiki-versions">
            {rows.map((row) => {
              const open = row.version === diffFor;
              return (
                <li key={row.id}>
                  <button type="button" className="wiki-version" aria-expanded={open}
                          onClick={() => onSelect(open ? null : row.version)}>
                    <Face participant={row.author} face={row.author_face} size={20} />
                    <span className="wiki-version-n">v{row.version}</span>
                    <span>{participantLabel(row.author, me)}</span>
                    <span className="wiki-lines">
                      <span className="wiki-added">+{row.added}</span>{" "}
                      <span className="wiki-removed">−{row.removed}</span>
                    </span>
                    <span className="muted wiki-version-when">{ago(row.created_at)}</span>
                  </button>
                  {row.reason && <p className="wiki-version-why">“{row.reason}”</p>}
                  {row.run_id && (
                    <p className="wiki-version-run">
                      <Link to={`/runs/${row.run_id}`}>view run ↗</Link>
                    </p>
                  )}
                  {open && (
                    <div className="wiki-version-diff">
                      {diffError
                        ? (
                          // A diff nobody can read is still an answer — and it
                          // must not read as one still arriving.
                          <p className="error wiki-diff-error">
                            This version’s diff could not be read ({diffError}).
                          </p>
                        )
                        : diff
                          ? <Diff diff={diff} />
                          : <p className="muted">Loading the diff…</p>}
                      {row.version !== current && (
                        <Button variant="secondary" disabled={busy}
                                onClick={() => onRestore(row.version)}>
                          Restore this version
                        </Button>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
    </section>
  );
}

/** The unified diff the API computed, line by line. The `+` and `−` are the
 * diff's own characters and stay in the text: colour says it faster, but a
 * line that is only green says nothing to a screen reader or to anybody who
 * cannot tell it from the red one. */
function Diff({ diff }: { diff: WikiDiff }) {
  return (
    <>
      <p className="muted wiki-diff-summary">
        <span className="wiki-added">+{diff.added}</span>{" "}
        <span className="wiki-removed">−{diff.removed}</span>
        {" "}against v{diff.version.version - 1}
      </p>
      <pre className="wiki-diff">
        {diffLines(diff.diff).map((line, i) => (
          // eslint-disable-next-line react/no-array-index-key
          <span key={i} className="wiki-diff-line" data-kind={line.kind}>
            {line.text}{"\n"}
          </span>
        ))}
      </pre>
    </>
  );
}
