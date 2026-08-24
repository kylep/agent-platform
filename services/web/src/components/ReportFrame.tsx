import { useEffect, useMemo, useRef, useState } from "react";
import reportKitCss from "@ap/ui/report-kit.css?raw";

// The report viewer surface: a SANDBOXED, script-free iframe (report HTML is
// agent-generated from untrusted inputs — the backend sanitizes at ingest,
// this is the second layer; see the sandbox invariant on the iframe below).
// The kit CSS consumes only --ds-* tokens, so we
// snapshot the live token values from the parent document into the srcdoc:
// reports follow the platform theme with zero duplicated color definitions.

const TOKEN_VARS = [
  "--ds-canvas", "--ds-surface", "--ds-raised", "--ds-border", "--ds-text",
  "--ds-text-muted", "--ds-text-subtle", "--ds-accent", "--ds-accent-hover",
  "--ds-on-accent", "--ds-warning", "--ds-danger", "--ds-success", "--ds-link",
  ...Array.from({ length: 8 }, (_, i) => `--ds-chart-${i + 1}`),
];

function tokenSnapshot(): string {
  const cs = getComputedStyle(document.documentElement);
  return TOKEN_VARS.map((v) => `${v}: ${cs.getPropertyValue(v).trim()};`).join(" ");
}

export function useThemeVersion(): number {
  // Re-render consumers when the platform theme toggles (Layout stamps
  // data-theme on <html>).
  const [v, setV] = useState(0);
  useEffect(() => {
    const obs = new MutationObserver(() => setV((x) => x + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return v;
}

export default function ReportFrame({ html, title }: { html: string; title: string }) {
  const themeVersion = useThemeVersion();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(0);
  const srcdoc = useMemo(() => {
    return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;">
<style>:root { ${tokenSnapshot()} } html,body { margin:0; background: var(--ds-canvas); } ${reportKitCss}</style>
</head><body><main class="rk-page">${html}</main></body></html>`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [html, themeVersion]);

  // Size the frame to its content. A fixed height either strands dead space
  // under a short report or clips the tail of a tall one (an iframe never
  // grows to fit, and this one has no scrollbar of its own), so the parent
  // measures the rendered body and sets the height itself.
  //
  // SECURITY INVARIANT — `allow-same-origin` WITHOUT `allow-scripts`, never
  // both, and nothing else. Report HTML is agent-generated from untrusted
  // input (docs/design/11); the two tokens together would let that document
  // script itself out of the sandbox into this origin. Alone,
  // `allow-same-origin` only lifts the opaque-origin barrier so THIS document
  // can read contentDocument — the framed content stays inert (no scripts, no
  // forms, no navigation), which is what makes measuring it safe. The srcdoc
  // CSP (`default-src 'none'`) is the second lock on the same door.
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    let observer: ResizeObserver | undefined;
    const measure = () => {
      const body = frame.contentDocument?.body;
      // contentDocument is null if the sandbox ever loses same-origin — the
      // CSS fallback height covers that rather than collapsing the frame.
      if (!body) return;
      const h = Math.ceil(body.getBoundingClientRect().height);
      if (h > 0) setHeight(h);
    };
    const attach = () => {
      observer?.disconnect();
      measure();
      const body = frame.contentDocument?.body;
      // Late reflows (font swap, image decode, viewport resize re-wrapping
      // text) change the content height after load; the observer catches them.
      if (body) { observer = new ResizeObserver(measure); observer.observe(body); }
    };
    frame.addEventListener("load", attach);
    // srcdoc may have finished loading before this effect ran.
    if (frame.contentDocument?.readyState === "complete") attach();
    return () => { frame.removeEventListener("load", attach); observer?.disconnect(); };
  }, [srcdoc]);

  return (
    <iframe
      ref={frameRef}
      className="report-frame"
      sandbox="allow-same-origin"
      title={title || "report"}
      srcDoc={srcdoc}
      style={height ? { height: `${height}px` } : undefined}
    />
  );
}
