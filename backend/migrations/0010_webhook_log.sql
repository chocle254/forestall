-- Every inbound webhook (EasyPost now, PayPal later): proof of delivery and a debugging aid.
create table if not exists webhook_log (
  id           uuid primary key default gen_random_uuid(),
  source       text not null,
  event        text,
  valid        boolean not null,
  reference    text,
  received_at  timestamptz not null default now()
);
create index if not exists webhook_log_recent_idx on webhook_log (source, received_at desc);
