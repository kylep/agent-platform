import { useState } from "react";
import { api, type AgentDef, type WebhookEntry } from "../api";

// The client side of a webhook's shared secret (docs/design/16). It lives here
// rather than in the form because the whole point is that the value is NOT
// part of the definition: the draft is snapshotted into the change log on
// every write, so the secret is held in component state and leaves only
// through its own write-only endpoint, after the def save that declares the
// path.

// The literal line an external caller has to send. Printed verbatim as the
// row's tooltip so there is nothing to guess or mistype.
export const WEBHOOK_SECRET_HEADER = "X-AP-Webhook-Secret: <your secret>";

// Mirrors webhooksecrets.MIN_SECRET_LENGTH. Client-side so the field says so
// before the round trip; the server rejects a short one either way.
export const WEBHOOK_SECRET_MIN = 16;

// 192 bits of CSPRNG, base64url — 32 characters, no padding. Offered because a
// typed secret is the weak case: the stored digest is a salted single-round
// SHA-256, which is fine against a high-entropy value and not against
// "password12345678". The value is shown (via the eye) exactly once, here.
export function generateWebhookSecret(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Secrets the operator just typed, keyed by the path they belong to — the key
// the endpoint takes. `rotating` is which already-set rows asked for the field
// back; a secret nobody can read has no other reason to show one.
export function useWebhookSecrets() {
  const [values, setValues] = useState<Record<string, string>>({});
  const [rotating, setRotating] = useState<Record<string, boolean>>({});

  function move<T>(m: Record<string, T>, from: string, to: string): Record<string, T> {
    if (!(from in m)) return m;
    const next = { ...m };
    delete next[from];
    next[to] = m[from];
    return next;
  }
  function drop<T>(m: Record<string, T>, path: string): Record<string, T> {
    if (!(path in m)) return m;
    const next = { ...m };
    delete next[path];
    return next;
  }

  return {
    values,
    rotating,
    set: (path: string, value: string) => setValues((v) => ({ ...v, [path]: value })),
    // A renamed row carries its half-typed secret with it, instead of
    // stranding it under a path that no longer exists.
    rename: (from: string, to: string) => {
      setValues((v) => move(v, from, to));
      setRotating((r) => move(r, from, to));
    },
    forget: (path: string) => {
      setValues((v) => drop(v, path));
      setRotating((r) => drop(r, path));
    },
    rotate: (path: string) => setRotating((r) => ({ ...r, [path]: true })),
    reset: () => { setValues({}); setRotating({}); },
  };
}

export type WebhookSecrets = ReturnType<typeof useWebhookSecrets>;

// The secrets that are ready to write: a `secret`-mode row on a real path with
// a long-enough value typed into it. A row whose secret is already set and was
// not rotated has nothing pending — the value it holds is unreadable by design.
export function pendingSecretWrites(webhooks: WebhookEntry[], values: Record<string, string>) {
  return webhooks
    .filter((w) => w.path && w.auth === "secret" && (values[w.path] ?? "").length >= WEBHOOK_SECRET_MIN)
    .map((w) => ({ path: w.path, secret: values[w.path] }));
}

// Rows the operator started typing a secret into but hasn't finished. Saving
// is blocked while any exists, so a too-short value can't be silently dropped.
export function shortSecretPaths(webhooks: WebhookEntry[], values: Record<string, string>): string[] {
  return webhooks
    .filter((w) => w.auth === "secret")
    .map((w) => w.path)
    .filter((p) => {
      const v = values[p] ?? "";
      return v !== "" && v.length < WEBHOOK_SECRET_MIN;
    });
}

// Written AFTER the definition, one call per path: the endpoint 404s until the
// path is declared, and the value must never travel with the def.
export async function writeWebhookSecrets(
  agent: string, writes: Array<{ path: string; secret: string }>,
): Promise<void> {
  for (const w of writes) {
    await api(
      `/api/agents/${encodeURIComponent(agent)}/webhooks/${encodeURIComponent(w.path)}/secret`,
      { method: "PUT", body: JSON.stringify({ secret: w.secret }) },
    );
  }
}

// Marks the paths whose secret was just written as set, without re-reading the
// definition — the API would report the state from before the secret call.
export function markSecretsSet(def: AgentDef, paths: string[]): AgentDef {
  if (paths.length === 0) return def;
  const written = new Set(paths);
  return {
    ...def,
    entrypoints: {
      ...def.entrypoints,
      webhooks: def.entrypoints.webhooks.map(
        (w) => (written.has(w.path) ? { ...w, secret_set: true } : w)),
    },
  };
}
