# apps/web — Assistant Studio frontend

Next.js 15 (App Router) · React 19 · TypeScript · Tailwind v4 · React Flow (canvas, Phase 1).

## Dev

```bash
npm install            # from the repo root (npm workspaces)
cp apps/web/.env.local.example apps/web/.env.local
npm run dev -w web      # http://localhost:3000
```

Point `NEXT_PUBLIC_API_URL` at the backend (default `http://localhost:8000`).

## Layout

```
app/
  (auth)/login, (auth)/register   auth screens
  dashboard/                      post-login shell
  layout.tsx, globals.css         root layout + theme tokens (light/dark)
components/ui/                     minimal shadcn-style primitives
components/canvas/                 React Flow node-graph builder (Phase 1)
lib/api.ts                        typed backend client + token store
```

## Scripts

`npm run -w web <script>`: `dev`, `build`, `start`, `lint`, `typecheck`.
