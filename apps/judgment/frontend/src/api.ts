// Typed client for the judgment app's API (apps/judgment/backend/judgmentapp/api.py,
// contract in docs/superpowers/plans/2026-10-01-judgment-app.md). Rows are the
// `schema.COLUMNS` rows as JSON; the vocabularies below mirror schema.py.

export const BELIEF_STATUSES = ["active", "superseded", "rejected"] as const;
export type BeliefStatus = (typeof BELIEF_STATUSES)[number];
export type Provenance = "kyle_confirmed" | "kyle_relayed" | "observed" | "inference" | "imported";
export type Confidence = "low" | "medium" | "high";
export type Timing = "prospective" | "retrospective";
export const OUTCOMES = ["supported", "contradicted", "mixed", "context_changed", "unresolved"] as const;
export type Outcome = (typeof OUTCOMES)[number];
export const RESOLVING_OUTCOMES = ["supported", "contradicted", "mixed", "context_changed"] as const;
export type ResolvingOutcome = (typeof RESOLVING_OUTCOMES)[number];

export const LIMITS = { claim: 1000, scope: 4000, reason: 4000, kyle_words: 4000 } as const;

export type Version = {
  id: string;
  belief_id: string;
  version: number;
  claim: string;
  scope: string | null;
  evidence: string | null;
  provenance: Provenance;
  source_ref: string | null;
  confidence: Confidence | null;
  reason: string | null;
  feedback_id: string | null;
  author: string;
  created_at: string;
};

export type Belief = { id: string; created_at: string; status: BeliefStatus; current_version: number };

export type BeliefRow = Belief & { current: Version; confirmed: Version | null };

export type BeliefPrediction = {
  id: string; scenario: string; predicted_choice: string; timing: Timing; created_at: string;
};

export type Feedback = {
  id: string;
  created_at: string;
  prediction_id: string | null;
  belief_id: string | null;
  belief_version: number | null;
  kyle_words: string;
  source_ref: string | null;
  source_at: string | null;
  outcome: Outcome;
  interpretation: string | null;
  author: string;
  confirmed_at: string | null;
};

export type BeliefDetail = {
  belief: Belief;
  versions: Version[];
  predictions: BeliefPrediction[];
  feedback: Feedback[];
};

export type Prediction = {
  id: string;
  created_at: string;
  scenario: string;
  alternatives: string[];
  predicted_choice: string;
  rationale: string | null;
  confidence: Confidence | null;
  timing: Timing;
  question_ref: string | null;
  author: string;
};

export type PredictionRow = Prediction & {
  resolution: ResolvingOutcome | null;
  flags: string[];
  feedback_count: number;
};

export type PredictionLink = { belief_id: string; belief_version: number; claim: string | null };

export type PredictionDetail = {
  prediction: Prediction;
  links: PredictionLink[];
  feedback: Feedback[];
  resolution: ResolvingOutcome | null;
  flags: string[];
};

export type DeletePreview = {
  versions: { belief_id: string; version: number; claim: string }[];
  beliefs_emptied: string[];
};

export type Deleted = { deleted: Record<string, number> };

export type ReviewCounts = {
  prospective_resolved: Record<ResolvingOutcome, number>;
  prospective_resolved_confirmed: number;
  prospective_resolved_relayed_only: number;
  prospective_pending: number;
  retrospective: number;
  flagged: number;
  feedback_unconfirmed: number;
};

export type ReviewItem = {
  prediction: Prediction;
  feedback: Feedback[];
  resolution: ResolvingOutcome | null;
  flags: string[];
};

export type Review = { counts: ReviewCounts; items: ReviewItem[] };

/** A failed call, with the status the page branches on: 403 is "not Kyle",
 * 409 is "the belief moved since you loaded it". */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const BASE = "/apps/judgment/api";

function detailText(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  // FastAPI's 422 is a list of {loc, msg}.
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d))).join("; ");
  }
  return fallback;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method,
    credentials: "include",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let detail: unknown = null;
    try { detail = ((await r.json()) as { detail?: unknown }).detail; } catch { /* not JSON */ }
    throw new ApiError(r.status, detailText(detail, `${r.status} ${r.statusText}`));
  }
  return r.json() as Promise<T>;
}

export const get = <T>(path: string) => request<T>("GET", path);
export const post = <T>(path: string, body: unknown) => request<T>("POST", path, body);
export const del = <T>(path: string) => request<T>("DELETE", path);
