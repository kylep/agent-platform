import { useCallback, useEffect, useRef, useState } from "react";
export type PresenceCursor={x:number;y:number};
export type PresenceSession={participant:string;label:string;client_session:string;state:"viewing"|"editing";version:number;page_id:string;node_id:string|null;cursor:PresenceCursor|null;sequence:number;expires_at:string};
type Roster={document_id:string;server_now:string;sessions:PresenceSession[]};
type TimedSession=PresenceSession & {deadline:number};
type Input={base:string;documentId:string|null;version:number|null;pageId:string|null;nodeId:string|null;editing:boolean;accessVersion:number;enabled:boolean;onAccessCheck:()=>void};
const randomId=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,"0")).join("");
export function useDesignerPresence(input:Input){
  const latest=useRef(input);latest.current=input;
  const [visible,setVisible]=useState(()=>document.visibilityState!=="hidden"),[sessions,setSessions]=useState<TimedSession[]>([]),[notice,setNotice]=useState("");
  const blocked=useRef<string|null>(null),nextAllowed=useRef(0);
  const ownIds=useRef(new Set<string>()),cursor=useRef<PresenceCursor|null>(null),lastCapture=useRef(0),captureTimer=useRef<ReturnType<typeof setTimeout>|null>(null),notify=useRef<(reset?:boolean)=>void>(()=>{});
  const active=input.enabled && !!input.documentId && !!input.version && !!input.pageId && visible;
  useEffect(()=>{const changed=()=>setVisible(document.visibilityState!=="hidden");document.addEventListener("visibilitychange",changed);return()=>document.removeEventListener("visibilitychange",changed);},[]);
  const onCursor=useCallback((next:PresenceCursor|null)=>{
    if(next && (![next.x,next.y].every(Number.isFinite) || Math.abs(next.x)>100000 || Math.abs(next.y)>100000))next=null;
    cursor.current=next;
    const now=performance.now();if(next && now-lastCapture.current<250){if(!captureTimer.current)captureTimer.current=setTimeout(()=>{captureTimer.current=null;lastCapture.current=performance.now();notify.current();},250-(now-lastCapture.current));return;}if(captureTimer.current)clearTimeout(captureTimer.current);captureTimer.current=null;lastCapture.current=now;notify.current();
  },[]);
  const tuple=JSON.stringify([input.version,input.pageId,input.nodeId,input.editing]);
  useEffect(()=>{cursor.current=null;if(captureTimer.current)clearTimeout(captureTimer.current);captureTimer.current=null;notify.current(true);},[input.version,input.pageId]);
  useEffect(()=>{notify.current(true);},[tuple,input.accessVersion]);
  useEffect(()=>{
    if(!active || !input.documentId){setSessions([]);setNotice("");notify.current=()=>{};return;}
    const base=input.base,documentId=input.documentId,clientSession=randomId();ownIds.current.add(clientSession);
    if(ownIds.current.size>128)ownIds.current.delete(ownIds.current.values().next().value!);
    const scopeKey=()=>JSON.stringify([base,documentId,latest.current.version,latest.current.pageId,latest.current.nodeId,latest.current.editing,latest.current.accessVersion]);
    let paused=blocked.current===scopeKey();
    let alive=true,working=false,dirty=!paused,nextSend=nextAllowed.current,lastGet=-Infinity,lastStamp="",lastServerTime=0;
    let sendTimer:ReturnType<typeof setTimeout>|null=null,expireTimer:ReturnType<typeof setTimeout>|null=null;
    let rows=new Map<string,TimedSession>();
    if(paused)setNotice("Presence updates are paused until document access or selection changes.");
    const controllers=new Set<AbortController>();
    const key=(row:PresenceSession)=>`${row.participant}:${row.client_session}`;
    function publish(){if(!alive)return;const now=performance.now();rows=new Map([...rows].filter(([,row])=>row.deadline>now));setSessions([...rows.values()]);if(expireTimer)clearTimeout(expireTimer);const deadline=Math.min(...[...rows.values()].map(row=>row.deadline));if(Number.isFinite(deadline))expireTimer=setTimeout(publish,Math.max(1,deadline-now));}
    function apply(roster:Roster,started=performance.now()){
      if(!alive)return;
      const serverTime=Date.parse(roster.server_now);
      if(roster.document_id!==documentId){pause("Presence paused because this document changed. Reload to rejoin.");return;}
      if(!Number.isFinite(serverTime) || !Array.isArray(roster.sessions) || roster.sessions.length>32 || new TextEncoder().encode(JSON.stringify(roster)).length>32768)throw new Error("Invalid presence roster");
      if(serverTime<lastServerTime || (serverTime===lastServerTime && roster.server_now<lastStamp))return;
      const now=performance.now(),fresh=new Map<string,TimedSession>();
      for(const row of roster.sessions){
        const expires=Date.parse(row.expires_at);
        if(typeof row.participant!=="string" || row.participant.length>160 || typeof row.label!=="string" || row.label.length>160 || !/^[a-f0-9]{32}$/.test(row.client_session) || !["viewing","editing"].includes(row.state) || !Number.isSafeInteger(row.sequence) || row.sequence<0 || !Number.isSafeInteger(row.version) || row.version<1 || typeof row.page_id!=="string" || row.page_id.length>384 || !Number.isFinite(expires) || (row.cursor && (![row.cursor.x,row.cursor.y].every(Number.isFinite) || Math.abs(row.cursor.x)>100000 || Math.abs(row.cursor.y)>100000)))continue;
        const previous=rows.get(key(row));
        if(previous && previous.sequence>row.sequence){if(previous.deadline>now)fresh.set(key(row),previous);continue;}
        const deadline=now+Math.min(45000,expires-serverTime)-(now-started);
        if(deadline>now)fresh.set(key(row),{...row,deadline});
      }
      rows=fresh;lastServerTime=serverTime;lastStamp=roster.server_now;publish();if(!paused)setNotice("");
    }
    function pause(message:string){const key=scopeKey(),already=blocked.current===key;blocked.current=key;paused=true;dirty=false;rows.clear();publish();setNotice(message);if(!already)latest.current.onAccessCheck();}
    async function response(response:Response,started:number){
      if(!alive)return false;
      if(response.status===401 || response.status===403 || response.status===404){pause("Presence unavailable for this design.");return false;}
      if(response.status===409){pause("Presence paused because this document changed. Reload to rejoin.");return false;}
      if(response.status===429){let delay=Number(response.headers.get("Retry-After"));if(!Number.isFinite(delay) || delay<=0){const date=Date.parse(response.headers.get("Retry-After") || "");delay=Number.isFinite(date)?(date-Date.now())/1000:1;}nextSend=Math.max(nextSend,performance.now()+Math.min(60000,Math.max(500,delay*1000)));nextAllowed.current=nextSend;setNotice("Presence updates are paused briefly. Reconnecting automatically…");return false;}
      if(!response.ok)throw new Error("Presence unavailable");
      apply(await response.json(),started);return true;
    }
    async function refresh(){
      if(!alive || performance.now()-lastGet<1000)return;lastGet=performance.now();
      const controller=new AbortController();controllers.add(controller);const started=performance.now();
      try{await response(await fetch(`${base}/presence`,{credentials:"same-origin",signal:controller.signal}),started);}catch{if(alive && !controller.signal.aborted)setNotice("Presence is temporarily unavailable.");}finally{controllers.delete(controller);}
    }
    function schedule(){if(!alive || paused || working || sendTimer)return;sendTimer=setTimeout(()=>{sendTimer=null;void send();},Math.max(0,nextSend-performance.now()));}
    async function send(){
      if(!alive || paused || working || !dirty)return;
      if(performance.now()<nextSend){schedule();return;}
      working=true;dirty=false;const controller=new AbortController();controllers.add(controller);const started=performance.now();nextSend=started+500;nextAllowed.current=nextSend;
      const current=latest.current;
      const body={document_id:documentId,version:current.version,page_id:current.pageId,state:current.editing?"editing":"viewing",node_id:current.nodeId,cursor:cursor.current};
      try{if(!await response(await fetch(`${base}/presence/${clientSession}`,{method:"PUT",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal:controller.signal}),started) && !paused)dirty=true;}catch{if(alive && !controller.signal.aborted){dirty=true;nextSend=Math.max(nextSend,performance.now()+2000);nextAllowed.current=nextSend;setNotice("Presence is temporarily unavailable.");}}finally{controllers.delete(controller);working=false;if(dirty)schedule();}
    }
    notify.current=(reset=false)=>{if(reset && blocked.current!==scopeKey()){blocked.current=null;paused=false;}if(paused)return;dirty=true;schedule();};
    const events=typeof EventSource!=="undefined" ? new EventSource(`${base}/presence/events`,{withCredentials:true}):null;
    events?.addEventListener("presence",message=>{try{apply(JSON.parse(message.data));}catch{void refresh();}});
    events?.addEventListener("open",()=>void refresh());events?.addEventListener("overflow",()=>void refresh());events?.addEventListener("error",()=>void refresh());
    const heartbeat=setInterval(()=>{void refresh();if(!paused){dirty=true;schedule();}},15000);
    void refresh();schedule();
    return()=>{alive=false;notify.current=()=>{};controllers.forEach(controller=>controller.abort());events?.close();if(sendTimer)clearTimeout(sendTimer);if(expireTimer)clearTimeout(expireTimer);clearInterval(heartbeat);if(captureTimer.current)clearTimeout(captureTimer.current);captureTimer.current=null;cursor.current=null;setSessions([]);void fetch(`${base}/presence/${clientSession}?${new URLSearchParams({document_id:documentId})}`,{method:"DELETE",credentials:"same-origin",keepalive:true}).catch(()=>{});};
  },[input.base,input.documentId,active]);
  return {sessions:sessions.filter(row=>!ownIds.current.has(row.client_session)),notice,onCursor,active};
}
