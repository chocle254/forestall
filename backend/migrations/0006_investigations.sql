-- The buyer's shipping address, as PayPal holds it.
alter table orders add column if not exists ship_to jsonb;

-- AI job 2 of 4: the agent investigates a flagged order with tools before proposing a fix.
create table if not exists investigations (
  id          uuid primary key default gen_random_uuid(),
  order_id    uuid not null references orders(id) on delete cascade,
  flag_id     uuid references flags(id) on delete set null,
  findings    jsonb not null,
  tool_calls  jsonb not null default '[]',   -- the agent's trace: which tools, with what, what came back
  model       text not null,
  created_at  timestamptz not null default now()
);
create index if not exists investigations_order_idx on investigations (order_id, created_at desc);

alter table actions add column if not exists investigation_id uuid references investigations(id) on delete set null;
