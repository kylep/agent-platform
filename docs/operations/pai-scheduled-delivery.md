# Pai owns scheduled Discord delivery

News and Stockmarket workers gather and store data. Pai owns communicating
that accepted data to Kyle using her `discord-default` account in Discord
channel `1482824836190306536` (Kyle's Bot Space / news).

The live schedules are Postgres Jobs, not agent entrypoint crons:

| Job | ID | America/Toronto schedule | Reader |
| --- | --- | --- | --- |
| pai-daily-news-digest | 2c393c3a8d024011876c30fb7045f398 | Daily at 10:00 | app_data: news/items_by_day |
| pai-market-review | 9ae0a2a8e44649c994890f8dd51ac846 | Weekdays at 10:30 | app_data: stockmarket/briefs_recent |

The reviewed prompt snapshots are [News](pai-news-delivery.txt) and
[Markets](pai-market-delivery.txt). The running Job rows are authoritative;
these files do not automatically update them. Pai holds `app_data`; the News
items/topics and Stockmarket briefs definitions permit her to read. She is not
a writer or owner of those records. Delivery uses her existing Discord account.

## October 5 repair and evidence

The state-App migration removed the old App APIs but left both delivery Jobs
using `query_app`. They returned 404 and posted nothing despite their run state
being `succeeded`. The repair changed their readers to published state views,
added Pai to the briefs read ACL as Stockmarket approved version 2, and kept the
existing schedules. A temporary `apps` grant to the Stockmarket owner was removed
after it proposed that access change. Pai's nonexistent retired `ttrpg` grant
was removed because whole-definition validation refused it.

- News verification run: `e63734d51e06482082a8dd58287922f6`.
  Discord request `cb842d9ae1194a1c8b1a158316e6e7ce` reached `accepted` with
  provider message `1556714156927746089`.
- Markets verification run: `078bf86783e74dd48187b0c88679adfc`.
  Discord request `257b38284b33448695ace4301a63f5b9` reached `accepted` with
  provider message `1556714439414255767`.
- Both delivered receipt IDs were saved in Pai's delivery memories.
- Local backend validation: 3,035 passed, 3 skipped; the focused access test
  proves Pai can read briefs, cannot mutate them, and cannot read the other
  Stockmarket collections.

The market collector currently selects the newest daily bar, including live
intraday prices. Until its completed-session selector is repaired, Pai labels
briefs collected before their market day's close as intraday snapshots and gives
the collection time. Repairing collection is separate from delivery; never
silently present an intraday brief as a completed-session close.

## Migration acceptance

Before retiring an App API, inventory every scheduled Job, agent reader,
published view, permission, bookmark and delivery destination that depends on
it. Migrate those consumers with the App. Verify a real collector run, a reader
run and a scheduled delivery run through its Discord receipt and provider
message ID. Run the delivery again and require no additional send. A healthy
App page and a successful agent exit are insufficient evidence of delivery.
