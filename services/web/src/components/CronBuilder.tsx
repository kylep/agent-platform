import { useEffect, useRef, useState } from "react";
import { Input, Select } from "@ap/ui/field";
import { localFireTime, useCronPreview } from "../lib/cron";

// A schedule builder for the places a cron expression is edited: the agent's
// entrypoint crons and the Jobs form. Raw cron is a fine STORAGE format and a
// terrible INPUT one — `0 9 * * 1-5` is unreadable until you already know it,
// and the field gave no signal until a run failed to happen.
//
// This is purely an input method. The value in and out is still the 5-field
// cron string the definition carries; nothing about the stored format changes.
// An expression that matches a preset shape exactly opens in that preset, and
// anything else — a step over a range, an nth-weekday — opens in Custom with
// the raw field, so the builder can never be a reason a schedule can't be
// expressed.

type Frequency = "minutes" | "hourly" | "daily" | "weekly" | "monthly" | "custom";

const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: "minutes", label: "Every N minutes" },
  { value: "hourly", label: "Hourly" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "custom", label: "Custom cron" },
];

// Cron numbers Sunday first, and so does every weekday picker anyone has used.
const WEEKDAYS = [
  { value: 0, short: "Sun", name: "Sunday" },
  { value: 1, short: "Mon", name: "Monday" },
  { value: 2, short: "Tue", name: "Tuesday" },
  { value: 3, short: "Wed", name: "Wednesday" },
  { value: 4, short: "Thu", name: "Thursday" },
  { value: 5, short: "Fri", name: "Friday" },
  { value: 6, short: "Sat", name: "Saturday" },
];

// Every preset carries every field, so switching frequency keeps what you had
// set on the other side of the switch instead of resetting to a default.
type Preset = {
  freq: Frequency;
  every: number;      // "every N minutes"
  minute: number;     // hourly: minute past the hour
  time: string;       // daily/weekly/monthly: "HH:MM"
  days: number[];     // weekly
  day: number;        // monthly: day of the month
  raw: string;        // custom: the expression, verbatim
};

const DEFAULTS: Preset = {
  freq: "custom", every: 15, minute: 0, time: "09:00", days: [1], day: 1, raw: "",
};

/** Where a form that CREATES a schedule in one deliberate act starts — the
 * Jobs form, which won't submit without a name and a prompt anyway. An agent's
 * cron rows deliberately do NOT use this: "+ Add cron" adds an EMPTY row, so
 * an accidental add followed by Save can't quietly arm a live daily run. An
 * empty row fails validation exactly as it did before the builder existed. */
export const DEFAULT_CRON = "0 9 * * *";

const pad = (n: number) => String(n).padStart(2, "0");

function hhmm(time: string): [number, number] {
  const parts = /^(\d{1,2}):(\d{2})$/.exec(time);
  if (!parts) return [9, 0];
  return [Math.min(23, Number(parts[1])), Math.min(59, Number(parts[2]))];
}

function parseDays(field: string): number[] | null {
  const days = new Set<number>();
  for (const part of field.split(",")) {
    const range = /^(\d)-(\d)$/.exec(part);
    if (range) {
      const [from, to] = [Number(range[1]) % 7, Number(range[2]) % 7];
      if (from > to) return null;
      for (let d = from; d <= to; d++) days.add(d);
      continue;
    }
    if (!/^\d$/.test(part)) return null;
    days.add(Number(part) % 7);      // cron accepts 7 for Sunday as well as 0
  }
  return days.size ? [...days].sort((a, b) => a - b) : null;
}

/** Read an expression back into the preset that would produce it. Anything
 * that isn't an exact match for a preset shape lands in Custom — a near-miss
 * silently rewritten to the nearest preset would change what fires. */
function parseCron(cron: string): Preset {
  const raw = cron.trim();
  const custom: Preset = { ...DEFAULTS, raw };
  // An empty row opens on the commonest shape so the controls are usable
  // immediately — but the VALUE stays empty until a control is touched, so
  // merely adding a row commits to no schedule at all.
  if (!raw) return { ...DEFAULTS, freq: "daily" };
  const fields = raw.split(/\s+/);
  if (fields.length !== 5) return custom;
  const [m, h, dom, month, dow] = fields;
  if (month !== "*") return custom;

  const num = (s: string) => (/^\d{1,2}$/.test(s) ? Number(s) : null);
  const step = /^\*\/(\d{1,2})$/.exec(m);
  if (step && h === "*" && dom === "*" && dow === "*") {
    const every = Number(step[1]);
    return every >= 1 && every <= 59 ? { ...custom, freq: "minutes", every } : custom;
  }

  const minute = num(m);
  if (minute === null || minute > 59) return custom;
  if (h === "*" && dom === "*" && dow === "*") return { ...custom, freq: "hourly", minute };

  const hour = num(h);
  if (hour === null || hour > 23) return custom;
  const time = `${pad(hour)}:${pad(minute)}`;
  if (dom === "*" && dow === "*") return { ...custom, freq: "daily", time };
  if (dom === "*") {
    const days = parseDays(dow);
    return days ? { ...custom, freq: "weekly", time, days } : custom;
  }
  if (dow === "*") {
    const day = num(dom);
    if (day !== null && day >= 1 && day <= 31) return { ...custom, freq: "monthly", time, day };
  }
  return custom;
}

/** The canonical 5-field cron a preset stands for. */
function serializeCron(p: Preset): string {
  const [hour, minute] = hhmm(p.time);
  switch (p.freq) {
    case "minutes": return `*/${p.every} * * * *`;
    case "hourly": return `${p.minute} * * * *`;
    case "daily": return `${minute} ${hour} * * *`;
    case "weekly": return `${minute} ${hour} * * ${(p.days.length ? p.days : [1]).join(",")}`;
    case "monthly": return `${minute} ${hour} ${p.day} * *`;
    default: return p.raw.trim();
  }
}

// A bounded integer field that keeps its own text, so clearing it to retype
// doesn't snap the schedule back to a value nobody asked for.
function NumberBox({ label, value, min, max, onChange }: {
  label: string; value: number; min: number; max: number; onChange: (n: number) => void;
}) {
  const [text, setText] = useState(String(value));
  const own = useRef(String(value));
  useEffect(() => {
    if (String(value) !== own.current) { setText(String(value)); own.current = String(value); }
  }, [value]);
  return (
    <Input className="cron-num" type="number" aria-label={label} min={min} max={max} value={text}
           onChange={(e) => {
             setText(e.target.value);
             own.current = e.target.value;
             const n = Number(e.target.value);
             if (Number.isFinite(n) && e.target.value.trim() !== "" && n >= min && n <= max) {
               onChange(Math.trunc(n));
             }
           }} />
  );
}

export function CronBuilder({ value, timezone = "", onChange, label = "Cron schedule" }: {
  value: string; timezone?: string; onChange: (cron: string) => void; label?: string;
}) {
  const [preset, setPreset] = useState<Preset>(() => parseCron(value));
  // What this component last put on the wire. An incoming value that isn't it
  // came from somewhere else (a different agent loaded, a draft reset) and has
  // to be re-parsed; our own echo must not be, or every edit would round-trip
  // through the parser and fight the controls.
  const emitted = useRef(value);
  useEffect(() => {
    if (value !== emitted.current) { setPreset(parseCron(value)); emitted.current = value; }
  }, [value]);

  function update(next: Preset) {
    setPreset(next);
    const cron = serializeCron(next);
    emitted.current = cron;
    onChange(cron);
  }

  function setFrequency(freq: Frequency) {
    // Custom opens on whatever the presets were describing, so switching to it
    // is "show me the cron", not "start over".
    update(freq === "custom" ? { ...preset, freq, raw: serializeCron(preset) } : { ...preset, freq });
  }

  const preview = useCronPreview(value, timezone);
  const zone = timezone || "UTC";

  return (
    <div className="cron-builder">
      <div className="cron-controls">
        <Select aria-label={`${label} frequency`} value={preset.freq}
                onChange={(e) => setFrequency(e.target.value as Frequency)}>
          {FREQUENCIES.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
        </Select>

        {preset.freq === "minutes" && (
          <>
            <span className="muted cron-word">every</span>
            <NumberBox label="Minutes between runs" value={preset.every} min={1} max={59}
                       onChange={(every) => update({ ...preset, every })} />
            <span className="muted cron-word">minutes</span>
          </>
        )}

        {preset.freq === "hourly" && (
          <>
            <span className="muted cron-word">at minute</span>
            <NumberBox label="Minute of the hour" value={preset.minute} min={0} max={59}
                       onChange={(minute) => update({ ...preset, minute })} />
          </>
        )}

        {preset.freq === "monthly" && (
          <>
            <span className="muted cron-word">on day</span>
            <NumberBox label="Day of the month" value={preset.day} min={1} max={31}
                       onChange={(day) => update({ ...preset, day })} />
          </>
        )}

        {(preset.freq === "daily" || preset.freq === "weekly" || preset.freq === "monthly") && (
          <>
            <span className="muted cron-word">at</span>
            <Input className="cron-time" type="time" aria-label="Time of day" value={preset.time}
                   onChange={(e) => update({ ...preset, time: e.target.value || "09:00" })} />
          </>
        )}

        {preset.freq === "custom" && (
          <Input className="cron-raw" aria-label="Cron expression" value={preset.raw}
                 placeholder="0 9 * * *" spellCheck={false}
                 onChange={(e) => update({ ...preset, raw: e.target.value })} />
        )}
      </div>

      {preset.freq === "weekly" && (
        <div className="toggle-row cron-days">
          {WEEKDAYS.map((d) => {
            const on = preset.days.includes(d.value);
            return (
              <label key={d.value} className={on ? "check-item on" : "check-item"}>
                <input type="checkbox" checked={on} aria-label={d.name}
                       onChange={() => update({
                         ...preset,
                         days: on ? preset.days.filter((x) => x !== d.value)
                                  : [...preset.days, d.value].sort((a, b) => a - b),
                       })} />
                <span className="toggle-name">{d.short}</span>
              </label>
            );
          })}
        </div>
      )}

      <CronPreviewLine cron={value} zone={zone} preview={preview} />
    </div>
  );
}

function CronPreviewLine({ cron, zone, preview }: {
  cron: string; zone: string; preview: ReturnType<typeof useCronPreview>;
}) {
  // The controls show a shape, but an untouched row holds no schedule — say so,
  // or "Daily at 09:00" above an empty value reads as a schedule that is set.
  if (!cron.trim()) {
    return <p className="muted check-note">No schedule yet — adjust a control to set one.</p>;
  }
  if (preview?.error) return <p className="error">{preview.error}</p>;
  if (!preview?.english) return <p className="muted check-note"><code>{cron}</code></p>;
  return (
    <p className="muted check-note" title={cron}>
      → {preview.english} ({zone})
      {preview.next.length > 0 && <> · next {preview.next.map(localFireTime).join(" · ")}</>}
    </p>
  );
}
