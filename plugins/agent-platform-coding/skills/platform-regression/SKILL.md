---
name: platform-regression
description: Reproduce and verify an Agent Platform bug or change across API, agent-run, and browser boundaries without adding brittle mirror tests.
---

# Verify a platform behavior

Find the smallest real trigger and expected outcome. Check the current deployed version, run state, and relevant events before inferring the cause from a screenshot or a single error. For asynchronous work, distinguish a gateway timeout from a failed underlying run and look for its durable result before retrying.

Write a focused test only when it protects behavior or a policy boundary. Prefer a real authorization failure, replay, retry, or state transition over assertions that copy implementation text. Verify a changed UI in a browser at desktop and narrow widths; inspect the rendered page and network result, not only a build.

Leave test-created external effects identifiable and clean them up when the platform supports it. Report what passed, what was not exercised, and any remaining uncertainty with the version and evidence used.
