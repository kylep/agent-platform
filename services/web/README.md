# Agent Platform — web

React + TypeScript SPA (Vite, react-router-dom). Talks to the backend API
under `/api/*`; in production this is proxied by nginx (see `nginx.conf`)
to the `agent-platform-api` service.

## Develop

```bash
npm install
npm run dev
```

## Build

```bash
npm run build
```

## Docker

`Dockerfile` builds the static bundle and serves it via nginx, proxying
`/api/` to the backend.
