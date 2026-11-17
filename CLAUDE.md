# Shared UI workflow

Before changing console or app UI, read [packages/ui/README.md](packages/ui/README.md).
It is the canonical design-system contract for humans, Claude, Codex, and other
coding agents. Reuse `@ap/ui`, register meaningful Storybook states, run the
required checks, and keep the adoption baseline shrink-only. Review intentional
screenshot changes before updating baselines. Verify locally before committing
or pushing; stage only files belonging to the task.
