import "./Inspector.css";
import { useEffect, useState } from "react";
import { Button } from "@ap/ui/button";
import { Input, Select, Textarea } from "@ap/ui/field";
import type { UiInspection } from "../../lib/uiDesigner";

type Props = { inspection: UiInspection; disabled: boolean; freePosition: boolean; frames?: { id: string; title?: string }[]; onSave: (changes: Record<string, unknown>) => void };
export default function Inspector({ inspection, disabled, freePosition, frames = [], onSave }: Props) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const authored = inspection.authored;
  useEffect(() => { setDraft({}); setError(""); }, [inspection.id, inspection.version]);
  if (!authored) return <p className="muted">This expanded component node cannot be edited directly.</p>;
  const value = (key: string) => draft[key] ?? (typeof authored[key] === "string" || typeof authored[key] === "number" ? String(authored[key]) : "");
  const change = (key: string, next: string) => setDraft((d) => ({ ...d, [key]: next }));
  const layout = authored.layout && typeof authored.layout === "object" ? authored.layout as Record<string, unknown> : {};
  const layoutDirection = draft["layout.dir"] ?? String(layout.dir ?? "");
  const constraints = authored.constraints && typeof authored.constraints === "object" ? authored.constraints as Record<string, unknown> : {};
  const link = authored.link_to && typeof authored.link_to === "object" ? authored.link_to as Record<string, unknown> : {};
  const linkValue = (key: string, fallback = "") => draft[`link.${key}`] ?? String(link[key] ?? fallback);
  function save() {
    try {
      const patch: Record<string, unknown> = {};
      for (const [key, val] of Object.entries(draft)) {
        if (key.startsWith("layout.") || key.startsWith("link.") || key.startsWith("constraints.")) continue;
        if (["x", "y", "w", "h", "opacity", "min_w", "max_w", "min_h", "max_h"].includes(key)) {
          if (!val.trim()) patch[key] = null;
          else if (["w", "h"].includes(key) && ["hug", "fill"].includes(val.trim())) patch[key] = val.trim();
          else { const num = Number(val); if (!Number.isFinite(num) || (["w", "h"].includes(key) && num <= 0)) throw new Error("Enter valid dimensions and coordinates."); if (["min_w", "max_w", "min_h", "max_h"].includes(key) && (num < 0 || num > 100000)) throw new Error("Minimum and maximum sizes must be from 0 to 100000."); patch[key] = num; }
        } else patch[key] = ["fill", "color", "align", "vertical_align", "decoration", "text_case"].includes(key) && !val.trim() ? null : val;
      }
      const layoutChanges = Object.entries(draft).filter(([key]) => key.startsWith("layout."));
      if (layoutChanges.length) {
        const next = { ...layout };
        for (const [key, val] of layoutChanges) {
          const field = key.slice(7);
          if (!val) delete next[field];
          else if (field === "wrap") next[field] = val === "true";
          else if (field === "columns") { const num = Number(val); if (!Number.isInteger(num) || num < 1 || num > 12) throw new Error("Grid columns must be an integer from 1 to 12."); next[field] = num; }
          else if (["gap", "pad", "row_gap", "column_gap"].includes(field)) { const num = Number(val); if (!Number.isFinite(num) || num < 0) throw new Error("Gap and padding must be nonnegative numbers."); next[field] = num; }
          else next[field] = val;
        }
        if (layoutDirection === "grid") { delete next.wrap; delete next.justify; }
        else { delete next.columns; delete next.row_gap; delete next.column_gap; }
        patch.layout = draft["layout.dir"] === "" ? null : Object.keys(next).length ? next : null;
      }
      for (const axis of ["w", "h"]) {
        const min = patch[`min_${axis}`] === undefined ? authored?.[`min_${axis}`] : patch[`min_${axis}`];
        const max = patch[`max_${axis}`] === undefined ? authored?.[`max_${axis}`] : patch[`max_${axis}`];
        if (typeof min === "number" && typeof max === "number" && min > max) throw new Error("Minimum size cannot exceed maximum size.");
      }
      const constraintChanges = Object.entries(draft).filter(([key]) => key.startsWith("constraints."));
      if (constraintChanges.length) {
        const next = { ...constraints };
        for (const [key, val] of constraintChanges) { if (val) next[key.slice(12)] = val; else delete next[key.slice(12)]; }
        patch.constraints = Object.keys(next).length ? next : null;
      }
      if (Object.keys(draft).some((key) => key.startsWith("link."))) {
        if (!linkValue("target")) patch.link_to = null;
        else {
          const trigger = linkValue("trigger", "tap"), transition = linkValue("transition", "instant");
          const next: Record<string, unknown> = { ...link, target: linkValue("target"), trigger, transition };
          for (const [field, fallback, lo, hi] of [["duration_ms", transition === "instant" ? "0" : "200", 0, 2000], ["delay_ms", "1000", 100, 60000]] as const) {
            if (field === "delay_ms" && trigger !== "after-delay") { delete next.delay_ms; continue; }
            const raw = linkValue(field, fallback), num = Number(raw);
            if (!raw.trim() || !Number.isInteger(num) || num < lo || num > hi) throw new Error(`${field === "delay_ms" ? "Delay" : "Duration"} must be an integer from ${lo} to ${hi} ms.`);
            next[field] = num;
          }
          patch.link_to = next;
        }
      }
      setError(""); onSave(patch);
    } catch (e) { setError(e instanceof Error ? e.message : "Check the form values."); }
  }
  return <section aria-label="Edit selected node"><p className="muted">{inspection.id} · {inspection.type}</p>
    <p className="ud-edit-field"><label htmlFor="ud-title">Title</label><Input id="ud-title" value={value("title")} onChange={(e) => change("title", e.target.value)} disabled={disabled} /></p>
    {(["w", "h", ...(freePosition ? ["x", "y"] : [])]).map((key) => <p className="ud-edit-field" key={key}><label htmlFor={`ud-${key}`}>{({ w: "Width", h: "Height", x: "X position", y: "Y position" })[key]}</label><Input id={`ud-${key}`} value={value(key)} onChange={(e) => change(key, e.target.value)} disabled={disabled} inputMode="decimal" placeholder={String(authored[key] ?? "Automatic")} /></p>)}
    <fieldset disabled={disabled}><legend>Size bounds</legend>
      {([["min_w","Minimum width"],["max_w","Maximum width"],["min_h","Minimum height"],["max_h","Maximum height"]] as const).map(([key,label])=><p className="ud-edit-field" key={key}><label htmlFor={`ud-${key}`}>{label}</label><Input id={`ud-${key}`} inputMode="decimal" value={value(key)} onChange={(e)=>change(key,e.target.value)} placeholder="Unbounded" /></p>)}
    </fieldset>
    <fieldset disabled={disabled || !freePosition}><legend>Resize constraints</legend>
      {(["horizontal","vertical"] as const).map((axis)=><p className="ud-edit-field" key={axis}><label htmlFor={`ud-constraint-${axis}`}>{axis === "horizontal" ? "Horizontal resize constraint":"Vertical resize constraint"}</label><Select id={`ud-constraint-${axis}`} value={draft[`constraints.${axis}`] ?? String(constraints[axis] ?? "")} onChange={(e)=>change(`constraints.${axis}`,e.target.value)}><option value="">Default (start)</option>{["start","end","center","stretch","scale"].map((option)=><option key={option} value={option}>{option}</option>)}</Select></p>)}
      <p className="muted">Constraints affect free-positioned or absolute children when their parent is resized with numeric dimensions.</p>
    </fieldset>
    {inspection.type !== "text" && <p className="ud-edit-field"><label htmlFor="ud-fill">Fill (colour or token)</label><Input id="ud-fill" value={value("fill")} onChange={(e) => change("fill", e.target.value)} disabled={disabled} placeholder={authored.fill && typeof authored.fill === "object" ? "Mixed paints (preserved unless edited)" : "#rrggbb or $color.name"} /></p>}
    {inspection.type === "text" && <><p className="ud-edit-field"><label htmlFor="ud-text">Text</label><Textarea id="ud-text" value={draft.text ?? (typeof authored.text === "string" ? authored.text : inspection.text ?? "")} onChange={(e) => change("text", e.target.value)} disabled={disabled} rows={4} /></p>
      {Array.isArray(authored.text) && <p className="muted">Editing text replaces its inline formatting. Undo restores it.</p>}
      <p className="ud-edit-field"><label htmlFor="ud-color">Text colour or token</label><Input id="ud-color" value={value("color")} onChange={(e) => change("color", e.target.value)} disabled={disabled} /></p>
      <fieldset disabled={disabled}><legend>Typography</legend>
        {([
          ["align", "Text horizontal alignment", [["left","Left"],["center","Center"],["right","Right"],["justify","Justified"]]],
          ["vertical_align", "Text vertical alignment", [["top","Top"],["center","Center"],["bottom","Bottom"]]],
          ["decoration", "Text decoration", [["none","None"],["underline","Underline"],["line-through","Line through"]]],
          ["text_case", "Text case", [["none","Original case"],["uppercase","Uppercase"],["lowercase","Lowercase"],["capitalize","Capitalize"]]],
        ] as const).map(([key,label,options])=><p className="ud-edit-field" key={key}><label htmlFor={`ud-text-${key}`}>{label}</label><Select id={`ud-text-${key}`} value={value(key)} onChange={(e)=>change(key,e.target.value)}><option value="">Default</option>{options.map(([option,text])=><option key={option} value={option}>{text}</option>)}</Select></p>)}
        <p className="muted">Case changes affect appearance; the original text stays unchanged. Vertical alignment uses the available height in a fixed-height text box.</p>
      </fieldset>
    </>}
    {inspection.type === "frame" && <fieldset disabled={disabled}><legend>Layout</legend>
      <p className="ud-edit-field"><label htmlFor="ud-direction">Direction</label><Select id="ud-direction" value={draft["layout.dir"] ?? String(layout.dir ?? "")} onChange={(e) => change("layout.dir", e.target.value)}><option value="">Free positioning</option><option value="row">Row</option><option value="column">Column</option><option value="grid">Grid</option></Select></p>
      {["gap", "pad"].map((key) => <p className="ud-edit-field" key={key}><label htmlFor={`ud-layout-${key}`}>{key === "gap" ? "Gap" : "Padding"}</label><Input id={`ud-layout-${key}`} inputMode="decimal" value={draft[`layout.${key}`] ?? (typeof layout[key] === "number" ? String(layout[key]) : "")} onChange={(e) => change(`layout.${key}`, e.target.value)} placeholder={Array.isArray(layout[key]) ? "Mixed padding (preserved unless edited)" : "0"} /></p>)}
      {layoutDirection === "grid" ? <>
        {([["columns","Grid columns","2"],["row_gap","Row gap","Uses gap"],["column_gap","Column gap","Uses gap"]] as const).map(([key,label,placeholder])=><p className="ud-edit-field" key={key}><label htmlFor={`ud-grid-${key}`}>{label}</label><Input id={`ud-grid-${key}`} inputMode="numeric" value={draft[`layout.${key}`] ?? (typeof layout[key] === "number" ? String(layout[key]):"")} placeholder={placeholder} onChange={(e)=>change(`layout.${key}`,e.target.value)} /></p>)}
      </> : layoutDirection && <p className="ud-edit-field"><label htmlFor="ud-layout-wrap">Wrap</label><Select id="ud-layout-wrap" value={draft["layout.wrap"] ?? (typeof layout.wrap === "boolean" ? String(layout.wrap):"")} onChange={(e)=>change("layout.wrap",e.target.value)}><option value="">Default</option><option value="true">Wrap</option><option value="false">No wrap</option></Select></p>}
      {([['align', ['start','center','end','stretch']], ['justify', ['start','center','end','space-between']]] as const).filter(([key]) => layoutDirection !== "grid" || key !== "justify").map(([key, choices]) => <p className="ud-edit-field" key={key}><label htmlFor={`ud-layout-${key}`}>{key === 'align' ? 'Align' : 'Justify'}</label><Select id={`ud-layout-${key}`} value={draft[`layout.${key}`] ?? String(layout[key] ?? '')} onChange={(e) => change(`layout.${key}`,e.target.value)}><option value="">Default</option>{choices.map((v) => <option key={v} value={v}>{v}</option>)}</Select></p>)}
    </fieldset>}
    <fieldset disabled={disabled}><legend>Prototype link</legend>
      <p className="ud-edit-field"><label htmlFor="ud-link-target">Destination frame</label><Select id="ud-link-target" value={linkValue("target")} onChange={(e)=>change("link.target",e.target.value)}><option value="">No link</option>{frames.map((f)=><option key={f.id} value={f.id}>{f.title || f.id}</option>)}</Select></p>
      {linkValue("target") && <>
        <p className="ud-edit-field"><label htmlFor="ud-link-trigger">Trigger</label><Select id="ud-link-trigger" value={linkValue("trigger","tap")} onChange={(e)=>change("link.trigger",e.target.value)}><option value="tap">Tap</option><option value="hover">Hover</option><option value="after-delay">After delay</option></Select></p>
        <p className="ud-edit-field"><label htmlFor="ud-link-transition">Transition</label><Select id="ud-link-transition" value={linkValue("transition","instant")} onChange={(e)=>change("link.transition",e.target.value)}><option value="instant">Instant</option><option value="dissolve">Dissolve</option><option value="slide">Slide</option></Select></p>
        <p className="ud-edit-field"><label htmlFor="ud-link-duration">Transition duration (ms)</label><Input id="ud-link-duration" inputMode="numeric" value={linkValue("duration_ms",linkValue("transition","instant")==="instant" ? "0":"200")} onChange={(e)=>change("link.duration_ms",e.target.value)} /></p>
        {linkValue("trigger","tap")==="after-delay" && <p className="ud-edit-field"><label htmlFor="ud-link-delay">Delay (ms)</label><Input id="ud-link-delay" inputMode="numeric" value={linkValue("delay_ms","1000")} onChange={(e)=>change("link.delay_ms",e.target.value)} /></p>}
      </>}
    </fieldset>
    {error && <p role="alert">{error}</p>}
    <Button disabled={disabled || !Object.keys(draft).length} onClick={save}>Save node</Button>
  </section>;
}
