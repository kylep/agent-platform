# October 5 platform health remediation

All checked operational systems passed their final relevant checks. Corrected
live UI follow-up `453f015b2d7d4ccab14e5aa64473b9c6` completed successfully and
its TCMS receipt was independently verified through the published runs view.
CI `37356693937`, `37360039815` and `37360422847` completed successfully.
The user's stop condition was all identified systems healthy or twenty percent
quota remaining. At 19:08 UTC, Codex weekly quota had thirty-six percent left.

## Repairs and evidence

| Area | Repair | Verified result |
| --- | --- | --- |
| Pai scheduled delivery | Replace retired App API readers with published state views; grant only accepted archive reads | News and Markets reached Discord provider receipts; duplicate runs sent nothing |
| Market attribution | Exclude live daily bars until exchange close plus fifteen minutes | Run `fc8ca60d80d14c4ebc3c29db0fc880f4` returned Friday October 2 for QQQ, SPY and XIU.TO on Monday October 5 |
| Market archive | Label the existing early October 5 brief as an intraday snapshot | Run `c65003fb79de47c4862e715f99bc16e6` saved the label while preserving the index and mover values |
| Olu News reader | Replace legacy query with `app_data`; add approved read-only News ACL | Run `40e0a21d12044a618e92a021db183fb6` read actual archive records; News approved version 2 |
| Health diagnostics | Include bounded recent denial samples with caller, run, operation and decision | Historical denied readers can be distinguished from rejected unauthenticated traffic; no broad grants added |
| Worker availability | Add Claude Haiku fallback to health-monitor and run-summarizer | Forced unavailable-primary run `62740e9adb6b464c83bebd24fc5094c0` succeeded on fallback with no permission denials |
| App previews | Prefer a home/overview page and wait for required page parameters before querying | All sixty live browser entries passed: fifteen routes, desktop/phone, light/dark; no console errors, failed requests, missing headings or overflow |
| QA execution | Bound parallel workers to cgroup CPU quota, cap at four, and flush verifier logs; install missing xdist in live dev image | Fresh QA pod ran `pytest -n 3` with unchanged coverage and JUnit reporting |
| Assigned skills | Include launch-verified installed skill bytes in Claude main-session instructions | Coder run `6b5252a5efe24bac8a80d607150885ad` identified both skills without file/wiki lookup and passed all three fallback tests |
| Change summarizer | Exercise current runtime against a harmless diff | Run `0aeefcb7675a471e8039db5372530c44` succeeded |
| Phone-layout test | Select the exact Report tab instead of also matching Expand Reporting in the rail | Ten focused repeats and all 368 local UI tests passed; CI passed |
| TCMS workflow | Name the accepted pytest JUnit, Playwright JSON and coverage files explicitly | Forty-seven parser/state tests passed; duplicate Playwright JUnit remains rejected |

Delivery schedules, provider message IDs and receipt IDs are recorded in
[Pai scheduled delivery](pai-scheduled-delivery.md). News remains daily at
10:00 America/Toronto and Markets weekdays at 10:30. Collection remains the
workers' responsibility; Pai owns communicating accepted collected data.

Code changes: `49caf1e`, `0b5d5b1`, `cec9312`, `f28d9b6`, `16d1748`,
`53e6507`, `c8aa34d`, `36fcc97`, `214f821`, `60a3744`. The initial frontmatter preload attempt did
not work in Claude main-session mode and broke two existing backend seam
checks. It was removed; those checks remain intact. Instructions are loaded
from checksum-verified assignments, not unverified definition names.

## Validation

- Full backend after the final correction: 3,041 passed, three skipped,
  coverage and JUnit recorded locally.
- Runner plus launcher/API seam, fallback and dev-image contracts: 152 passed.
- Live nightly `a04e42d7f4de4841b517a3c3eda4763d`: backend 3,039 passed,
  five skipped; broker 427, facade 35, runner 138, executor 83, Discord
  connector seven passed. SDK, App backend/frontend, Tool, build and lint
  checks passed. The eight executor chart-test skips require Helm; the
  chart and Claude proxy suites also require unavailable Helm. Those areas
  passed their CI checks. The two additional backend skips require `age`;
  their encryption round-trip/tamper tests passed locally. Three Postgres
  tests require an explicitly configured scratch database and remain skipped.
- That nightly recorded its genuine single browser failure as TCMS run
  `6bef4db62c2f4455ba20c8fd8d77c283` (3,406 pass, five skip, one fail).
  The ambiguous Report locator was fixed afterwards. This record remains
  unchanged. Corrected UI-only record `d39677e5824e4c9b9f65c8ca650ab297`
  on `214f821` contains all 368 passes, zero skips, zero failures,
  `verify_ok=true` and `ingest_state=complete`. Both rows were independently
  read back from the published TCMS runs view.
  Other suites' actual results are in the nightly verify.json and logs;
  TCMS's ingested report files cover backend and browser results.
- Full web suite: 368 passed; lint, production build, token checks and
  Storybook build completed successfully.
- Tool suites and executor tests passed; completed-session selector has
  twenty-one focused tests, including live-bar and early-close boundaries.
- All fourteen deployments and both stateful workloads ready; no new
  unexpected restarts. Kafka reachable, expected topics present, zero lag
  and zero dead-letter backlog.
- October 5 Postgres and cloud backup jobs completed. This review did not
  perform a production restore drill.

Cancelled recovery runs remain cancelled. They are not counted as successful
verification. A successful harness exit is insufficient: actual tool results,
test reports and delivery receipts are the acceptance evidence.

## Deployment details

Backend and Web images were rebuilt and rolled out. Both runner images now
carry current runner code. The development runner was repaired by layering
`pytest-xdist==3.8.0` and the tested runner modules over its existing image;
the full source Dockerfile already installs the backend dev extra containing
xdist. No grant or secret authority was added by the skill-context change.

At 18:31 UTC, standard runner manifest was
`sha256:dc3869a1355c26f15b095eceebe2d5bcb310d21ac608b2360ee6d7ebc9bf0a0a`,
and development runner manifest was
`sha256:08dd4985440dd438b49476da163fe7b1b3b7075e43bdd857968cd54a3ecf4a63`.

OPS-28 records correctly rejected unauthenticated `agents_grant` calls from
14:04 UTC. The stored audit lacks ingress/source attribution, so this review
cannot identify their origin. It does not indicate a broken agent workflow or
justify weakening authentication. OPS-35 is now closed following QA recovery.

TCMS's curated case inventory links 48 of the 368 UI results; the other 320
results are still executed and recorded but lack curated case entries. Expanding
that inventory is a coverage-management improvement, not a failed test or
blocked execution. No tests were pruned based on the two-run performance sample.
QA's persistent prompt now names accepted report files, requires a production
build before standalone browser tests, and bounds progress checks to sixty
seconds. Its existing grants, secrets and publish restrictions were preserved.
