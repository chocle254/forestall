# Forestall

An AI agent that stops "item not received" (INR) disputes before buyers file them. It watches PayPal orders after capture, flags shipments at risk, drafts a fix, and runs that fix through PayPal once the seller approves. If a dispute opens anyway, it drafts the evidence.

Built for the Devpost PayPal AI Hackathon.

## Layout

| Path | What | Hosted on |
|---|---|---|
| `backend/` | FastAPI API: PayPal webhooks, AI calls, action execution | Railway |
| `frontend/` | React (Vite) seller dashboard | Vercel |
| `supabase/migrations/` | Postgres schema | Supabase |

## Deploy

1. **Supabase:** create a project and run `supabase/migrations/0001_init.sql` in the SQL editor.
2. **Railway:** new service from this repo, root directory `backend`. Set the variables from `backend/.env.example`.
3. **Vercel:** new project from this repo, root directory `frontend`. Set `VITE_API_URL` (the Railway URL), `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`.
4. Put the Vercel URL in Railway's `CORS_ORIGINS`.

Check `GET /health` on the backend: it reports whether Supabase and PayPal are wired up.

## License

MIT
