import "./SharingPanel.css";
import { useEffect, useRef, useState } from "react";
import { Button } from "@ap/ui/button";
import { Select } from "@ap/ui/field";
import { api, apiErrorMessage } from "../../api";
import type { DesignerAccess } from "../../lib/useDesignerAccess";
type Choice={principal:string;label:string;kind?:string};
type Choices={principals:Choice[];teams:{principal:string;id:string;name:string;slug:string}[];truncated:boolean};
type Props={base:string;access:DesignerAccess;checking:boolean;onRefresh:()=>void;onPending:(pending:boolean)=>void};
export default function SharingPanel(props:Props){
  const {base,access,checking}=props;const callbacks=useRef(props);callbacks.current=props;
  const [choices,setChoices]=useState<Choices>({principals:[],teams:[],truncated:false}),[choiceError,setChoiceError]=useState("");
  const [visibility,setVisibility]=useState(access.visibility),[readers,setReaders]=useState(access.readers),[editors,setEditors]=useState(access.editors),[revision,setRevision]=useState(access.access_version);
  const [dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[uncertain,setUncertain]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
  const [newPrincipal,setNewPrincipal]=useState(""),[newRole,setNewRole]=useState("reader");
  const pending=useRef<string|null>(null),working=useRef(false),alive=useRef(true),operation=useRef<AbortController|null>(null);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;operation.current?.abort();callbacks.current.onPending(false);};},[]);
  useEffect(()=>{
    if(pending.current || dirty)return;
    setVisibility(access.visibility);setReaders(access.readers);setEditors(access.editors);setRevision(access.access_version);
  },[access,dirty]);
  useEffect(()=>{
    if(!access.can_manage_sharing)return;
    const controller=new AbortController();setChoiceError("");
    api<Choices>(`/api/ui-designer/sharing-choices?${new URLSearchParams({slug:decodeURIComponent(base.split('/').at(-1)!)})}`,{signal:controller.signal})
      .then((value)=>{if(!controller.signal.aborted)setChoices(value);}).catch((e)=>{if(!controller.signal.aborted)setChoiceError(apiErrorMessage(e,"Could not load sharing choices."));});
    return()=>controller.abort();
  },[base,access.can_manage_sharing]);
  const names=new Map([...choices.principals.map((p)=>[p.principal,p.label] as const),...choices.teams.map((t)=>[t.principal,`${t.name} (team)`] as const)]);
  const locked=busy || uncertain || checking || !access.can_manage_sharing;
  function updateRole(principal:string,role:string){setReaders((old)=>role==="reader" ? [...new Set([...old,principal])]:old.filter((p)=>p!==principal));setEditors((old)=>role==="editor" ? [...new Set([...old,principal])]:old.filter((p)=>p!==principal));setDirty(true);setNotice("");}
  async function save(retry=false){
    if(working.current || !access.can_manage_sharing || (!retry && (checking || uncertain)))return;
    if(!retry){const request_id=Array.from(crypto.getRandomValues(new Uint8Array(16)),(b)=>b.toString(16).padStart(2,"0")).join("");pending.current=JSON.stringify({expected_access_version:revision,request_id,visibility,readers,editors});}
    if(!pending.current)return;
    const controller=new AbortController();operation.current=controller;working.current=true;setBusy(true);setError("");callbacks.current.onPending(true);
    try{
      await api<DesignerAccess>(`${base}/access`,{method:"PUT",signal:controller.signal,body:pending.current});
      if(!alive.current || controller.signal.aborted)return;
      pending.current=null;setUncertain(false);setDirty(false);setNotice("Sharing saved.");callbacks.current.onRefresh();callbacks.current.onPending(false);
    }catch(e){
      if(!alive.current || controller.signal.aborted)return;
      const status=e instanceof Error ? Number(/^(\d+):/.exec(e.message)?.[1]):0;
      if(!(status>=400 && status<500 && status!==408)){setUncertain(true);setError("The sharing save could not be confirmed. Retry the same save.");}
      else{pending.current=null;setUncertain(false);callbacks.current.onPending(false);if(status===409){setDirty(false);setError("Sharing changed elsewhere. Reloaded the latest settings; review them and save again.");callbacks.current.onRefresh();}else{setError(apiErrorMessage(e,"Could not save sharing."));if(status===403 || status===404)callbacks.current.onRefresh();}}
    }finally{if(alive.current && !controller.signal.aborted){working.current=false;setBusy(false);}}
  }
  const members=[...new Set([...readers,...editors])];
  return <section className="ud-sharing" aria-label="Document sharing"><h2>Sharing</h2><p>Owner: {names.get(access.owner) ?? access.owner.replace(/^(user|agent):/,"")}</p>
    <p className="muted">Workspace access follows existing platform permissions. Restricted documents are available to their owner and the people or teams selected here. Sharing applies to every document version.</p>
    {!access.can_manage_sharing && <p className="muted">Only the owner or an administrator can change sharing.</p>}
    {choiceError && <p role="alert">{choiceError}</p>}
    <fieldset disabled={locked}><label>Document visibility <Select aria-label="Document visibility" value={visibility} onChange={(e)=>{setVisibility(e.target.value as typeof visibility);setDirty(true);setNotice("");}}><option value="workspace">Workspace</option><option value="restricted">Restricted</option></Select></label>
      <ul aria-label="Shared access">{members.map((principal)=><li key={principal}><span>{names.get(principal) ?? principal}</span>{" "}<Select aria-label={`Access for ${names.get(principal) ?? principal}`} value={editors.includes(principal)?"editor":"reader"} onChange={(e)=>updateRole(principal,e.target.value)}><option value="reader">Can view</option><option value="editor">Can edit</option><option value="remove">Remove access</option></Select></li>)}</ul>
      {access.can_manage_sharing && <div className="ud-actions"><label>Person or team <Select aria-label="Share with person or team" value={newPrincipal} onChange={(e)=>setNewPrincipal(e.target.value)}><option value="">Choose…</option><optgroup label="People and agents">{choices.principals.map((p)=><option key={p.principal} value={p.principal}>{p.label}</option>)}</optgroup><optgroup label="Teams">{choices.teams.map((t)=><option key={t.principal} value={t.principal}>{t.name}</option>)}</optgroup></Select></label><Select aria-label="New sharing permission" value={newRole} onChange={(e)=>setNewRole(e.target.value)}><option value="reader">Can view</option><option value="editor">Can edit</option></Select><Button disabled={!newPrincipal || members.length>=64} onClick={()=>{updateRole(newPrincipal,newRole);setNewPrincipal("");}}>Add access</Button></div>}
      {choices.truncated && <p className="muted">Some sharing choices are omitted. Contact an administrator if the person or team you need is unavailable.</p>}
      <Button disabled={!dirty} onClick={()=>void save()}>Save sharing</Button>
    </fieldset>
    {busy && <p role="status">Saving sharing…</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {uncertain && <Button disabled={busy || !access.can_manage_sharing} onClick={()=>void save(true)}>Retry sharing save</Button>}
  </section>;
}
