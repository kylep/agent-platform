export type MotionEasing = "linear" | "ease-in" | "ease-out" | "ease-in-out";
export type MotionInput = { version: number; mode?: string; from_node: string; to_node: string; duration_ms: number; easing: MotionEasing; fps: 12 | 24; max_edge: number };
export type MotionPose = { x: number; y: number; w: number; h: number; rotate: number; opacity: number; asset: string };
export type MotionManifest = { renderer_version: string; motion_version: "motion-1"; width: number; height: number; duration_ms: number; fps: number; frames: number; easing: MotionEasing; from_image: string; to_image: string; assets: Record<string,string>; layers: {id:string;from:MotionPose|null;to:MotionPose|null}[]; warnings: string[] };
export function motionEase(t: number, easing: MotionEasing) {
  t = Math.max(0,Math.min(1,t));
  return easing === "ease-in" ? t*t : easing === "ease-out" ? 1-(1-t)**2 : easing === "ease-in-out" ? t<0.5 ? 2*t*t : 1-2*(1-t)**2 : t;
}
export function validateMotion(value: MotionManifest): MotionManifest {
  if (!value || new TextEncoder().encode(JSON.stringify(value)).length > 16*1024*1024 || value.motion_version !== "motion-1" || !Number.isInteger(value.width) || !Number.isInteger(value.height) || value.width<1 || value.height<1 || value.width>720 || value.height>720 || !Number.isFinite(value.duration_ms) || value.duration_ms<200 || value.duration_ms>5000 || ![12,24].includes(value.fps) || !Number.isInteger(value.frames) || value.frames<2 || value.frames>120 || !["linear","ease-in","ease-out","ease-in-out"].includes(value.easing) || !Array.isArray(value.layers) || value.layers.length>64 || !value.assets || typeof value.assets!=="object" || Object.keys(value.assets).length>128 || !Array.isArray(value.warnings) || value.warnings.length>32 || value.warnings.some((w)=>typeof w!=="string" || w.length>500)) throw new Error("The motion preview is invalid or exceeds its preview limits.");
  let pixels=0;const seen=new Set<string>();
  function png(data: string) {
    if(seen.has(data))return;seen.add(data);
    if (typeof data!=="string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(data)) throw new Error("Motion preview contains an invalid image.");
    const header=atob(data.slice(0,44));
    if (header.length<24 || ![137,80,78,71,13,10,26,10].every((byte,i)=>header.charCodeAt(i)===byte)) throw new Error("Motion preview contains an invalid image.");
    const dimension=(offset:number)=>Array.from(header.slice(offset,offset+4)).reduce((n,c)=>n*256+c.charCodeAt(0),0);
    pixels+=dimension(16)*dimension(20);
    if(pixels>32*1024*1024)throw new Error("Motion preview images exceed the memory limit.");
    if (dimension(16)<1 || dimension(20)<1 || dimension(16)>720 || dimension(20)>720) throw new Error("Motion preview image exceeds its size limit.");
  }
  png(value.from_image); png(value.to_image); Object.values(value.assets).forEach(png);
  for (const layer of value.layers) {
    if (!layer || typeof layer.id!=="string" || (!layer.from && !layer.to)) throw new Error("Motion preview contains an invalid layer.");
    for (const pose of [layer.from,layer.to]) if (pose && (![pose.x,pose.y,pose.w,pose.h,pose.rotate,pose.opacity].every(Number.isFinite) || pose.w<0 || pose.h<0 || pose.w>100000 || pose.h>100000 || Math.abs(pose.x)>100000 || Math.abs(pose.y)>100000 || Math.abs(pose.rotate)>100000 || pose.opacity<0 || pose.opacity>1 || !Object.hasOwn(value.assets,pose.asset))) throw new Error("Motion preview contains invalid layer geometry.");
  }
  return value;
}
