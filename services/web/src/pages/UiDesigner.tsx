import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import FigmaImport from "../components/ui-designer/FigmaImport";
import { api } from "../api";
import { useTitle } from "../lib/title";
import { UD, type UiDocSummary } from "../lib/uiDesigner";

// UI Designer document list (docs/design/46 §7, Phase 0: read-only).
export default function UiDesigner() {
  useTitle("UI Designer");
  const [docs, setDocs] = useState<UiDocSummary[] | null>(null);
  const [admin, setAdmin] = useState(false);
  useEffect(() => { api<{ role: string }>("/api/whoami").then((me) => setAdmin(me.role === "admin")).catch(() => setAdmin(false)); }, []);
  const [err, setErr] = useState("");
  useEffect(() => {
    api<{ docs: UiDocSummary[] }>(UD).then((r) => setDocs(r.docs))
      .catch((e) => setErr(String(e.message ?? e)));
  }, []);
  return (
    <div>
      <div className="page-header"><h1>UI Designer</h1></div>
      {admin && <FigmaImport />}
      {err && <p role="alert" className="muted">Could not load documents: {err}</p>}
      {docs && docs.length === 0 && <p className="muted">No documents yet.</p>}
      {docs && docs.length > 0 && (
        <table>
          <thead><tr><th>Title</th><th>Slug</th><th>Updated</th><th>Author</th></tr></thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.slug}>
                <td><Link to={`/ui-designer/${encodeURIComponent(d.slug)}`}>{d.title || d.slug}</Link></td>
                <td className="muted">{d.slug}</td>
                <td className="muted">{d.updated_at ? new Date(d.updated_at).toLocaleString() : ""}</td>
                <td>{d.owner}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
