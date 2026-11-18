import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";
import library from "../designer-library.json";
import {
  Banner, Button, Chip, ChipButton, CodeEditor, ConfirmDialog, FormDialog,
  InfoDialog, Input, Markdown, QuotaBars, Select, SideNav, Stat, StatRow,
  StatusChip, Table, TD, Textarea, ThemeToggle, TH,
  type NavEntry, type QuotaSnapshot,
} from "./index";

type Args = { component: string; variant: string };
type Sample = (typeof library.components)[number]["variants"][number];

function SampleTheme({ labelMode, canvasMode, nav, props }: {
  labelMode: string; canvasMode: string; nav: boolean; props: Record<string, unknown>;
}) {
  // The app normally changes the entire page theme. The reference keeps the
  // chosen label state independently testable on either Storybook background.
  localStorage.setItem("theme", labelMode);
  useEffect(() => {
    document.documentElement.dataset.theme = canvasMode;
  }, [canvasMode]);
  return nav
    ? <SideNav entries={props.entries as NavEntry[]} activePath={String(props.activePath)} />
    : <ThemeToggle />;
}

function SampleReact({ component, sample, theme }: { component: string; sample: Sample; theme: string }) {
  const props = sample.props as Record<string, unknown>;
  const fixture = sample.fixture as Record<string, unknown>;
  const width = sample.width;
  const label = String(props.children ?? "");
  const field = { defaultValue: String(props.defaultValue), disabled: Boolean(props.disabled),
    readOnly: Boolean(props.readOnly), "aria-invalid": props["aria-invalid"] === true || undefined,
    "aria-label": component, style: { width } };
  const modal = { open: true, title: String(props.title), children: label };
  const noop = () => {};

  switch (component) {
    case "Banner": return <Banner {...props} style={{ width }}>{label}</Banner>;
    case "Button": return <Button {...props}>{label}</Button>;
    case "Chip": return <Chip {...props}>{label}</Chip>;
    case "ChipButton": return <ChipButton {...props}>{label}</ChipButton>;
    case "StatusChip": return <StatusChip status={String(props.status)} />;
    case "CodeEditor": return <CodeEditor {...field} />;
    case "Input": return <Input {...field} />;
    case "Select": return <Select {...field}>
      {(fixture.options as string[]).map((option) => <option key={option}>{option}</option>)}
    </Select>;
    case "Textarea": return <Textarea {...field} />;
    case "ConfirmDialog":
      return <ConfirmDialog {...modal} confirmLabel={String(props.confirmLabel)} onConfirm={noop} onCancel={noop} />;
    case "FormDialog":
      return <FormDialog {...modal} submitLabel={String(props.submitLabel)} disabled={Boolean(props.disabled)}
                          onSubmit={noop} onCancel={noop} />;
    case "InfoDialog": return <InfoDialog {...modal} onClose={noop} />;
    case "Markdown": return <div style={{ width }}><Markdown text={String(props.text)} flow={Boolean(props.flow)} /></div>;
    case "QuotaBars": return <div style={{ width }}><QuotaBars {...props as QuotaSnapshot} /></div>;
    case "SideNav":
    case "ThemeToggle":
      return <SampleTheme key={String(fixture.theme)} labelMode={String(fixture.theme)} canvasMode={theme}
                          nav={component === "SideNav"} props={props} />;
    case "Stat":
      return <div style={{ width }}>
        <Stat label={String(props.label)} value={String(props.value)} warn={Boolean(props.warn)}
              to={props.to as string | undefined} sub={props.sub as string | undefined} />
      </div>;
    case "StatRow":
      return <div style={{ width }}><StatRow>
        {(fixture.cards as { label: string; value: string; warn?: boolean }[])
          .map((card) => <Stat key={card.label} {...card} />)}
      </StatRow></div>;
    case "Table": {
      const headers = fixture.headers as string[];
      const rows = fixture.rows as string[][];
      return <div style={{ width }}>
        <Table aria-label="Sample runs" style={{ width: headers.length * 128, tableLayout: "fixed" }}>
          <thead><tr>{headers.map((header) => <TH key={header} scope="col">{header}</TH>)}</tr></thead>
          <tbody>{rows.map((row) => <tr key={row[0]}>
            {row.map((cell, i) => <TD key={i} colSpan={row.length === 1 ? headers.length : undefined}>{cell}</TD>)}
          </tr>)}</tbody>
        </Table>
      </div>;
    }
    case "TH":
      return <Table aria-label="Sample header" style={{ width }}>
        <thead><tr><TH data-designer-cell {...props}>{label}</TH></tr></thead>
      </Table>;
    case "TD":
      return <Table aria-label="Sample cell" style={{ width }} className="[&_tbody_tr]:border-0">
        <tbody><tr><TD data-designer-cell {...props}>{label}</TD></tr></tbody>
      </Table>;
    default:
      throw new Error(`Unsupported Designer catalog component ${component}`);
  }
}

const meta = {
  title: "UI Designer/Agent Platform Library",
  args: { component: "Button", variant: "default" },
  argTypes: {
    component: { control: "select", options: library.components.map(({ name }) => name) },
    variant: { control: "select", options: [...new Set(library.components.flatMap(
      ({ variants }) => variants.map(({ id }) => id),
    ))] },
  },
  render: ({ component, variant }: Args, { globals }) => {
    const definition = library.components.find(({ name }) => name === component);
    if (!definition) throw new Error(`Unknown component ${component}`);
    const sample = definition.variants.find(({ id }) => id === variant) ?? definition.variants[0];
    return <div data-designer-fixture data-component={component} data-variant={sample.id}
                style={{ display: "inline-block" }}>
      <SampleReact key={`${component}/${sample.id}`} component={component} sample={sample}
                   theme={String(globals.theme ?? "dark")} />
    </div>;
  },
} satisfies Meta<Args>;

export default meta;
export const Reference: StoryObj<typeof meta> = {};
