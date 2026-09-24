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
  (app)/                          post-login shell (sidebar, org switch, theme toggle)
    assistants/                   list; [id]/build (canvas, panels, sources,
                                  databases, history) and [id]/chat
  layout.tsx, globals.css         root layout + theme tokens (light/dark)
components/ui/                     shadcn-style primitives, hand-written (ADR 0001):
                                  button, input, select, tabs (ARIA tablist),
                                  dialog (native <dialog>; useConfirm/usePrompt),
                                  toast (live region), switch, badge, card
components/canvas/                 React Flow node-graph builder
components/chat/                   thread, tool cards (with subagent nesting), sources
lib/api.ts                        typed backend client; refreshes the session on 401
lib/session.ts                    single-flight, cross-tab-safe token refresh
```

## Scripts

`npm run -w web <script>`: `dev`, `build`, `start`, `lint`, `typecheck`, `test` (vitest).
