import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiErrorMessage } from "../api";
export type DesignerAccess={document_id:string;owner:string;visibility:"workspace"|"restricted";readers:string[];editors:string[];access_version:number;can_read:boolean;can_write:boolean;can_manage_sharing:boolean};
export function useDesignerAccess(base:string){
  const [snapshot,setSnapshot]=useState<{base:string;value:DesignerAccess}|null>(null),[checking,setChecking]=useState(true),[unavailable,setUnavailable]=useState(false),[error,setError]=useState(""),[epoch,setEpoch]=useState(0);
  const generation=useRef(0);
  const access=snapshot?.base===base ? snapshot.value:null;
  const reload=useCallback(()=>{generation.current++;setChecking(true);setUnavailable(false);setEpoch((n)=>n+1);},[]);
  const denied=useCallback(()=>{generation.current++;setSnapshot(null);setChecking(false);setUnavailable(true);setError("");},[]);
  useEffect(()=>{
    const controller=new AbortController(),token=++generation.current;setChecking(true);setUnavailable(false);setError("");
    api<DesignerAccess>(`${base}/access`,{signal:controller.signal}).then((value)=>{
      if(controller.signal.aborted || token!==generation.current)return;
      if(!value.can_read){setSnapshot(null);setUnavailable(true);}else setSnapshot({base,value});
    }).catch((e)=>{if(!controller.signal.aborted && token===generation.current){setSnapshot(null);if(e instanceof Error && /^(403|404):/.test(e.message))setUnavailable(true);else setError(apiErrorMessage(e,"Could not check document access."));}}).finally(()=>{if(!controller.signal.aborted && token===generation.current)setChecking(false);});
    return()=>controller.abort();
  },[base,epoch]);
  return {access,checking,unavailable,error,reload,denied,canRead:!!access?.can_read && !checking && !unavailable};
}
