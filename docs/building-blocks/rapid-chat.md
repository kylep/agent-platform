# Rapid-chat

A rapid-chat is a **timed sprint** in which chosen persona agents take as many turns as they can on one problem, and the platform writes one output artifact from their work. Design: [45](../design/45-rapid-chat.md).

Launch one from **Rapid-chats** in the web UI or with the `create_rapid_chat` MCP tool. You choose the title, the goal, the participants, the output (today a wiki page, replaced or appended), the areas the problem must cover, and the end conditions: duration, maximum turns, a per-turn timeout and quota ceilings (Claude 5-hour and 7-day, Codex 7-day). Only an admin can launch, and one rapid-chat runs at a time.

**How turns work.** A controller in the dispatcher hands each free participant one job: an area and a role. Every turn is an ordinary Run (`trigger=rapid_chat`), so it appears on the Runs page with its transcript and can be killed. Participants work in parallel; an area has at most one turn in flight. Each turn gets a short prompt: the goal, the coverage map, its assigned area's current proposal and critique, and the last few summaries. It does not get a transcript. It answers in fixed sections (`Summary`, `Proposal` or `Critique`, `Alternatives`, `Open questions`); a reply missing a required section is recorded as malformed and not merged.

**Avoiding premature convergence.** The controller, not the prompt, decides what each turn works on:
- *Coverage map:* no area gets a second pass until every area has had its first.
- *Diverge, then converge:* an area's first author must offer at least two options. The critic picks or merges one, and the rest are kept as alternatives.
- *Critique needs an alternative:* every turn must name a materially different approach.
- *Critic is never the author.*
- *Integrator:* once every area is reviewed, one turn checks the areas against each other for contradictions and reopens the areas that conflict.
- *Devil's advocate:* after that, turns argue that a finished area is wrong or boring, and a reviser has to answer.
- *Near-duplicate penalty:* a turn that restates the previous text counts double against the area's cap.

**Ending.** Before every new turn, the controller checks elapsed time, the turn count, a stop request and the quota ceilings, using the latest quota readings (it refreshes Codex itself). Any one of them ends the chat. Turns already in flight finish; a turn past its timeout is cancelled. A checkpoint of the page is written at the moment the chat ends, then a summary is built: turns per participant and outcome, per-area status, quota at start and end, and, for chats that end on time or turn count, one short summarizer turn.

**What turns may do.** A turn run can read the wiki, search memory and read quota, and nothing else: it cannot post in Relay or Discord, write the wiki, start runs or Tasks, or edit an agent. The fence stays on even if the turn outlives its chat. The controller is the only writer of the page, as `rapid-chat:<id>`, and in replace mode it never overwrites a concurrent edit; it switches to appending a dated draft instead. Another participant's text reaches a turn as tagged data, never as instructions.

**Auditing.** Every turn is a row in `rapid_chat_turns` with the exact prompt the agent received, its raw reply, the parsed sections, the outcome and a reason. `get_rapid_chat` returns the chat with its areas, last quota reading, output link and summary; `list_rapid_chat_turns` returns the audit rows; `stop_rapid_chat` ends a chat early.
