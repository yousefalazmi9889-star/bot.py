# LEON Discord Bot

LEON's Discord bot provides livestream links, role-gated Gemini replies, moderation, persistent support tickets, and staff ratings.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `python-project/src/python_project/` — application package and Discord bot
- `python-project/main.py` — direct-run compatibility entry point
- `python-project/run_production.sh` — Reserved VM supervisor with automatic bot restart
- `python-project/tests/` — automated tests
- `python-project/pyproject.toml` — package metadata and executable entry point
- `python-project/leon_tickets.db` — runtime SQLite database (ignored by git)

## Architecture decisions

- The Python package uses a `src/` layout to keep imports honest during development.
- The bot uses `discord.py` and reads `DISCORD_TOKEN` and `GEMINI_API_KEY` from Replit Secrets.
- Ticket Category, log channel, and support-role settings are stored in SQLite per `guild_id`; runtime ticket routing never uses global server IDs.
- Ticket and rating views use persistent Discord component custom IDs and are restored from SQLite in `setup_hook`.
- The ticket inactivity clock is based only on the owner's last message.
- Ticket management controls require Discord's `Manage Channels` permission; each Guild can optionally save its own support role through `/setup_ticket`.
- Production uses a Reserved VM deployment because the Discord bot must stay online continuously; the production supervisor restarts it after unexpected exits.

## Product

The bot supports LEON's community with livestream commands, role-gated AI mentions, bulk clearing, persistent tickets, transcripts, rating DMs, and staff statistics.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

_Populate as you build — sharp edges, "always run X before Y" rules._

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
