import type { Artifact, GenerateIn, ImageModel } from "../../api";

// What every form that asks for a picture shares (docs/design/23): the
// Studio's compose panel and an agent's Generate dialog start on the same
// model, ask each model for a square in its own words, and read the API's
// refusals the same way — so a portrait made on an agent's page and a picture
// made in the Studio are the same request, differently dressed. One source
// of truth: nothing here is defined a second time anywhere else.

/** The form as typed: strings throughout, parsed once at generate. */
export type Draft = {
  model: string; prompt: string; size: string; aspect: string; quality: string; seed: string;
};

export const EMPTY_DRAFT: Draft = { model: "", prompt: "", size: "", aspect: "", quality: "", seed: "" };

/** The model a fresh form starts on: the registry's default when its key is
 * set, else the first that is; null when nothing is configured. */
export function defaultModel(models: ImageModel[]): ImageModel | null {
  return models.find((m) => m.configured && m.default) ?? models.find((m) => m.configured) ?? null;
}

/** A square, in the model's own geometry: sizes for the models that take
 * pixels, an aspect for the ones that take a ratio. */
export function geometryFor(m: ImageModel): { size?: string; aspect?: string } {
  if (m.sizes?.length) return { size: m.sizes.find((s) => s.includes("1024x1024")) ?? m.sizes[0] };
  if (m.aspects?.length) return { aspect: m.aspects.includes("1:1") ? "1:1" : m.aspects[0] };
  return {};
}

/** The body the generate route takes, read off the form: every knob the
 * chosen model has, nothing it does not, and references only where the
 * model would use them. */
export function requestFor(chosen: ImageModel, draft: Draft, references: Artifact[]): GenerateIn {
  const body: GenerateIn = { model: chosen.id, prompt: draft.prompt.trim() };
  if (chosen.sizes?.length && draft.size) body.size = draft.size;
  if (chosen.aspects?.length && draft.aspect) body.aspect = draft.aspect;
  if (chosen.qualities?.length && draft.quality) body.quality = draft.quality;
  const seed = draft.seed.trim();
  if (/^\d+$/.test(seed)) body.seed = Number(seed);
  if (chosen.edits && references.length) body.reference_ids = references.map((r) => r.id);
  return body;
}

// The API's refusals (402 over budget, 422, 429, 502 from the provider) put
// the reason in `detail`; the wrapper's message is `<status>: <body>`, so the
// body is read back out and shown as the API wrote it. A validation error's
// detail is a list of `{loc, msg}`, folded into one line.
export function errorDetail(err: unknown, fallback: string): string {
  if (!(err instanceof Error)) return fallback;
  const m = /^\d{3}: ([\s\S]*)$/.exec(err.message);
  if (m) {
    try {
      const detail = (JSON.parse(m[1]) as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail) return detail;
      if (Array.isArray(detail) && detail.length) {
        return detail.map((d: { loc?: unknown; msg?: unknown }) => {
          const loc = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body").join(".") : "";
          return loc ? `${loc}: ${String(d.msg ?? "")}` : String(d.msg ?? "");
        }).join("; ");
      }
    } catch {
      // Not JSON: the message as a whole is the best there is.
    }
  }
  return err.message || fallback;
}
