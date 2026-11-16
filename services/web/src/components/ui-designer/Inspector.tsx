import "./Inspector.css";
import { useEffect, useState } from "react";
import { Button } from "@ap/ui/button";
import { Input, Select, Textarea } from "@ap/ui/field";
import type { UiInspection } from "../../lib/uiDesigner";

type Props = { inspection: UiInspection; disabled: boolean; freePosition: boolean; onSave: (changes: Record<string, unknown>) => void };
export default function Inspector({ inspection, disabled, freePosition, onSave }: Props) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const authored = inspection.authored;
  useEffect(() => { setDraft({}); setError(""); }, [inspection.id, inspection.version]);
  if (!authored) return <p className="muted">This expanded component node cannot be edited directly.</p>;
  const value = (key: string) => draft[key] ?? (typeof authored[key] === "string" || typeof authored[key] === "number" ? String(authored[key]) : "");
  const change = (key: string, next: string) => setDraft((d) => ({ ...d, [key]: next }));
  const layout = authored.layout && typeof authored.layout === "object" ? authored.layout as Record<string, unknown> : {};
  function save() {
    try {
      const patch: Record<string, unknown> = {};
      for (const [key, val] of Object.entries(draft)) {
        if (key.startsWith("layout.")) continue;
        if (["x", "y", "w", "h", "opacity"].includes(key)) {
          if (!val.trim()) patch[key] = null;
          else if (["w", "h"].includes(key) && ["hug", "fill"].includes(val.trim())) patch[key] = val.trim();
          else { const num = Number(val); if (!Number.isFinite(num) || (["w", "h"].includes(key) && num <= 0)) throw new Error("Enter valid dimensions and coordinates."); patch[key] = num; }
        } else patch[key] = ["fill", "color"].includes(key) && !val.trim() ? null : val;
      }
      const layoutChanges = Object.entries(draft).filter(([key]) => key.startsWith("layout."));
      if (layoutChanges.length) {
        const next = { ...layout };
        for (const [key, val] of layoutChanges) {
          const field = key.slice(7);
          if (!val) delete next[field];
          else if (["gap", "pad"].includes(field)) { const num = Number(val); if (!Number.isFinite(num) || num < 0) throw new Error("Gap and padding must be nonnegative numbers."); next[field] = num; }
          else next[field] = val;
        }
        patch.layout = draft["layout.dir"] === "" ? null : Object.keys(next).length ? next : null;
      }
      setError(""); onSave(patch);
    } catch (e) { setError(e instanceof Error ? e.message : "Check the form values."); }
  }
  return <section aria-label="Edit selected node"><p className="muted">{inspection.id} · {inspection.type}</p>
    <p className="ud-edit-field"><label htmlFor="ud-title">Title</label><Input id="ud-title" value={value("title")} onChange={(e) => change("title", e.target.value)} disabled={disabled} /></p>
    {(["w", "h", ...(freePosition ? ["x", "y"] : [])]).map((key) => <p className="ud-edit-field" key={key}><label htmlFor={`ud-${key}`}>{({ w: "Width", h: "Height", x: "X position", y: "Y position" })[key]}</label><Input id={`ud-${key}`} value={value(key)} onChange={(e) => change(key, e.target.value)} disabled={disabled} inputMode="decimal" placeholder={String(authored[key] ?? "Automatic")} /></p>)}
    {inspection.type !== "text" && <p className="ud-edit-field"><label htmlFor="ud-fill">Fill (colour or token)</label><Input id="ud-fill" value={value("fill")} onChange={(e) => change("fill", e.target.value)} disabled={disabled} placeholder={authored.fill && typeof authored.fill === "object" ? "Mixed paints (preserved unless edited)" : "#rrggbb or $color.name"} /></p>}
    {inspection.type === "text" && <><p className="ud-edit-field"><label htmlFor="ud-text">Text</label><Textarea id="ud-text" value={draft.text ?? (typeof authored.text === "string" ? authored.text : inspection.text ?? "")} onChange={(e) => change("text", e.target.value)} disabled={disabled} rows={4} /></p>
      {Array.isArray(authored.text) && <p className="muted">Editing text replaces its inline formatting. Undo restores it.</p>}
      <p className="ud-edit-field"><label htmlFor="ud-color">Text colour or token</label><Input id="ud-color" value={value("color")} onChange={(e) => change("color", e.target.value)} disabled={disabled} /></p></>}
    {inspection.type === "frame" && <fieldset disabled={disabled}><legend>Layout</legend>
      <p className="ud-edit-field"><label htmlFor="ud-direction">Direction</label><Select id="ud-direction" value={draft["layout.dir"] ?? String(layout.dir ?? "")} onChange={(e) => change("layout.dir", e.target.value)}><option value="">Free positioning</option><option value="row">Row</option><option value="column">Column</option></Select></p>
      {["gap", "pad"].map((key) => <p className="ud-edit-field" key={key}><label htmlFor={`ud-layout-${key}`}>{key === "gap" ? "Gap" : "Padding"}</label><Input id={`ud-layout-${key}`} inputMode="decimal" value={draft[`layout.${key}`] ?? (typeof layout[key] === "number" ? String(layout[key]) : "")} onChange={(e) => change(`layout.${key}`, e.target.value)} placeholder={Array.isArray(layout[key]) ? "Mixed padding (preserved unless edited)" : "0"} /></p>)}
      {([['align', ['start','center','end','stretch']], ['justify', ['start','center','end','space-between']]] as const).map(([key, choices]) => <p className="ud-edit-field" key={key}><label htmlFor={`ud-layout-${key}`}>{key === 'align' ? 'Align' : 'Justify'}</label><Select id={`ud-layout-${key}`} value={draft[`layout.${key}`] ?? String(layout[key] ?? '')} onChange={(e) => change(`layout.${key}`,e.target.value)}><option value="">Default</option>{choices.map((v) => <option key={v} value={v}>{v}</option>)}</Select></p>)}
    </fieldset>}
    {error && <p role="alert">{error}</p>}
    <Button disabled={disabled || !Object.keys(draft).length} onClick={save}>Save node</Button>
  </section>;
}
