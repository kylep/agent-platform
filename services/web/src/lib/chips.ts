import { artifactRefs } from "./artifacts";
import { ticketRefs } from "./tickets";
import { wikiRefs } from "./wiki";

// Chips: the pass that turns `OPS-12` and `[[deploying]]` in ordinary prose
// into links, wherever that prose is rendered — a room message, a ticket body,
// a wiki page. One module because the hard part is not the rewrite, it is
// knowing where NOT to rewrite, and two copies of that answer would disagree
// the first time one of them learned something.
//
// It runs on markdown SOURCE rather than on rendered HTML because that is
// where code, links and fences are still telling the truth about themselves —
// once it is `<pre><code>` the text is a string of tags, and telling quoted
// text from prose means parsing them back.

// Where a reference is NOT a reference:
//
// - a fenced block, and an inline code span — quoted text either way;
// - a link or image, whole (`[see](https://x/OPS-2)`): rewriting inside one
//   corrupts the URL or nests a link inside link text, and both render as
//   nothing anybody can click;
// - an autolink or a raw tag, and a bare URL that markdown will autolink
//   itself — same corruption, one step later.
//
// Inline code is the one place this is stricter than the backend's ticket
// rule, which links a key in backticks because people habitually write them
// that way. On screen `OPS-2` is CODE: rewriting it there would put a literal
// `[OPS-2](/tickets/…)` inside a <code> element, which is worse than a missing
// chip. For `[[slug]]` the backend agrees — `wiki.find_links` skips inline
// spans too, so a page documenting the syntax creates no wanted page.
export const PROTECTED = new RegExp([
  "```[\\s\\S]*?(?:```|$)",        // fenced block; unclosed runs to the end
  "`+[^`]*`+",                     // inline code span
  // Link or image, whole. The text is allowed two levels of balanced
  // brackets, because `[See [[deploying]]](url)` is a link somebody wrote on
  // purpose: matching only `[^\]]*` would end the text at the first `]`,
  // leave the wiki pass looking at the rest, and nest an anchor inside an
  // anchor — which renders as nothing anybody can click.
  "!?\\[(?:[^\\[\\]]|\\[(?:[^\\[\\]]|\\[[^\\[\\]]*\\])*\\])*\\]\\([^)]*\\)",
  "<[^>\\s]+>",                    // autolink, or a raw tag
  "https?://\\S+",                 // a bare URL gfm will autolink
].join("|"), "g");

/** The text cut into stretches, each marked as protected or not, in order.
 * The one place the protection is applied: the rewrite below and the split
 * further down both walk this, so neither can learn a rule the other lacks. */
function* stretches(text: string): Generator<[chunk: string, guarded: boolean]> {
  let at = 0;
  for (const span of text.matchAll(PROTECTED)) {
    yield [text.slice(at, span.index), false];
    yield [span[0], true];
    at = span.index + span[0].length;
  }
  yield [text.slice(at), false];
}

/** Run `rewrite` over every stretch of ordinary prose, leaving every protected
 * span exactly as it was. */
export function outsideCode(text: string, rewrite: (plain: string) => string): string {
  let out = "";
  for (const [chunk, guarded] of stretches(text)) out += guarded ? chunk : rewrite(chunk);
  return out;
}

/** A body cut around its artifact cards: prose to render as markdown, and
 * the ids to render as cards between the pieces (docs/design/23).
 *
 * A split rather than a rewrite, because a card is a component and the
 * markdown renderer hands back HTML — there is no anchor a card could be
 * written as, and `<img>` is forbidden in rendered markdown on purpose. The
 * same protection applies: `[[artifact:…]]` in a code span or a link is
 * quoted, not used. Empty prose pieces are dropped, so a body that IS a chip
 * yields one card and no blank paragraph beside it. */
export type BodyPiece = { kind: "text"; text: string } | { kind: "artifact"; id: string };

export function splitArtifacts(text: string): BodyPiece[] {
  const out: BodyPiece[] = [];
  let buf = "";
  const flush = () => { if (buf.trim()) out.push({ kind: "text", text: buf }); buf = ""; };
  for (const [chunk, guarded] of stretches(text || "")) {
    if (guarded) { buf += chunk; continue; }
    let at = 0;
    for (const ref of artifactRefs(chunk)) {
      buf += chunk.slice(at, ref.start);
      flush();
      out.push({ kind: "artifact", id: ref.id });
      at = ref.end;
    }
    buf += chunk.slice(at);
  }
  flush();
  return out;
}

/** Every ticket key in one stretch of ordinary prose, as a markdown link. */
function plainTickets(text: string, prefixes: string[]): string {
  // `ticketRefs` blanks fenced blocks itself; there are none left in here, so
  // that pass is a no-op and the fence rule still has exactly one definition.
  const refs = ticketRefs(text, prefixes);
  if (refs.length === 0) return text;
  let out = "";
  let at = 0;
  for (const ref of refs) {
    out += text.slice(at, ref.start) + `[${ref.key}](/tickets/${ref.key})`;
    at = ref.end;
  }
  return out + text.slice(at);
}

/** `OPS-12` in a message, as a link to the ticket (docs/design/20). */
export function linkTickets(text: string, prefixes: string[]): string {
  if (!text || prefixes.length === 0) return text;
  return outsideCode(text, (plain) => plainTickets(plain, prefixes));
}

// How a red link tells the renderer it is one. The rendered markdown is
// `marked` + DOMPurify — elements, not React — so the only hooks a stylesheet
// gets are the ones that survive sanitising. `title` does (DOMPurify keeps it),
// it needs no second pass over the HTML, and it doubles as the tooltip that
// says what the colour means, which colour alone never can.
export const WANTED_TITLE = "wanted page — nobody has written this yet";

function plainWiki(text: string, known: Set<string> | null): string {
  const refs = wikiRefs(text);
  if (refs.length === 0) return text;
  let out = "";
  let at = 0;
  for (const ref of refs) {
    // `known === null` is "the page list has not landed yet": the link is
    // still a link, it just does not claim to be red. A momentary flash of
    // wanted chips on a page full of real ones is a lie the reader acts on.
    const wanted = known !== null && !known.has(ref.slug);
    out += text.slice(at, ref.start)
      + `[${ref.slug}](/wiki/${ref.slug}${wanted ? ` "${WANTED_TITLE}"` : ""})`;
    at = ref.end;
  }
  return out + text.slice(at);
}

/** `[[deploying]]` anywhere, as a link to the page (docs/design/21). `known` is
 * the set of live page slugs — anything outside it is a wanted page, which is
 * an invitation rather than a broken link. */
export function linkWiki(text: string, known: Set<string> | null): string {
  if (!text) return text;
  return outsideCode(text, (plain) => plainWiki(plain, known));
}

/** Both passes, in the order that keeps them independent: tickets first, then
 * wiki links over the result. A `[[slug]]` cannot appear inside a ticket chip
 * (the key grammar has no brackets) and the markdown link the first pass
 * writes is protected from the second. */
export function linkChips(text: string, prefixes: string[], known: Set<string> | null): string {
  return linkWiki(linkTickets(text, prefixes), known);
}
