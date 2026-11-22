import "./Presence.css";
import type { PresenceSession } from "../../lib/useDesignerPresence";
import { presenceColor } from "../../lib/uiPresence";
export default function PresenceBar({sessions,notice,version}:{sessions:PresenceSession[];notice:string;version:number|null}){
  const groups=new Map<string,PresenceSession[]>();for(const session of sessions){const group=groups.get(session.participant) ?? [];group.push(session);groups.set(session.participant,group);}
  return <section className="ud-presence" aria-label="People in this design"><h2>People here</h2>{groups.size ? <ul>{[...groups].sort(([a],[b])=>a.localeCompare(b)).map(([participant,rows])=><li key={participant}><span className="ud-presence-dot" style={{background:presenceColor(participant)}} aria-hidden="true" /><span>{rows[0].label} · {rows.some(row=>row.state==="editing")?"editing":"viewing"}{rows.length>1?` · ${rows.length} tabs`:""}{rows.some(row=>row.version!==version)?` · ${[...new Set(rows.map(row=>`v${row.version}`))].join(", ")}`:""}</span></li>)}</ul>:<p className="muted">No other viewers.</p>}{notice && <p role="status" className="muted">{notice}</p>}</section>;
}
