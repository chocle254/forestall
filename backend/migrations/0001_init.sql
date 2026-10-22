-- Forestall schema: one seller, the INR prevention loop. Runs on Neon Postgres.

create table if not exists orders (
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

create table if not exists shipment_events (
  id           uuid primary key default gen_random_uuid(),
  order_id     uuid not null references orders(id) on delete cascade,
  status       text not null,          -- label_created | in_transit | stalled | out_for_delivery | delivered
  location     text,
  occurred_at  timestamptz not null default now(),
  source       text not null default 'simulator'
);
create index if not exists shipment_events_order_idx on shipment_events (order_id, occurred_at);

create table if not exists buyer_messages (
  id           uuid primary key default gen_random_uuid(),
  order_id     uuid not null references orders(id) on delete cascade,
  body         text not null,
  received_at  timestamptz not null default now()
);
create index if not exists buyer_messages_order_idx on buyer_messages (order_id, received_at);

create table if not exists flags (
  id          uuid primary key default gen_random_uuid(),
  order_id    uuid not null references orders(id) on delete cascade,
  risk_level  text not null check (risk_level in ('low','medium','high')),
  reason      text not null,
  signal      text not null check (signal in ('no_tracking','stalled','buyer_concern','delivered_not_received')),
  resolved    boolean not null default false,
  created_at  timestamptz not null default now()
);
create index if not exists flags_order_idx on flags (order_id);

create table if not exists actions (
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
create index if not exists actions_flag_idx on actions (flag_id);

create table if not exists disputes (
  id                 uuid primary key default gen_random_uuid(),
  paypal_dispute_id  text unique not null,
  order_id           uuid references orders(id) on delete set null,
  reason             text,
  status             text,
  evidence_draft     text,
  submitted_at       timestamptz,
  created_at         timestamptz not null default now()
);
