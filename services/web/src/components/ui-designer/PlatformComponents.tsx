import { useId, useRef, useState } from "react";
import { Button } from "@ap/ui/button";
import { CodeEditor, Input, Select, Textarea } from "@ap/ui/field";
import { nodeLabel, type RNode, type RPage, type UiEditOp, type UiInspection } from "../../lib/uiDesigner";
import { type PlatformComponent, type PlatformLibrary, type PlatformVariant } from "../../lib/uiPlatformComponents";
import "./PlatformComponents.css";

type TextProperties = Record<string, string>;

function propertyLabel(name: string) {
  if (name === "children") return "Text";
  if (name === "md") return "Medium";
  if (name === "sm") return "Small";
  const spaced = name.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[-_]/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

function ComponentFields({ component, variant, onVariant, props, onProps, disabled }: {
  component: PlatformComponent;
  variant: PlatformVariant;
  props: TextProperties;
  disabled?: boolean;
  onVariant: (variant: PlatformVariant) => void;
  onProps: (props: TextProperties) => void;
}) {
  const fieldId = useId();
  const axes = Object.keys(variant.selection);
  return <div className="ud-platform-fields">
    {axes.length > 1 ? axes.map((axis) => {
      const options = component.variants.filter((item) =>
        axes.every((key) => key === axis || item.selection[key] === variant.selection[key])
      );
      return <label key={axis} className="ud-platform-field">
        {propertyLabel(axis)}
        <Select disabled={disabled} value={variant.id} onChange={(e) => {
          const next = options.find((item) => item.id === e.target.value);
          if (next) onVariant(next);
        }}>
          {options.map((item) => <option key={item.id} value={item.id}>{propertyLabel(item.selection[axis])}</option>)}
        </Select>
      </label>;
    }) : component.variants.length > 1 && <>
      <label htmlFor={`${fieldId}-variant`}>Variant</label>
      <Select id={`${fieldId}-variant`} disabled={disabled} value={variant.id} onChange={(e) => {
        const next = component.variants.find((item) => item.id === e.target.value);
        if (next) onVariant(next);
      }}>
        {component.variants.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
      </Select>
    </>}
    {component.properties.map((property) => {
      const value = props[property.name] ?? property.default;
      const multiline = ["Markdown", "CodeEditor", "Textarea"].includes(component.name) ||
        ["body", "description", "message"].includes(property.name);
      const Field = multiline ? Textarea : Input;
      return <label key={property.name} className="ud-platform-field">
        {propertyLabel(property.name)}
        <Field value={value} disabled={disabled} maxLength={4096}
          onChange={(e) => onProps({ ...props, [property.name]: e.target.value })} />
      </label>;
    })}
  </div>;
}

function StorybookLink({ storyId, name }: { storyId?: string | null; name: string }) {
  if (!storyId) return null;
  return <a href={`/storybook/?${new URLSearchParams({ path: `/story/${storyId}` })}`} target="_blank" rel="noreferrer">
    {name} in Storybook
  </a>;
}

function InsertComponent({ component, revision, page, selected, disabled, onInsert }: {
  component: PlatformComponent;
  revision: number;
  page: RPage;
  selected: RNode | null;
  disabled: boolean;
  onInsert: (op: Extract<UiEditOp, { op: "platform_component" }>) => void;
}) {
  const [choice, setChoice] = useState("");
  const [props, setProps] = useState<TextProperties>({});
  const variant = component.variants.find((item) => item.id === choice) ?? component.variants[0];
  const destination = selected?.type === "frame" && !selected.of && !selected.id.includes("/") ? selected : page;

  function insert() {
    if (disabled) return;
    const id = `component-${Array.from(crypto.getRandomValues(new Uint8Array(12)), (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
    onInsert({ op: "platform_component", component: component.name, variant: variant.id, props, parent: destination.id, id, revision });
  }

  return <section aria-label={`Configure ${component.name}`} className="ud-platform-config">
    <h4>{component.name}</h4>
    <p>{component.description}</p>
    <ComponentFields component={component} variant={variant} props={props} onVariant={(next) => setChoice(next.id)} onProps={setProps} />
    {component.limitations.map((limitation) => <p key={limitation} className="muted">{limitation}</p>)}
    <StorybookLink storyId={component.story_id} name={component.name} />
    <p className="muted">Insert into {nodeLabel(destination)}</p>
    <Button disabled={disabled} onClick={insert}>Insert component</Button>
  </section>;
}

export default function PlatformComponents({ library, error, reload, page, selected, disabled, onInsert }: {
  library: PlatformLibrary | null;
  error: string;
  reload: () => void;
  page: RPage;
  selected: RNode | null;
  disabled: boolean;
  onInsert: (op: Extract<UiEditOp, { op: "platform_component" }>) => void;
}) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [chosen, setChosen] = useState("");
  const components = library?.components ?? [];
  const categories = [...new Set(components.flatMap((component) => component.category ? [component.category] : []))].sort();
  const results = components.filter((component) =>
    (!category || category === component.category) &&
    `${component.name} ${component.description}`.toLowerCase().includes(query.trim().toLowerCase())
  );
  const component = results.find((item) => item.name === chosen);
  const heading = useId();

  return <section className="ud-platform-library" aria-labelledby={heading}>
    <h2 id={heading}>Components</h2>
    <h3>Agent Platform</h3>
    {error && <><p role="alert">{error}</p><Button onClick={reload}>Reload components</Button></>}
    {!library && !error && <p role="status">Loading components…</p>}
    {library && <>
      <div className="ud-platform-fields">
        <label className="ud-platform-field">Search components
          <Input type="search" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        {categories.length > 0 && <label className="ud-platform-field">Category
          <Select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {categories.map((name) => <option key={name} value={name}>{name}</option>)}
          </Select>
        </label>}
      </div>
      {results.length ? <ul className="ud-platform-results" aria-label="Component results">
        {results.map((item) => <li key={item.name}>
          <Button variant={chosen === item.name ? "primary" : "secondary"} aria-pressed={chosen === item.name}
            onClick={() => setChosen(item.name)}>{item.name}</Button>
        </li>)}
      </ul> : <p role="status">No components match these filters.</p>}
      {component && <InsertComponent key={component.name} component={component} revision={library.revision}
        page={page} selected={selected} disabled={disabled} onInsert={onInsert} />}
    </>}
  </section>;
}

export function PlatformComponentInspector({ inspection, component, disabled, onSave }: {
  inspection: UiInspection;
  component?: PlatformComponent;
  disabled: boolean;
  onSave: (changes: Record<string, unknown>) => void;
}) {
  const implementation = inspection.implementation!;
  const [draftVariant, setDraftVariant] = useState<PlatformVariant | null>(null);
  const [draftProps, setDraftProps] = useState<TextProperties>({});
  const [copyStatus, setCopyStatus] = useState("");
  const code = useRef<HTMLTextAreaElement>(null);
  const variant = draftVariant ?? component?.variants.find((item) => item.id === implementation.variant);
  const authoredProps = inspection.authored?.props as TextProperties | undefined;

  async function copy() {
    if (!implementation.snippet) return;
    try {
      await navigator.clipboard.writeText(implementation.snippet);
      setCopyStatus("Copied JSX.");
    } catch {
      code.current?.focus();
      code.current?.select();
      setCopyStatus("Use your copy shortcut to copy the selected JSX.");
    }
  }

  return <section aria-label="Platform implementation" className="ud-platform-implementation">
    <h3>Agent Platform implementation</h3>
    <p><code>{implementation.component}</code> from <code>{implementation.import_path}</code></p>
    <StorybookLink storyId={implementation.story_id ?? component?.story_id} name={implementation.component} />
    {implementation.status === "exact" ? <>
      {implementation.snippet ? <>
        <CodeEditor ref={code} aria-label="Implementation JSX" readOnly rows={5} value={implementation.snippet} />
        <Button onClick={() => void copy()}>Copy JSX</Button>
        {copyStatus && <p role="status">{copyStatus}</p>}
      </> : <p className="muted">JSX is unavailable for this inspection.</p>}
      {component && variant && <form className="ud-platform-config" onSubmit={(e) => {
        e.preventDefault();
        if (!disabled) onSave({ variant: variant.selection, props: { ...authoredProps, ...draftProps } });
      }}>
        <ComponentFields component={component} variant={variant} props={{ ...authoredProps, ...draftProps }}
          disabled={disabled} onVariant={setDraftVariant} onProps={(props) => setDraftProps(props)} />
        <Button type="submit" disabled={disabled || (!draftVariant && !Object.keys(draftProps).length)}>Save component properties</Button>
      </form>}
    </> : <p>Local changes have detached this instance from its Agent Platform implementation.</p>}
    {implementation.limitations?.map((limitation) => <p key={limitation} className="muted">{limitation}</p>)}
  </section>;
}
