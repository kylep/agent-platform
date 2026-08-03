import { useEffect, useMemo, useState } from "react";
import reportKitCss from "@ap/ui/report-kit.css?raw";

// The report viewer surface: a SANDBOXED, script-free iframe (report HTML is
// agent-generated from untrusted inputs — the backend sanitizes at ingest,
// this is the second layer). The kit CSS consumes only --ds-* tokens, so we
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
  const srcdoc = useMemo(() => {
    return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;">
<style>:root { ${tokenSnapshot()} } html,body { margin:0; background: var(--ds-canvas); } ${reportKitCss}</style>
</head><body><main class="rk-page">${html}</main></body></html>`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [html, themeVersion]);
  return (
    <iframe
      className="report-frame"
      sandbox=""
      title={title || "report"}
      srcDoc={srcdoc}
    />
  );
}
