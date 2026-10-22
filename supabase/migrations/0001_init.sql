-- Forestall schema: one seller, the INR prevention loop.

create extension if not exists pgcrypto;

create table orders (
  id               uuid primary key default gen_random_uuid(),
  paypal_order_id  text unique not null,
  capture_id       text unique,
  buyer_name       text,
  buyer_email      text,
  amount           numeric(12,2) not null,
  currency         text not null default 'USD',
  captured_at      timestamptz not null default now(),
  tracking_number  text,
  carrier          text,
  created_at       timestamptz not null default now()
);

create table shipment_events (
  id           uuid primary key default gen_random_uuid(),
  order_id     uuid not null references orders(id) on delete cascade,
  status       text not null,          -- label_created | in_transit | stalled | out_for_delivery | delivered
  location     text,
  occurred_at  timestamptz not null default now(),
  source       text not null default 'simulator'
);
create index on shipment_events (order_id, occurred_at);

create table buyer_messages (
  id           uuid primary key default gen_random_uuid(),
  order_id     uuid not null references orders(id) on delete cascade,
  body         text not null,
  received_at  timestamptz not null default now()
);
create index on buyer_messages (order_id, received_at);

create table flags (
  id          uuid primary key default gen_random_uuid(),
  order_id    uuid not null references orders(id) on delete cascade,
  risk_level  text not null check (risk_level in ('low','medium','high')),
  reason      text not null,
  signal      text not null check (signal in ('no_tracking','stalled','buyer_concern','delivered_not_received')),
  resolved    boolean not null default false,
  created_at  timestamptz not null default now()
);
create index on flags (order_id);

create table actions (
  id               uuid primary key default gen_random_uuid(),
  flag_id          uuid not null references flags(id) on delete cascade,
  type             text not null check (type in ('attach_tracking','message_buyer','partial_refund')),
  params           jsonb not null default '{}',
  justification    text not null,
  status           text not null default 'proposed'
                   check (status in ('proposed','approved','executed','failed','dismissed')),
  paypal_response  jsonb,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index on actions (flag_id);

create table disputes (
  id                 uuid primary key default gen_random_uuid(),
  paypal_dispute_id  text unique not null,
  order_id           uuid references orders(id) on delete set null,
  reason             text,
  status             text,
  evidence_draft     text,
  submitted_at       timestamptz,
  created_at         timestamptz not null default now()
);

-- The frontend reads with the anon key; only the backend (service role) writes.
alter table orders          enable row level security;
alter table shipment_events enable row level security;
alter table buyer_messages  enable row level security;
alter table flags           enable row level security;
alter table actions         enable row level security;
alter table disputes        enable row level security;

create policy "public read" on orders          for select using (true);
create policy "public read" on shipment_events for select using (true);
create policy "public read" on buyer_messages  for select using (true);
create policy "public read" on flags           for select using (true);
create policy "public read" on actions         for select using (true);
create policy "public read" on disputes        for select using (true);

alter publication supabase_realtime add table orders, shipment_events, flags, actions, disputes;
