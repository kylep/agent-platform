import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import CreateDocument from "../components/ui-designer/CreateDocument";
import FigmaImport from "../components/ui-designer/FigmaImport";
import { api, apiErrorMessage } from "../api";
import { useTitle } from "../lib/title";
import { UD, type UiDocSummary } from "../lib/uiDesigner";

// UI Designer document list (docs/design/46 §7, Phase 0: read-only).
export default function UiDesigner() {
  useTitle("UI Designer");
  const [docs, setDocs] = useState<UiDocSummary[] | null>(null);
  const [role, setRole] = useState("");
  const admin = role === "admin";
  const canCreate = ["admin", "operator"].includes(role);
  useEffect(() => { api<{ role: string }>("/api/whoami").then((me) => setRole(me.role)).catch(() => setRole("")); }, []);
  const [err, setErr] = useState("");
  const [reload,setReload]=useState(0);
  const invalidate=useCallback(()=>{setDocs(null);setReload((n)=>n+1);},[]);
  useEffect(() => {
    const controller=new AbortController();setErr("");
    api<{ docs: UiDocSummary[] }>(UD,{signal:controller.signal}).then((r) => {if(!controller.signal.aborted)setDocs(r.docs);})
      .catch((e) => {if(!controller.signal.aborted){setDocs(null);setErr(e instanceof Error && /^(403|404):/.test(e.message) ? "Documents are unavailable.":apiErrorMessage(e,"Could not load documents."));}});
    return()=>controller.abort();
  }, [reload]);
  useEffect(()=>{if(typeof EventSource==="undefined")return;const events=new EventSource("/api/ui-designer/events",{withCredentials:true});events.addEventListener("design",(event)=>{try{if(JSON.parse(event.data).type==="access")invalidate();}catch{/* ignore malformed events */}});return()=>events.close();},[invalidate]);
  return (
    <div>
      <div className="page-header"><h1>UI Designer</h1></div>
      {canCreate && <CreateDocument />}
      {admin && <FigmaImport />}
      {err && <p role="alert" className="muted">{err}</p>}
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
