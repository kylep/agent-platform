import { Button } from "@ap/ui/button";
import { Input, Select } from "@ap/ui/field";

type Draft = Record<string, string>;
const object = (v: unknown): Record<string, unknown> | null => v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null;
export default function PaintControls({ fill, draft, change, disabled }: { fill: unknown; draft: Draft; change: (key: string, value: string) => void; disabled: boolean }) {
  const paints = Array.isArray(fill) ? fill : [fill];
  return <fieldset disabled={disabled}><legend>Gradient paints</legend>
    {paints.map((entry, index) => {
      const paint = object(entry);
      if (!paint || typeof paint.gradient !== "string") return null;
      const prefix = `paint.${index}.`;
      const stops: [number | string, string][] = draft[prefix+"stops"] ? JSON.parse(draft[prefix+"stops"]) : Array.isArray(paint.stops) ? paint.stops : [];
      const updateStops = (next: [number | string, string][]) => change(prefix+"stops", JSON.stringify(next));
      return <fieldset key={index}><legend>Fill layer {index + 1}</legend>
        <p className="ud-edit-field"><label htmlFor={`ud-paint-${index}-kind`}>Gradient type, layer {index + 1}</label><Select id={`ud-paint-${index}-kind`} value={draft[prefix+"gradient"] ?? paint.gradient} onChange={(e)=>change(prefix+"gradient",e.target.value)}>{[["linear","Linear"],["radial","Radial"],["angular","Conic"],["diamond","Diamond"]].map(([value,label])=><option key={value} value={value}>{label}</option>)}</Select></p>
        <p className="ud-edit-field"><label htmlFor={`ud-paint-${index}-angle`}>Gradient angle, layer {index + 1}</label><Input id={`ud-paint-${index}-angle`} inputMode="decimal" value={draft[prefix+"angle"] ?? String(paint.angle ?? "")} placeholder="0 degrees" onChange={(e)=>change(prefix+"angle",e.target.value)} /></p>
        <fieldset><legend>Colour stops, layer {index + 1}</legend>
          {stops.map((stop, stopIndex) => <fieldset key={stopIndex}><legend>Stop {stopIndex + 1}</legend>
            <p className="ud-edit-field"><label htmlFor={`ud-paint-${index}-${stopIndex}-position`}>Position, layer {index + 1} stop {stopIndex + 1}</label><Input id={`ud-paint-${index}-${stopIndex}-position`} inputMode="decimal" value={String(stop[0])} onChange={(e)=>updateStops(stops.map((s,i)=>i===stopIndex ? [e.target.value,s[1]] : s))} /></p>
            <p className="ud-edit-field"><label htmlFor={`ud-paint-${index}-${stopIndex}-colour`}>Colour or token, layer {index + 1} stop {stopIndex + 1}</label><Input id={`ud-paint-${index}-${stopIndex}-colour`} value={stop[1]} maxLength={256} onChange={(e)=>updateStops(stops.map((s,i)=>i===stopIndex ? [s[0],e.target.value] : s))} /></p>
            <Button disabled={disabled || stops.length<=2} onClick={()=>updateStops(stops.filter((_,i)=>i!==stopIndex))}>Remove stop {stopIndex + 1}, layer {index + 1}</Button>
          </fieldset>)}
          <Button disabled={disabled || stops.length>=8} onClick={()=>updateStops([...stops,[1,stops.at(-1)?.[1] || ""]])}>Add stop, layer {index + 1}</Button>
        </fieldset>
      </fieldset>;
    })}
    <p className="muted">Use 2–8 colour stops in ascending order, with positions from 0 to 1. Colours accept hex values or colour tokens. Other fill layers and paint settings stay unchanged.</p>
  </fieldset>;
}
