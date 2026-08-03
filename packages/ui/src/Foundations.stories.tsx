import type { Meta, StoryObj } from "@storybook/react-vite";

// The token catalogue: palette, type, status colors — rendered from the live
// CSS variables, so this page IS the design system, not a picture of it.
const meta: Meta = { title: "Design System/Foundations" };
export default meta;

const semantic = [
  "canvas", "surface", "raised", "border", "text", "text-muted", "text-subtle",
  "accent", "accent-hover", "on-accent", "warning", "danger", "success", "link",
];

function Swatch({ name }: { name: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <div style={{
        width: 44, height: 28, borderRadius: "var(--radius-md)",
        background: `var(--ds-${name})`, border: "1px solid var(--ds-border)",
      }} />
      <code style={{ fontSize: 13 }}>--ds-{name}</code>
    </div>
  );
}

export const Palette: StoryObj = {
  render: () => (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(200px, 1fr))", gap: 10, maxWidth: 560 }}>
      {semantic.map((n) => <Swatch key={n} name={n} />)}
      {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => <Swatch key={i} name={`chart-${i}`} />)}
    </div>
  ),
};

export const TypeScale: StoryObj = {
  render: () => (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 640 }}>
      <h1>Page title — 22px semibold</h1>
      <h2>Section heading — 16px semibold</h2>
      <p>Body — 14px sans. The platform console is read at arm's length on a desk; density beats drama.</p>
      <p className="muted">Muted — secondary copy, timestamps, hints.</p>
      <p style={{ color: "var(--ds-text-subtle)" }}>Subtle — table headers, stat labels (AA on both themes).</p>
      <code style={{ fontFamily: "var(--font-mono)", fontSize: 13 }}>
        mono — identifiers, cron exprs, file paths, diffs
      </code>
    </div>
  ),
};
