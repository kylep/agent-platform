import {useEffect,useLayoutEffect,useRef,useState} from "react";
import {api,apiErrorMessage} from "../api";
import type {UiComment,UiCommentNotification} from "./uiDesigner";
type Pending={body:string;nodeId:string|null};
type Reply={document_id?:string;id?:string|null;comment?:UiComment|null;notifications?:UiCommentNotification[];replayed?:boolean};
type Input={base:string;documentId:string|null;enabled:boolean;canWrite:boolean;revision:number;onAccessCheck:()=>void};
export function useDesignerComments(input:Input){
  const latest=useRef(input);latest.current=input;
  const [comments,setComments]=useState<UiComment[]>([]),[draft,setDraft]=useState(""),[busy,setBusy]=useState(false),[uncertain,setUncertain]=useState(false),[error,setError]=useState(""),[reload,setReload]=useState(0),[receipt,setReceipt]=useState<{id:string|null;notifications:UiCommentNotification[]}|null>(null);
  const generation=useRef(0),working=useRef(false),pending=useRef<Pending|null>(null),controllers=useRef(new Set<AbortController>());
  const {base,documentId,enabled,revision}=input;
  useLayoutEffect(()=>{generation.current++;controllers.current.forEach(controller=>controller.abort());controllers.current.clear();pending.current=null;working.current=false;setComments([]);setDraft("");setBusy(false);setUncertain(false);setError("");setReceipt(null);},[base,documentId]);
  useEffect(()=>{const cancel=()=>{generation.current++;controllers.current.forEach(controller=>controller.abort());controllers.current.clear();};return cancel;},[]);
  useEffect(()=>{
    if(!enabled || !documentId){setComments([]);return;}
    const controller=new AbortController(),scope=generation.current,active=controllers.current;active.add(controller);
    api<{document_id?:string;comments:UiComment[]}>(`${base}/comments?${new URLSearchParams({expected_document_id:documentId})}`,{signal:controller.signal}).then(value=>{
      if(controller.signal.aborted || scope!==generation.current)return;
      if(value.document_id && value.document_id!==documentId){setComments([]);latest.current.onAccessCheck();return;}
      setComments(value.comments ?? []);
    }).catch(e=>{if(!controller.signal.aborted && scope===generation.current){if(e instanceof Error && /^(403|404|409):/.test(e.message)){setComments([]);latest.current.onAccessCheck();}else setError(apiErrorMessage(e,"Could not load comments."));}}).finally(()=>active.delete(controller));
    return()=>{controller.abort();active.delete(controller);};
  },[base,documentId,enabled,revision,reload]);
  async function send(payload:Pending){
    if(working.current || !latest.current.canWrite)return;
    const scope=generation.current,controller=new AbortController();controllers.current.add(controller);working.current=true;setBusy(true);setError("");
    try{
      const value=await api<Reply>(`${base}/comments`,{method:"POST",body:payload.body,signal:controller.signal});
      if(controller.signal.aborted || scope!==generation.current)return;
      if(value.document_id && value.document_id!==documentId){setUncertain(true);setError("This design changed. Reload before commenting.");latest.current.onAccessCheck();return;}
      pending.current=null;setUncertain(false);setDraft("");
      const notifications=value.notifications ?? value.comment?.notifications ?? [];
      setReceipt({id:value.id ?? value.comment?.id ?? null,notifications});
      if(value.comment)setComments(old=>[...old.filter(comment=>comment.id!==value.comment!.id),{...value.comment!,notifications:value.comment!.notifications ?? notifications}]);
      setReload(value=>value+1);
    }catch(e){
      if(controller.signal.aborted || scope!==generation.current)return;
      const status=e instanceof Error ? Number(/^(\d+):/.exec(e.message)?.[1]):0;
      if(!(status>=400 && status<500 && status!==408)){setUncertain(true);setError("The comment save could not be confirmed. Retry the same comment to avoid duplicate requests.");}
      else{pending.current=null;setUncertain(false);setError(apiErrorMessage(e,"Could not save this comment."));if(status===403 || status===404)latest.current.onAccessCheck();}
    }finally{controllers.current.delete(controller);if(!controller.signal.aborted && scope===generation.current){working.current=false;setBusy(false);}}
  }
  function add(nodeId:string|null){
    if(working.current || pending.current || !input.canWrite || !draft.trim() || draft.length>8192)return;
    const request_id=Array.from(crypto.getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,"0")).join("");
    const payload={nodeId,body:JSON.stringify({expected_document_id:documentId,node_id:nodeId,parent_id:null,body:draft.trim(),request_id})};pending.current=payload;void send(payload);
  }
  async function resolve(id:string){
    if(working.current || pending.current || !input.canWrite)return;
    const scope=generation.current,controller=new AbortController();controllers.current.add(controller);working.current=true;setBusy(true);setError("");
    try{await api(`${base}/comments/${encodeURIComponent(id)}/resolve`,{method:"POST",signal:controller.signal});if(!controller.signal.aborted && scope===generation.current)setReload(value=>value+1);}
    catch(e){if(!controller.signal.aborted && scope===generation.current)setError(apiErrorMessage(e,"Could not resolve this comment."));}
    finally{controllers.current.delete(controller);if(!controller.signal.aborted && scope===generation.current){working.current=false;setBusy(false);}}
  }
  return {comments,draft,setDraft,busy,uncertain,error,receipt,add,resolve,retry:()=>{if(pending.current)void send(pending.current);},pendingNodeId:pending.current?.nodeId};
}
