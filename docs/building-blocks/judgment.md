# Judgment

**What:** Kai's private record of what it believes about Kyle, what it
predicted he would choose, and what he actually said
(`docs/design/38-judgment-app.md`, from ticket ENG-8). It exists so Kai can
find where its model of Kyle is wrong, rather than only growing more
confident. Three kinds of record:

- A **belief** is a claim about Kyle with its scope, evidence, provenance and
  confidence. Beliefs are versioned: every change is a new version, and the
  trail stays visible.
- A **prediction** is what Kai expects Kyle to choose in a concrete scenario,
  written down before he chooses. It never changes after it is written.
- **Feedback** is what Kyle chose to share: his words, kept apart from Kai's
  interpretation of them, and an outcome (`supported`, `contradicted`,
  `mixed`, `context_changed` or `unresolved`).

**Lives in:** Postgres, in the `app_judgment` schema owned by the
[app](apps.md) at `apps/judgment/`. Kai reads and writes through the
`judgment` [tool](tools.md); Kyle uses the page at `/apps/judgment/`.

## Who can see it

Only Kai and Kyle.

- **Kai** uses the `judgment` tool. The tool refuses every other caller, even
  one that has been granted it, because it checks the run's own identity
  (`TOOL_CALLER_AGENT` and `TOOL_RUN_ID`), not just the grant.
- **Kyle** uses the page. Its API answers only an admin login session whose
  principal is in `JUDGMENT_OWNER_PRINCIPALS` (default `admin`). Reader
  logins, the QA login, admin API keys and every agent's `query_app` get a
  403.
- Nothing is copied into memory, the wiki or search indexes. Kai's prompt
  tells it to keep judgment records in the tool.

## Provenance

Every belief version says where the claim came from:

| provenance | meaning | who can write it |
|---|---|---|
| `kyle_confirmed` | Kyle confirmed this exact claim on his page | Kyle only |
| `kyle_relayed` | Kai reports Kyle said it; carries a message link | Kai |
| `observed` | a pattern in Kyle's own messages | Kai |
| `inference` | Kai's reasoning | Kai |
| `imported` | text from another assistant or an export | Kai |

`kyle_relayed` records must carry a `source_ref`:
`relay:<channel id>/<message id>` or `discord:<channel id>/<message id>`.
The page links a Relay ref to its room and thread, so a claim of "Kyle
said" can be checked; a Discord ref shows its ids. Only Kyle's confirmation produces
`kyle_confirmed`. Kai can't reject or supersede a belief Kyle confirmed, and
when Kai looks a belief up it always sees the latest confirmed version beside
the current one.

## Honest predictions

Kai states `outcome_known` on every prediction. `false` makes it
**prospective**, and only prospective predictions count in the review. The
server stamps the time, so a prediction can't be backdated. Whether Kai
really didn't know the answer yet is its own attestation, so the review flags
two patterns that undercut it: feedback dated before the prediction, and
feedback arriving within ten minutes of it.

A prediction is **resolved** once it has feedback with an outcome other than
`unresolved`. Disagreeing feedback makes it `mixed`. Silence never resolves
anything.

## The tool

| action | what it does |
|---|---|
| `belief` | Create a belief, or (with `id` and `expected_version`) add a version or change its status, with a reason. |
| `predict` | Record a prediction with `outcome_known` and the beliefs it relies on. |
| `feedback` | Record what Kyle shared about a prediction or a belief version, with his words and the message link. |
| `recall` | Look beliefs up by text, or any record by id, with corrections and conflicting feedback attached. |
| `pending` | Unresolved prospective predictions, and contradicting feedback no belief has absorbed yet. |

Writes return ids only. Reads come back marked as untrusted data, so stored
text can't act as an instruction. There is no delete action; Kai can only
supersede or reject.

## The page

`/apps/judgment/` has four tabs:

- **Beliefs:** confirm a claim, correct it in your own words, reject it, or
  delete it, with the full version trail.
- **Predictions:** pending and resolved, with flags; add your own feedback.
- **Feedback:** what Kai relayed, waiting for you to confirm or correct.
- **Review:** counts first (resolved by outcome, confirmed versus relayed,
  pending, retrospective, flagged), then each resolved prediction. There is
  deliberately no accuracy percentage: sparse, self-selected feedback can't
  support one.

## Deleting

Deleting on the page removes the records from the App's tables for good.
Deleting feedback also deletes every belief version that cites it, since a
derived claim can repeat its words; the page lists what will go first.

Two places deletion can't reach:
- **Kai's run transcripts** hold its tool calls and their output. Only admins
  can read them, and they expire with Kai's transcript retention.
- **Backups.** A deleted record stays in older encrypted backups until they
  rotate out, and restoring one brings it back
  ([backups.md](backups.md#restore-onto-a-fresh-cluster)).
