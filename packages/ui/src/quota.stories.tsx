import type { ReactNode } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { QuotaBars, type QuotaSnapshot } from "./quota";

const meta: Meta = { title: "UI/QuotaBars" };
export default meta;

// The two usage bars that sit under the sidebar brand. They are drawn at the
// sidebar's inner width, so every story frames them that way.

const now = Date.now();
const iso = (msFromNow: number) => new Date(now + msFromNow).toISOString();

function snapshot(five: number | null, seven: number | null,
                  over: Partial<QuotaSnapshot> = {}): QuotaSnapshot {
  return {
    five_hour: { utilization: five, resets_at: iso(3.9 * 3600e3) },
    seven_day: { utilization: seven, resets_at: iso(4.2 * 86400e3) },
    status: "allowed", observed_at: iso(-120e3), source: "proxy",
    stale: false, age_seconds: 120, ...over,
  };
}

function Frame({ theme, children }: { theme?: "light"; children: ReactNode }) {
  return (
    <div data-theme={theme} style={{ background: "var(--ds-surface)",
                                     padding: 16, width: 200 }}>
      <div style={{ fontWeight: 600, paddingBottom: 8 }}>Agent Platform</div>
      {children}
    </div>
  );
}

// 0 and 100 are the ends the two-layer label has to survive: at neither one
// does any glyph straddle the fill edge, and both must still be legible.
export const Range: StoryObj = {
  render: () => (
    <Frame>
      {[[0, 0], [0.22, 0.81], [0.81, 1]].map(([a, b]) => (
        <div key={`${a}`} style={{ paddingBottom: 12 }}>
          <QuotaBars {...snapshot(a, b)} />
        </div>
      ))}
    </Frame>
  ),
};

export const Stale: StoryObj = {
  render: () => <Frame><QuotaBars {...snapshot(0.22, 0.81, {
    stale: true, observed_at: iso(-3 * 3600e3), age_seconds: 10800 })} /></Frame>,
};

export const Unknown: StoryObj = {
  render: () => <Frame><QuotaBars {...snapshot(null, null, { stale: true })} /></Frame>,
};

export const Light: StoryObj = {
  render: () => <Frame theme="light"><QuotaBars {...snapshot(0.22, 0.81)} /></Frame>,
};
