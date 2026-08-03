import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type PullRequest, type SyncStatus } from "../api";
import { Banner } from "@ap/ui/banner";
import { Chip } from "@ap/ui/chip";

// The standardized change loop (docs/building-blocks/changes.md):
//   Propose → Review → Accept → Deploying → Live   (or Discard)
// Every git-declared building block (agent, skill, secret) rides the same
// rails: a deterministic coder/<kind>-<name> branch, one pending change at a
// time, and deploy tracking against /api/sync-status (the cluster's checkout).

export type BlockRef = { kind: "agent" | "skill" | "secret"; name: string };

export function parseBranch(branch: string): BlockRef | null {
  const m = branch.match(/^coder\/(agent|skill|secret)-(.+)$/);
  return m ? { kind: m[1] as BlockRef["kind"], name: m[2] } : null;
}

export function blockPath(ref: BlockRef): string {
  if (ref.kind === "agent") return `/agents/${encodeURIComponent(ref.name)}`;
  if (ref.kind === "skill") return "/skills";
  return "/secrets";
}

export type ChangePhase = "idle" | "pending" | "deploying" | "live";

// Watch one building block's branch through the loop. While a PR is open on
// it, `pr` is set (callers lock their editors). When the PR resolves (accepted
// or discarded), we wait for the synced checkout's sha to move, then fire
// onLive (callers refetch content) and flash `phase: "live"` briefly.
// `adopt` registers a just-created PR immediately so the lock is instant.
export function useChangeLoop(branch: string, onLive?: () => void) {
  const [pr, setPr] = useState<PullRequest | null>(null);
  const [phase, setPhase] = useState<ChangePhase>("idle");
  const prRef = useRef<PullRequest | null>(null);
  const baseShaRef = useRef<string | null>(null);
  const onLiveRef = useRef(onLive);
  onLiveRef.current = onLive;

  function adopt(p: PullRequest) {
    prRef.current = p;
    setPr(p);
    setPhase("pending");
  }

  useEffect(() => {
    let stop = false;
    api<SyncStatus>("/api/sync-status")
      .then((s) => { baseShaRef.current = s.sha; })
      .catch(() => {});

    async function watchDeploy() {
      // Judgment call: "live" = the checkout's sha moved off its pre-resolve
      // value (or 90s passed — sync interval is 60s). We can't cheaply prove
      // ancestry client-side; a moved sha after an accept means our commit is
      // in history in all but pathological cases.
      const started = Date.now();
      while (!stop && Date.now() - started < 90_000) {
        try {
          const s = await api<SyncStatus>("/api/sync-status");
          if (s.sha && s.sha !== baseShaRef.current) { baseShaRef.current = s.sha; break; }
        } catch { /* transient; keep waiting */ }
        await new Promise((r) => setTimeout(r, 4000));
      }
      if (stop) return;
      setPhase("live");
      onLiveRef.current?.();
      setTimeout(() => { if (!stop) setPhase("idle"); }, 6000);
    }

    async function tick() {
      try {
        const prs = await api<PullRequest[]>("/api/pull-requests");
        if (stop) return;
        const mine = prs.find((p) => p.branch === branch) ?? null;
        if (mine) {
          prRef.current = mine;
          setPr(mine);
          setPhase("pending");
        } else if (prRef.current) {
          prRef.current = null;
          setPr(null);
          setPhase("deploying");
          watchDeploy();
        }
      } catch { /* PR listing unavailable → don't lock */ }
    }
    tick();
    const id = setInterval(tick, 10_000);
    return () => { stop = true; clearInterval(id); };
  }, [branch]);

  return { pr, phase, adopt };
}

// "This <what> has a pending change — review it under Changes."
export function PendingChangeBanner({ pr, what }: { pr: PullRequest; what: string }) {
  return (
    <Banner>
      This {what} has a pending change
      {pr.number ? <> (<a href={pr.url} target="_blank" rel="noreferrer">PR #{pr.number}</a>)</> : null} —{" "}
      <Link to={`/changes?open=${pr.number}`}>review &amp; accept it under Changes</Link>.
      Editing is locked until it's accepted or discarded.
    </Banner>
  );
}

// Post-resolve feedback shared by every editor page.
export function ChangePhaseBanner({ phase, what }: { phase: ChangePhase; what: string }) {
  if (phase === "deploying") {
    return <Banner>Change resolved — syncing to the cluster…</Banner>;
  }
  if (phase === "live") {
    return <Banner variant="ok">✓ Live — showing the current {what}.</Banner>;
  }
  return null;
}

// Tracks one accepted change's merge sha until the cluster runs it.
// Used by the Changes page, where the exact sha is known.
export function DeployTracker({ sha, onLive }: { sha: string | null; onLive?: () => void }) {
  const [state, setState] = useState<"deploying" | "live" | "unconfirmed">("deploying");
  const onLiveRef = useRef(onLive);
  onLiveRef.current = onLive;

  useEffect(() => {
    let stop = false;
    let moved = false;
    const started = Date.now();
    async function poll() {
      while (!stop && Date.now() - started < 150_000) {
        try {
          const s = await api<SyncStatus>("/api/sync-status");
          if (sha && s.sha === sha) { if (!stop) { setState("live"); onLiveRef.current?.(); } return; }
          if (s.sha && s.sha !== sha) moved = true;
        } catch { /* transient */ }
        await new Promise((r) => setTimeout(r, 4000));
      }
      // Judgment call: head moved past our sha (another commit landed after) or
      // sync is slow — report live-but-unconfirmed instead of spinning forever.
      void moved;
      if (!stop) { setState("unconfirmed"); onLiveRef.current?.(); }
    }
    poll();
    return () => { stop = true; };
  }, [sha]);

  if (state === "live") return <Chip variant="ok">live ✓</Chip>;
  if (state === "unconfirmed") return <Chip variant="warn" title="The checkout moved but its head isn't this exact sha (another commit may have landed after). Almost certainly live.">live (unconfirmed)</Chip>;
  return <Chip variant="warn">deploying…</Chip>;
}
