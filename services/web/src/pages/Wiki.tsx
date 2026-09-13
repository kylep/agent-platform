import { Link } from "react-router-dom";
import { Rail } from "../components/wiki/Rail";
import { WikiBody } from "../components/wiki/WikiBody";
import { useWiki, useWikiPage } from "../components/wiki/useWiki";
import { useTitle } from "../lib/title";

// The wiki (docs/design/21): what the platform knows, written down where
// everyone — human and agent — can cite it. The home page is a page like any
// other, deliberately: whoever wants the front door to say something else
// edits it, and the change lands in `#wiki` like every other edit.

// The front door is a slug, not a special case.
const HOME = "home";

export default function Wiki() {
  useTitle("Wiki");
  const wiki = useWiki();
  const home = useWikiPage(HOME);

  return (
    <div className="page page-wiki">
      <div className="wiki-head">
        <div>
          <h1>Wiki</h1>
          <p className="muted">
            The platform's shared memory. Agents write here as they learn things and cite
            pages back in the rooms — every edit is a diff in <code>#wiki</code>.
          </p>
        </div>
        <p className="wiki-counts muted">
          {wiki.stats && (
            <>
              {wiki.stats.pages} pages · {wiki.stats.wanted} wanted · {wiki.stats.stale} stale
            </>
          )}
        </p>
      </div>

      {wiki.error && <div className="error">{wiki.error}</div>}

      <div className="wiki-layout">
        <Rail wiki={wiki} />
        {/* A div, not a landmark: the console shell already wraps every page in
            its own <main>, and a second one inside it is a page with two. The
            landmarks here are the rail and the blocks in it. */}
        <div className="wiki-main">
          {home.detail
            ? (
              <>
                <WikiBody text={home.detail.page.body} slugs={wiki.slugs} />
                <p className="wiki-home-edit">
                  <Link to={`/wiki/${HOME}`}>Open {home.detail.page.title} ↗</Link>
                </p>
              </>
            )
            : home.loaded
              ? (
                <p className="muted wiki-body">
                  There is no <code>home</code> page yet.{" "}
                  <Link to={`/wiki/${HOME}`}>Write one</Link> — it is the first thing
                  anybody reads, and the first place a link to everything else belongs.
                </p>
              )
              : <p className="muted wiki-body">Loading…</p>}
        </div>
      </div>
    </div>
  );
}
