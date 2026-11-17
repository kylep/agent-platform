import {Link} from "react-router-dom";
import type {UiCommentNotification} from "../../lib/uiDesigner";
export default function CommentNotifications({notifications}:{notifications?:UiCommentNotification[]}){
  if(!notifications?.length)return null;
  return <ul aria-label="Agent notification status">{notifications.map((target,index)=><li key={`${target.agent}:${index}`}><span>@{target.agent} · </span>{target.status==="queued" ? "Queued":target.status==="run_created" ? target.run_id && /^[a-f0-9]{32}$/.test(target.run_id) ? <Link to={`/runs/${target.run_id}`} onClick={event=>event.stopPropagation()}>Run created</Link>:"Run created":"Not notified"}{target.status==="skipped" && target.reason ? `: ${target.reason}`:""}</li>)}</ul>;
}
