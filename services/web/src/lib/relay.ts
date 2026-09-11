import type { RelayChannel } from "../api";

// Participant strings (`agent:news`, `user:kyle`, `discord:1529…`) are the
// only identity Relay has — the API derives them from the caller's token, so
// nothing here has to second-guess a self-reported name. These are the small
// readings of that string the UI does over and over.

export const AGENT = "agent:";
export const USER = "user:";

export function isAgent(participant: string): boolean {
  return participant.startsWith(AGENT);
}

export function agentName(participant: string): string | null {
  return isAgent(participant) ? participant.slice(AGENT.length) : null;
}

/** The namespace part — "agent", "user", "discord" — or "" if malformed. */
export function namespaceOf(participant: string): string {
  const i = participant.indexOf(":");
  return i < 0 ? "" : participant.slice(0, i);
}

/** Whether this participant is the person reading the page.
 *
 * `me` comes from /api/whoami. When that call failed we still know something:
 * the `user:` side of a room is a human, and the human in front of this
 * browser is the one looking at it — a far better guess than rendering your
 * own name as the other half of your own dm. */
export function isCaller(participant: string, me: string | null): boolean {
  return me ? participant === me : participant.startsWith(USER);
}

/** What to call a participant in a message header or a rail row. A human is
 * "you" when it is the signed-in principal; everyone else is their own id,
 * because a room where two names could mean the same person is unreadable. */
export function participantLabel(participant: string, me: string | null): string {
  if (isCaller(participant, me)) return "you";
  const i = participant.indexOf(":");
  return i < 0 ? participant : participant.slice(i + 1);
}

/** A channel's display name: `#slug` for channels, the other person for a DM,
 * and for a group — which the API gives no title of its own (T9) — who is in
 * it, since that is the only thing that distinguishes one group from another. */
export function channelLabel(channel: RelayChannel, me: string | null): string {
  if (channel.kind === "channel") return `#${channel.name ?? "channel"}`;
  if (channel.kind === "group") {
    const who = channel.participants.map((p) => participantLabel(p, me));
    return who.length
      ? `${who.length} members: ${who.join(", ")}`
      : "group";
  }
  const other = otherParticipant(channel, me);
  return other ? participantLabel(other, me) : "direct message";
}

/** The participant a DM is *with* — the one that isn't the viewer. Falls back
 * to the row's legacy single-agent column for a DM whose membership rows the
 * caller cannot see. */
export function otherParticipant(channel: RelayChannel, me: string | null): string | null {
  const others = channel.participants.filter((p) => !isCaller(p, me));
  if (others.length) return others[0];
  return channel.agent ? AGENT + channel.agent : null;
}

/** The agents a mention can actually reach here. An open channel holds every
 * enabled agent by definition; a closed room, a group or a dm holds only its
 * membership rows — and the backend ignores a mention of anyone else, so
 * offering the name would be offering a summons that never happens. */
export function mentionableIn(channel: RelayChannel | null, enabled: string[]): string[] {
  if (!channel) return [];
  if (channel.kind === "channel" && channel.open) return enabled;
  const members = new Set(channel.participants
    .map((p) => agentName(p))
    .filter((n): n is string => n !== null));
  return enabled.filter((name) => members.has(name));
}
