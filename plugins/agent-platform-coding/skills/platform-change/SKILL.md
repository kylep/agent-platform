---
name: platform-change
description: Implement an Agent Platform code or configuration change with focused evidence, safe migration, and a reviewable deployment path.
---

# Change the platform

Start from the observable behavior and the component that owns it. Preserve existing data and readers during schema or route changes; prefer an additive migration and a measured cutover. Keep policy in the server boundary that enforces it, not only in a prompt or form.

Run focused tests that can catch the failure and verify the built web bundle when the UI changes. For a live change, follow `docs/deployment.md`, verify the new pod and a real user path, and record any result that differs from a local test. Check quota before expensive model-driven tests.

For a Live App action, a published page is only the presentation layer: keep the operation contract, viewer grant, fixed target, dispatch-time access check, call budget, and durable outcome receipt together at the server boundary. An unreviewed Tool branch does not become callable merely because it appears in the operation catalog.

Before committing, inspect the staged diff and use the repository's secret checks. Stage only intended files. Describe the final behavior and the evidence that proves it. Credentials belong in the platform's secret store or a local ignored file, never in Git.
