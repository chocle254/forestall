-- Watch every order from "shipped" until it is safely delivered, and log every step.

alter table orders add column if not exists shipped_at timestamptz;
-- not_shipped -> watching -> protected (delivered, no open risk) | closed
alter table orders add column if not exists watch_status text not null default 'not_shipped';
-- When the watcher should next look at this order (a threshold is about to be crossed).
alter table orders add column if not exists next_check_at timestamptz;
-- Delivery story the simulator plays out over (simulated) time, if any.
alter table orders add column if not exists demo_scenario text;

create index if not exists orders_watch_due_idx on orders (next_check_at) where watch_status = 'watching';

-- The audit trail: one row per thing that happened to an order, in order.
create table if not exists order_timeline (
  id           uuid primary key default gen_random_uuid(),
  order_id     uuid not null references orders(id) on delete cascade,
  kind         text not null,      -- paid | shipped | scan | buyer_message | check | flag | flag_cleared | action | paypal | delivered | protected
  title        text not null,
  detail       jsonb not null default '{}',
  occurred_at  timestamptz not null default now(),  -- in the order's (possibly simulated) time
  recorded_at  timestamptz not null default now()   -- wall clock
);
create index if not exists order_timeline_order_idx on order_timeline (order_id, occurred_at, recorded_at);

-- Backfill: existing orders start their timeline at payment.
insert into order_timeline (order_id, kind, title, detail, occurred_at)
select o.id, 'paid', 'Payment captured by PayPal',
       jsonb_build_object('capture_id', o.capture_id, 'amount', o.amount::text, 'currency', o.currency),
       o.captured_at
from orders o
where not exists (select 1 from order_timeline t where t.order_id = o.id and t.kind = 'paid');
