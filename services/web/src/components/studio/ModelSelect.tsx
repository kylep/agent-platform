import { Select } from "@ap/ui/field";
import type { ImageModel } from "../../api";

// The model picker (docs/design/23). Every model the registry knows is
// offered, priced, and the ones whose key is not set are listed disabled
// with where the key goes — a missing option reads as "the platform cannot
// do that", a disabled one as "you have not set it up yet".

export function ModelSelect({ id, models, value, disabled, onChange }: {
  id: string;
  /** Null while the registry has not answered; the select waits with it. */
  models: ImageModel[] | null;
  value: string;
  disabled?: boolean;
  onChange: (id: string) => void;
}) {
  return (
    <Select id={id} value={value} disabled={disabled || !models}
            onChange={(e) => onChange(e.target.value)}>
      {(models ?? []).map((m) => (
        <option key={m.id} value={m.id} disabled={!m.configured}>
          {m.label}{m.configured ? ` · $${m.price_usd.toFixed(2)}` : " — add key in Secrets"}
        </option>
      ))}
    </Select>
  );
}
