import ChannelView from "./ChannelView";
import { useChannel } from "./useChannel";

// One room, live, standing on its own: subscribe to the channel and draw it.
// This is the shape AgentDetail's Conversations tab wants — a channel id and
// nothing else. Relay itself holds the subscription a level up instead, so its
// thread pane can share one stream with the room beside it.

export default function MessagePane({ channelId, onThread }: {
  channelId: string;
  onThread?: (id: string) => void;
}) {
  const room = useChannel(channelId);
  return <ChannelView room={room} onThread={onThread} />;
}
