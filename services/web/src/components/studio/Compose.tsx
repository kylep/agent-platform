import type { KeyboardEvent, RefObject } from "react";
import { Link } from "react-router-dom";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { Input, Select, Textarea } from "@ap/ui/field";
import { Stat, StatRow } from "@ap/ui/stat";
import type { Artifact, ArtifactStats, ImageModel } from "../../api";
import type { Draft } from "./generate";
import { ModelSelect } from "./ModelSelect";
import { ReferenceStrip } from "./ReferenceStrip";

// The left column (docs/design/23): what is asked for. Every control is
// driven by the chosen model's registry entry — a model that speaks sizes
// gets a size select, one that speaks aspects an aspect select, quality only
// where the model has one — so the form never offers a knob the provider
// would reject. The price sits on the button because the button is the
// moment it is spent.

export function Compose({
  models, chosen, draft, onDraft, references, onAddReference, onRemoveReference,
  result, pending, elapsed, stats, error, onError, onGenerate, promptRef,
}: {
  models: ImageModel[] | null;
  chosen: ImageModel | null;
  draft: Draft;
  onDraft: (patch: Partial<Draft>) => void;
  references: Artifact[];
  onAddReference: (a: Artifact) => void;
  onRemoveReference: (id: string) => void;
  result: Artifact | null;
  pending: boolean;
  elapsed: number;
  stats: ArtifactStats | null;
  error: string | null;
  onError: (message: string | null) => void;
  onGenerate: () => void;
  promptRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const none = models !== null && !models.some((m) => m.configured);
  const ready = !!chosen && draft.prompt.trim().length > 0 && !pending;

  function keydown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      if (ready) onGenerate();
    }
  }

  return (
    <section className="studio-compose" aria-label="Compose">
      {none && (
        <Banner variant="info">
          No image provider is configured — add a key under{" "}
          <Link to="/secrets">Settings → Secrets</Link> and the models appear here.
        </Banner>
      )}

      <label className="field-label" htmlFor="studio-model">Model</label>
      <ModelSelect id="studio-model" models={models} value={draft.model} disabled={pending}
                   onChange={(id) => onDraft({ model: id })} />

      <label className="field-label" htmlFor="studio-prompt">Prompt</label>
      <Textarea id="studio-prompt" ref={promptRef} rows={5} value={draft.prompt} disabled={pending}
                placeholder="What should the picture show? ⌘⏎ generates."
                onChange={(e) => onDraft({ prompt: e.target.value })} onKeyDown={keydown} />

      <div className="studio-knobs">
        {chosen?.sizes?.length ? (
          <div className="studio-knob">
            <label className="field-label" htmlFor="studio-size">Size</label>
            <Select id="studio-size" value={draft.size} disabled={pending}
                    onChange={(e) => onDraft({ size: e.target.value })}>
              {chosen.sizes.map((s) => <option key={s} value={s}>{s}</option>)}
            </Select>
          </div>
        ) : null}
        {chosen?.aspects?.length ? (
          <div className="studio-knob">
            <label className="field-label" htmlFor="studio-aspect">Aspect</label>
            <Select id="studio-aspect" value={draft.aspect} disabled={pending}
                    onChange={(e) => onDraft({ aspect: e.target.value })}>
              {chosen.aspects.map((a) => <option key={a} value={a}>{a}</option>)}
            </Select>
          </div>
        ) : null}
        {chosen?.qualities?.length ? (
          <div className="studio-knob">
            <label className="field-label" htmlFor="studio-quality">Quality</label>
            <Select id="studio-quality" value={draft.quality} disabled={pending}
                    onChange={(e) => onDraft({ quality: e.target.value })}>
              {/* The provider's own default, rather than a guess at which of
                  its words means "normal". */}
              <option value="">default</option>
              {chosen.qualities.map((q) => <option key={q} value={q}>{q}</option>)}
            </Select>
          </div>
        ) : null}
        <div className="studio-knob">
          <label className="field-label" htmlFor="studio-seed">Seed</label>
          <Input id="studio-seed" type="number" inputMode="numeric" min={0} step={1}
                 value={draft.seed} disabled={pending} placeholder="random"
                 onChange={(e) => onDraft({ seed: e.target.value })} />
        </div>
      </div>

      <ReferenceStrip references={references} enabled={!!chosen?.edits}
                      modelLabel={chosen?.label ?? null} result={result} busy={pending}
                      onAdd={onAddReference} onRemove={onRemoveReference} onError={onError} />

      {error && <Banner variant="danger" role="alert">{error}</Banner>}

      <div className="studio-generate">
        <Button disabled={!ready} onClick={onGenerate}>
          {pending ? `Generating… ${elapsed} s` : "Generate"}
          {!pending && chosen && <span className="studio-price">· ${chosen.price_usd.toFixed(2)}</span>}
        </Button>
        <span className="muted studio-hint">⌘⏎ / Ctrl⏎</span>
      </div>

      {stats && (
        <StatRow>
          <Stat label="images this month" value={stats.generated_this_month} />
          <Stat label="spend this month" value={`$${stats.spend_this_month_usd.toFixed(2)}`} />
          <Stat label="spend today / cap"
                value={`$${stats.spend_today_usd.toFixed(2)} / $${stats.daily_cap_usd.toFixed(2)}`}
                warn={stats.daily_cap_usd > 0 && stats.spend_today_usd >= stats.daily_cap_usd * 0.9} />
        </StatRow>
      )}
    </section>
  );
}
