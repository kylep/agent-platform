import type { RNode, Resolved } from "./uiDesigner";
export type Box = { x: number; y: number; w: number; h: number };
export const nodeBox = (n: RNode): Box => n.bounds ? { x: n.bounds[0], y: n.bounds[1], w: n.bounds[2], h: n.bounds[3] } : { x: n.x ?? 0, y: n.y ?? 0, w: n.w ?? 0, h: n.h ?? 0 };
function intersect(a: Box, b: Box): Box {
  const x = Math.max(a.x,b.x), y = Math.max(a.y,b.y);
  return { x,y,w: Math.max(0,Math.min(a.x+a.w,b.x+b.w)-x),h:Math.max(0,Math.min(a.y+a.h,b.y+b.h)-y) };
}
export function prototypeFrames(tree: Resolved): RNode[] {
  const found: RNode[] = [];
  function walk(nodes: RNode[]) { for (const n of nodes) { if (n.visible === false) continue; if (n.type === "frame" && !n.of && !n.id.includes("/")) found.push(n); walk(n.children ?? []); } }
  for (const page of tree.pages ?? []) walk(page.children ?? []);
  return found;
}
export function prototypeHotspots(frame: RNode): { node: RNode; box: Box }[] {
  const found: { node: RNode; box: Box }[] = [];
  const crop = nodeBox(frame);
  function walk(n: RNode, clip: Box) {
    if (n.visible === false) return;
    const box = intersect(nodeBox(n),clip);
    if (n.link_to && box.w > 0 && box.h > 0) found.push({ node:n,box });
    const childClip = n.clip ? intersect(clip,nodeBox(n)) : clip;
    for (const child of n.children ?? []) walk(child,childClip);
  }
  walk(frame,crop);
  return found;
}
