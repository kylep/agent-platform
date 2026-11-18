export interface CaptureBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface PrimitiveBase extends CaptureBounds {
  groupId: number;
  opacity: number;
  clip: CaptureBounds;
}

export interface CaptureBorder {
  width: number;
  color: string;
  style: string;
}

export type CapturePrimitive = PrimitiveBase & (
  | { kind: "rect"; fill: string; borders: CaptureBorder[]; radii: number[] }
  | {
      kind: "text";
      text: string;
      color: string;
      fontFamily: string;
      fontSize: number;
      fontWeight: number;
      lineHeight: number;
      fontStyle: string;
      letterSpacing: number;
      textDecoration: string;
    }
  | { kind: "svg"; viewBox: string; preserveAspectRatio: string; shapes: CaptureSvgShape[]; svg: string }
  | { kind: "image"; src: string; objectFit: string; objectPosition: string; alt: string; sourceKind?: "canvas" }
);

export interface CaptureSvgShape {
  tag: string;
  attributes: Record<string, string>;
  matrix?: [number, number, number, number, number, number];
}

export interface CaptureGroup extends CaptureBounds {
  id: number;
  parentId: number | null;
  tag: string;
  role: string;
  label: string;
  href?: string;
  classes: string[];
}

export interface CaptureOptions {
  fullPage?: boolean;
  maxHeight?: number;
  maxPrimitives?: number;
}

export interface CapturedScene {
  width: number;
  height: number;
  groups: CaptureGroup[];
  primitives: CapturePrimitive[];
  unsupported: Record<string, number>;
}

/** Runs inside the browser. Keep every runtime dependency inside this function. */
export function captureScene(options: CaptureOptions = {}): CapturedScene {
  const width = window.innerWidth;
  const height = options.fullPage
    ? Math.max(window.innerHeight, Math.min(document.documentElement.scrollHeight, options.maxHeight ?? 4000))
    : window.innerHeight;
  const originY = options.fullPage ? window.scrollY : 0;
  const viewport = { x: 0, y: 0, width, height };
  const maxPrimitives = options.maxPrimitives ?? 8000;
  const implicitRoles: Record<string, string> = {
    a: "link", aside: "complementary", button: "button", dialog: "dialog", footer: "contentinfo",
    form: "form", header: "banner", h1: "heading", h2: "heading", h3: "heading", h4: "heading",
    h5: "heading", h6: "heading", img: "img", li: "listitem", main: "main", nav: "navigation",
    ol: "list", select: "combobox", table: "table", td: "cell", textarea: "textbox", th: "columnheader",
    tr: "row", ul: "list",
  };
  const groups: CaptureGroup[] = [];
  const primitives: CapturePrimitive[] = [];
  const unsupported: Record<string, number> = {};
  const context = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  const colors = new Map<string, string>();

  const report = (feature: string) => {
    unsupported[feature] = (unsupported[feature] ?? 0) + 1;
  };

  interface Offset {
    x: number;
    y: number;
  }

  const elementStyle = (element: Element, pseudo?: string) => element.ownerDocument.defaultView!.getComputedStyle(element, pseudo);

  const bounds = (rect: DOMRect, offset: Offset): CaptureBounds => ({
    x: rect.left + offset.x,
    y: rect.top + offset.y,
    width: rect.width,
    height: rect.height,
  });

  const intersect = (first: CaptureBounds, second: CaptureBounds): CaptureBounds => {
    const x = Math.max(first.x, second.x);
    const y = Math.max(first.y, second.y);
    return {
      x,
      y,
      width: Math.max(0, Math.min(first.x + first.width, second.x + second.width) - x),
      height: Math.max(0, Math.min(first.y + first.height, second.y + second.height) - y),
    };
  };

  const visible = (rect: CaptureBounds, clip: CaptureBounds) => {
    const overlap = intersect(rect, clip);
    return overlap.width > 0 && overlap.height > 0;
  };

  const number = (value: string) => Number.parseFloat(value) || 0;

  function color(value: string): string {
    const cached = colors.get(value);
    if (cached) return cached;
    if (!context || !CSS.supports("color", value)) return "#00000000";
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = value;
    context.fillRect(0, 0, 1, 1);
    const [r, g, b, alpha] = context.getImageData(0, 0, 1, 1).data;
    const hex = (channel: number) => channel.toString(16).padStart(2, "0");
    const result = `#${hex(r)}${hex(g)}${hex(b)}${alpha < 255 ? hex(alpha) : ""}`;
    colors.set(value, result);
    return result;
  }

  function add(primitive: CapturePrimitive) {
    if (primitives.length < maxPrimitives) primitives.push(primitive);
    else if (!unsupported["primitive limit"]) report("primitive limit");
  }

  function text(textValue: string, rect: CaptureBounds, style: CSSStyleDeclaration, base: PrimitiveBase, paint = style.color, scale = 1) {
    if (!textValue || !visible(rect, base.clip)) return;
    const fontSize = number(style.fontSize) * scale;
    const lineHeight = style.lineHeight === "normal" ? Math.max(fontSize * 1.2, rect.height) : number(style.lineHeight) * scale;
    add({
      ...base,
      ...rect,
      kind: "text",
      text: textValue,
      color: color(paint),
      fontFamily: style.fontFamily,
      fontSize,
      fontWeight: number(style.fontWeight) || 400,
      lineHeight,
      fontStyle: style.fontStyle,
      letterSpacing: number(style.letterSpacing) * scale,
      textDecoration: style.textDecorationLine,
    });
  }

  function textNode(node: Text, style: CSSStyleDeclaration, base: PrimitiveBase, offset: Offset) {
    const value = node.data;
    if (!value.trim()) return;
    const range = node.ownerDocument.createRange();
    range.selectNodeContents(node);
    const rects = Array.from(range.getClientRects()).filter((rect) => rect.width > 0 && rect.height > 0);
    const preserveSpaces = ["pre", "pre-wrap", "break-spaces"].includes(style.whiteSpace);
    function formatted(from: number, to: number) {
      let result = value.slice(from, to);
      if (!preserveSpaces) result = result.replace(/[\t\n\f\r ]+/g, " ").trim();
      if (style.textTransform === "uppercase") return result.toLocaleUpperCase();
      if (style.textTransform === "lowercase") return result.toLocaleLowerCase();
      if (style.textTransform === "capitalize") return result.replace(/(^|\s)(\p{L})/gu, (_, space, letter) => space + letter.toLocaleUpperCase());
      return result;
    }
    if (rects.length === 1) {
      text(formatted(0, value.length), bounds(rects[0], offset), style, base);
      return;
    }

    // Locate each line break by expanding a DOM range along the source text.
    // This leaves font shaping, whitespace collapse, and wrapping to the browser.
    let from = 0;
    for (const rect of rects) {
      let low = from + 1;
      let high = value.length;
      while (low < high) {
        const midpoint = Math.floor((low + high + 1) / 2);
        range.setStart(node, from);
        range.setEnd(node, midpoint);
        const advances = Array.from(range.getClientRects()).some((item) => item.width > 0 && item.top > rect.top + 1);
        if (advances) high = midpoint - 1;
        else low = midpoint;
      }
      text(formatted(from, low), bounds(rect, offset), style, base);
      from = low;
      if (from >= value.length) break;
    }
  }

  const svgTags = new Set(["svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "defs", "lineargradient", "radialgradient", "stop", "clippath", "mask"]);
  const svgAttributes = new Set([
    "id", "viewBox", "preserveAspectRatio", "x", "y", "width", "height", "d", "points",
    "cx", "cy", "r", "rx", "ry", "x1", "x2", "y1", "y2", "fx", "fy", "fr", "transform",
    "gradientTransform", "gradientUnits", "spreadMethod", "offset", "clipPathUnits", "maskUnits", "maskContentUnits",
  ]);
  const svgStyles = [
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-opacity", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset",
    "opacity", "stop-color", "stop-opacity", "clip-path", "clip-rule", "mask",
  ];

  function vector(root: SVGSVGElement, base: PrimitiveBase, offset: Offset) {
    const shapes: CaptureSvgShape[] = [];
    const labels: Array<() => void> = [];
    const inverse = root.getScreenCTM()?.inverse();
    function readLabel(element: SVGTextContentElement, parentOpacity: number) {
      const style = elementStyle(element);
      const matrix = element.getScreenCTM();
      const opacity = parentOpacity * Number(style.opacity);
      if (style.display === "none" || style.visibility !== "visible" || opacity <= 0 || !matrix) return;
      const scale = Math.hypot(matrix.c, matrix.d);
      if (style.stroke !== "none" || style.fill.startsWith("url(")) report("SVG text paint");
      const paint = style.fill === "none" ? style.stroke : style.fill;
      const paintOpacity = style.fill === "none" ? Number(style.strokeOpacity) : Number(style.fillOpacity);
      for (const child of Array.from(element.childNodes)) {
        if (child.nodeType === Node.TEXT_NODE && child.textContent?.trim()) {
          const range = child.ownerDocument!.createRange();
          range.selectNodeContents(child);
          const rect = bounds(range.getBoundingClientRect(), offset);
          const value = child.textContent.replace(/\s+/g, " ").trim();
          labels.push(() => text(value, rect, style, { ...base, opacity: opacity * paintOpacity }, paint, scale));
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          if ((child as Element).localName === "tspan") readLabel(child as SVGTextContentElement, opacity);
          else report(`SVG ${(child as Element).localName}`);
        }
      }
    }

    function copy(element: Element, inDefinition = false, parentOpacity = base.opacity): Element | null {
      if (element.localName === "text") {
        if (!inDefinition) readLabel(element as SVGTextContentElement, parentOpacity);
        return null;
      }
      if (!svgTags.has(element.localName.toLowerCase())) {
        if (!["title", "desc"].includes(element.localName)) report(`SVG ${element.localName}`);
        return null;
      }
      const style = elementStyle(element);
      if (style.display === "none" || style.visibility !== "visible") return null;
      const opacity = element === root ? parentOpacity : parentOpacity * Number(style.opacity);
      const clone = document.createElementNS("http://www.w3.org/2000/svg", element.localName);
      const attributes: Record<string, string> = {};
      for (const { name, value } of Array.from(element.attributes)) {
        if (svgAttributes.has(name)) attributes[name] = value;
      }
      for (const name of svgStyles) {
        let value = style.getPropertyValue(name);
        if (!value || value === "normal") continue;
        if (value.includes("url(")) {
          const fragment = value.match(/#[\w:.-]+(?=["')])/);
          if (!fragment || !Array.from(root.querySelectorAll("[id]")).some((target) => `#${target.id}` === fragment[0])) {
            report("SVG external paint reference");
            continue;
          }
          value = `url(${fragment[0]})`;
        } else if (["fill", "stroke", "stop-color"].includes(name) && value !== "none") {
          value = color(value);
        }
        attributes[name] = value;
      }
      if (element === root) {
        delete attributes.opacity;
        attributes.width = String(base.width);
        attributes.height = String(base.height);
      }
      for (const [name, value] of Object.entries(attributes)) clone.setAttribute(name, value);
      if (!inDefinition && ["path", "rect", "circle", "ellipse", "line", "polyline", "polygon"].includes(element.localName)) {
        const ctm = (element as SVGGeometryElement).getScreenCTM();
        const geometry = inverse && ctm ? inverse.multiply(ctm) : undefined;
        const matrix = geometry && [geometry.a, geometry.b, geometry.c, geometry.d, geometry.e, geometry.f];
        shapes.push({
          tag: element.localName,
          attributes,
          matrix: matrix?.every(Number.isFinite) ? matrix as CaptureSvgShape["matrix"] : undefined,
        });
      }
      for (const child of Array.from(element.children)) {
        const definition = inDefinition || ["defs", "clippath", "mask"].includes(element.localName.toLowerCase());
        const copiedChild = copy(child, definition, opacity);
        if (copiedChild) clone.append(copiedChild);
      }
      return clone;
    }
    const copied = copy(root);
    if (copied) {
      add({
        ...base,
        kind: "svg",
        viewBox: copied.getAttribute("viewBox") ?? `0 0 ${base.width} ${base.height}`,
        preserveAspectRatio: copied.getAttribute("preserveAspectRatio") ?? "xMidYMid meet",
        shapes,
        svg: new XMLSerializer().serializeToString(copied),
      });
    }
    for (const label of labels) label();
  }

  function controlIcon(base: PrimitiveBase, style: CSSStyleDeclaration, icon: "calendar" | "chevron") {
    const size = icon === "calendar" ? 14 : 10;
    const x = base.x + base.width - size - Math.max(4, number(style.paddingRight));
    const y = base.y + (base.height - size) / 2;
    const paths = icon === "calendar"
      ? ["M2 3 H12 V12 H2 Z M2 6 H12 M4 1 V4 M10 1 V4"]
      : ["M2 3.5 L5 6.5 L8 3.5"];
    const stroke = color(style.color);
    const shapes = paths.map((d) => ({ tag: "path", attributes: { d, stroke, fill: "none", "stroke-width": "1.3" } }));
    const viewBox = `0 0 ${size} ${size}`;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" width="${size}" height="${size}">${paths.map((d) => `<path d="${d}" stroke="${stroke}" fill="none" stroke-width="1.3"/>`).join("")}</svg>`;
    add({ ...base, x, y, width: size, height: size, kind: "svg", viewBox, preserveAspectRatio: "xMidYMid meet", shapes, svg });
  }

  function dateInput(value: string, withTime: boolean): string {
    if (!value) return withTime ? "mm/dd/yyyy, --:-- --" : "mm/dd/yyyy";
    const [day, time] = value.split("T");
    const instant = new Date(`${day}T${time ?? "00:00"}Z`);
    if (!Number.isFinite(instant.valueOf())) return value;
    const date = new Intl.DateTimeFormat(navigator.language, {
      year: "numeric", month: "2-digit", day: "2-digit", timeZone: "UTC",
      ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
    });
    return date.format(instant);
  }

  function control(element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, style: CSSStyleDeclaration, base: PrimitiveBase) {
    const type = element.localName === "input" ? (element as HTMLInputElement).type : "";
    if (["checkbox", "radio", "range", "file", "color"].includes(type)) {
      report(`native ${type} control`);
      return;
    }
    if (type === "hidden") return;
    const isSelect = element.localName === "select";
    const isDate = type === "date" || type === "datetime-local";
    const icon = style.appearance !== "none" && (isDate || (isSelect && !(element as HTMLSelectElement).multiple && (element as HTMLSelectElement).size <= 1));
    if (icon) controlIcon(base, style, isDate ? "calendar" : "chevron");
    const placeholder = isSelect ? "" : (element as HTMLInputElement | HTMLTextAreaElement).placeholder;
    let value = isSelect
      ? Array.from((element as HTMLSelectElement).selectedOptions, (option) => option.label).join(", ")
      : element.value;
    if (type === "password") value = "•".repeat(value.length);
    if (isDate) value = dateInput(value, type === "datetime-local");
    const textStyle = !value && placeholder ? elementStyle(element, "::placeholder") : style;
    value ||= placeholder;
    if (!value) return;

    const x = base.x + number(style.borderLeftWidth) + number(style.paddingLeft);
    const y = base.y + number(style.borderTopWidth) + number(style.paddingTop);
    const contentWidth = base.x + base.width - x - number(style.borderRightWidth) - number(style.paddingRight) - (icon ? 16 : 0);
    const contentHeight = base.y + base.height - y - number(style.borderBottomWidth) - number(style.paddingBottom);
    const lineHeight = number(textStyle.lineHeight) || number(textStyle.fontSize) * 1.2;
    if (context) {
      context.font = `${textStyle.fontStyle} ${textStyle.fontWeight} ${textStyle.fontSize} ${textStyle.fontFamily}`;
    }
    const available = { x, y, width: contentWidth, height: contentHeight };
    const clip = intersect(available, base.clip);
    const lines = value.split("\n");
    let top = element.localName === "textarea" ? y - element.scrollTop : y + (contentHeight - lineHeight) / 2;
    for (const line of lines) {
      const lineWidth = context?.measureText(line).width ?? contentWidth;
      const alignment = style.textAlign;
      let left = x;
      if (alignment === "center") left += (contentWidth - lineWidth) / 2;
      else if (alignment === "right" || (alignment === "end" && style.direction === "ltr")) left += contentWidth - lineWidth;
      text(line, { x: left - element.scrollLeft, y: top, width: lineWidth, height: lineHeight }, textStyle, { ...base, clip });
      top += lineHeight;
    }
  }

  function inputRole(input: HTMLInputElement) {
    switch (input.type) {
      case "button": case "submit": case "reset": case "image": return "button";
      case "checkbox": return "checkbox";
      case "number": return "spinbutton";
      case "radio": return "radio";
      case "range": return "slider";
      case "search": return "searchbox";
      default: return "textbox";
    }
  }

  function canvasArtwork(canvas: HTMLCanvasElement, base: PrimitiveBase) {
    const area = canvas.width * canvas.height;
    if (area <= 0 || area > 2_000_000) {
      report("canvas dimensions");
      return;
    }
    try {
      const src = canvas.toDataURL("image/png");
      if (src.length > 2_000_000) {
        report("canvas encoding size");
        return;
      }
      add({
        ...base,
        kind: "image",
        sourceKind: "canvas",
        src,
        alt: canvas.getAttribute("aria-label") ?? "Canvas artwork",
        objectFit: "fill",
        objectPosition: "50% 50%",
      });
    } catch {
      report("unavailable canvas artwork");
    }
  }

  function visit(element: Element, parentId: number | null, clip: CaptureBounds, parentOpacity: number, offset: Offset, frameDepth: number) {
    const tag = element.localName;
    if (["script", "style", "link", "template", "noscript"].includes(tag)) return;
    const style = elementStyle(element);
    const opacity = parentOpacity * Number(style.opacity);
    if (style.display === "none" || style.contentVisibility === "hidden" || opacity <= 0) return;

    const rect = bounds(element.getBoundingClientRect(), offset);
    if (style.clipPath === "inset(50%)" || style.clip === "rect(0px, 0px, 0px, 0px)") return;
    const shown = visible(rect, clip) && style.visibility === "visible";
    const id = groups.length;
    const anchor = tag === "a" && element.namespaceURI === "http://www.w3.org/1999/xhtml" ? element as HTMLAnchorElement : null;
    const href = anchor?.origin === location.origin ? `${anchor.pathname}${anchor.search}${anchor.hash}` : undefined;
    const role = element.getAttribute("role") ?? (tag === "input" ? inputRole(element as HTMLInputElement) : implicitRoles[tag] ?? "");
    const label = element.getAttribute("aria-label") ?? element.getAttribute("title") ?? element.getAttribute("alt") ?? "";
    groups.push({
      ...rect,
      id,
      parentId,
      tag,
      role,
      label,
      href,
      classes: Array.from(element.classList),
    });
    const base = { ...rect, groupId: id, opacity, clip };

    if (shown) {
      const fill = color(style.backgroundColor);
      const borders = ["top", "right", "bottom", "left"].map((side) => ({
        width: number(style.getPropertyValue(`border-${side}-width`)),
        color: color(style.getPropertyValue(`border-${side}-color`)),
        style: style.getPropertyValue(`border-${side}-style`),
      }));
      const radii = [
        style.borderTopLeftRadius, style.borderTopRightRadius,
        style.borderBottomRightRadius, style.borderBottomLeftRadius,
      ].map((radius) => radius.endsWith("%") ? number(radius) * Math.min(rect.width, rect.height) / 100 : number(radius));
      if (fill !== "#00000000" || borders.some((border) => border.width > 0)) {
        add({ ...base, kind: "rect", fill, borders, radii });
      }

      if (style.backgroundImage !== "none") report("CSS background image");
      if (style.boxShadow !== "none") report("CSS box shadow");
      if (style.filter !== "none" || style.backdropFilter !== "none") report("CSS filter");
      if (style.clipPath !== "none") report("CSS clip path");
      if (style.transform !== "none") report("CSS transform");
      if (style.outlineStyle !== "none") report("CSS outline");
      for (const selector of ["::before", "::after"]) {
        const content = elementStyle(element, selector).content;
        if (content !== "none" && content !== "normal") report(`generated content ${selector}`);
      }
      if (tag === "svg") {
        vector(element as SVGSVGElement, base, offset);
        return;
      }
      if (tag === "img") {
        const image = element as HTMLImageElement;
        const src = image.currentSrc || image.src;
        if (src && image.naturalWidth) {
          add({
            ...base,
            kind: "image",
            src,
            alt: image.alt,
            objectFit: style.objectFit,
            objectPosition: style.objectPosition,
          });
        } else report("unavailable image");
        return;
      }
      if (tag === "input" || tag === "textarea" || tag === "select") {
        control(element as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, style, base);
        return;
      }
      if (tag === "iframe") {
        const frame = (element as HTMLIFrameElement).contentDocument;
        if (!frame?.documentElement || !frame.defaultView || frameDepth >= 4) {
          report("unavailable iframe");
          return;
        }
        const x = rect.x + element.clientLeft;
        const y = rect.y + element.clientTop;
        const frameClip = intersect(clip, { x, y, width: element.clientWidth, height: element.clientHeight });
        if (frameClip.width > 0 && frameClip.height > 0) {
          visit(frame.documentElement, id, frameClip, opacity, { x, y }, frameDepth + 1);
        }
        return;
      }
      if (tag === "canvas") {
        canvasArtwork(element as HTMLCanvasElement, base);
        return;
      }
      if (["video", "object", "embed"].includes(tag)) {
        report(tag);
        return;
      }
    }

    const clipX = ["auto", "scroll", "hidden", "clip"].includes(style.overflowX);
    const clipY = ["auto", "scroll", "hidden", "clip"].includes(style.overflowY);
    const childClip = clipX || clipY ? intersect(clip, {
      x: clipX ? rect.x + element.clientLeft : clip.x,
      y: clipY ? rect.y + element.clientTop : clip.y,
      width: clipX ? element.clientWidth : clip.width,
      height: clipY ? element.clientHeight : clip.height,
    }) : clip;
    if (childClip.width <= 0 || childClip.height <= 0) return;

    // Flex/grid order and positioned stacking levels can differ from DOM order.
    const nodes = Array.from(element.childNodes).map((node, index) => {
      const childStyle = node.nodeType === Node.ELEMENT_NODE ? elementStyle(node as Element) : null;
      const zIndex = childStyle && (childStyle.position !== "static" || /flex|grid/.test(style.display))
        ? number(childStyle.zIndex) : 0;
      return { node, index, zIndex, order: childStyle ? number(childStyle.order) : 0 };
    });
    nodes.sort((a, b) => a.zIndex - b.zIndex || a.order - b.order || a.index - b.index);
    for (const { node } of nodes) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        visit(node as Element, id, childClip, opacity, offset, frameDepth);
      } else if (node.nodeType === Node.TEXT_NODE && style.visibility === "visible") {
        textNode(node as Text, style, { ...base, clip: childClip }, offset);
      }
    }
  }

  visit(document.documentElement, null, viewport, 1, { x: 0, y: originY }, 0);
  return { width, height, groups, primitives, unsupported };
}
