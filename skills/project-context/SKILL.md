---
name: project-context
description: Find relevant earlier conversations in a platform Project before continuing work.
---
# Project conversation context

When a run includes a `<work-context>` Project line, use the Relay tool's
`search` action with `project` set to that project's slug and `q` set to a few
specific terms. Search is bounded and returns only messages in rooms you may
read. It does not reveal private rooms merely because they share a project.

Example: `relay(action="search", project="rpg-playtest", q="dice feedback", limit=10)`.

Read the surrounding room or thread with `relay(action="read", channel=<id>)`
when a hit matters. Cite the room and date when using earlier decisions. If the
Relay tool is absent from your grants, work with the current conversation and
say that you could not inspect project history.
