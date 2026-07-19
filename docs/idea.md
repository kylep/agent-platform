# High level idea

I want agent-platform to be my full end-to-end agent wrangler. It will live on my home
nuc as is described in https://github.com/kylep/multi/blob/main/apps/blog/blog/markdown/wiki/devops/pai-nuc-k3s.md.
It should be generic though enough that i can run it on just about any k8s env, within reason.


I want a web UI to control my agents, and all the infra associated with it, to be stood up as code by this project.
Agents are always k8s pods that run 'claude --agent <name> -p <prompt>'

Key features for the agent platform:

- Agents are defined as code in this repo
- New agents are added to the platform by coding agents, which will be operated both from within the platform and outside it
- The platform offers a convenient UI to review all the agents, CRUD them (edit the md, for example)
- It provides cron job / scheduler support and visibility. You can schedule agents, review schedules, review runs.
- It wrangles the logs and metrics of agents. It stores their output, tool call details, metrics, etc in some db(s).
  - It has agent output logs and metrics page(s).
- It handles secrets. Secrets can be bound to skills. Agent only get the secrets they need. It encrypts them appropriately.
- It handles invoking agents through API calls: You can create listeners and it will invoke the agent.\
- It handles invoking agents through Kafka messages
  - Webhook handling goes through Kafka so things get queued up
    - Kafka infra has sufficient monitoring and metrics / reporting to be sure its healthy
- Skills are first-class ui components, maybe handled as plugins? They're also stored as code and pulled in by agents.\
  - some basic skills should be shipped in this out the gate like interacting with Git and Discord
- Agents should have access to per-agent-namespaced memory. No vector store support (expensive, annoying), use postgres or something.
  - Memories should be reviewable
- Overall system health including agent runs, infra, whatever shoudl have a reporting page
- Web UI's APIs should have openapi spec, sdk, claude skills for easy consumption and meta-operation
- auth and rbac

The infra needed should spin up with the project.
 - Postgres is my preferred relational db
 - Kafka should be minimal
 - Keep memory requests tight on infra and scale up as needed
 - use other infra as appropriate, but justify it

