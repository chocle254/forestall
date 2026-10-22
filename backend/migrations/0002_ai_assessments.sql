-- Slice 5: AI risk assessments.

-- Tracking the seller has (from the label) vs. tracking actually attached to the
-- PayPal transaction. INR disputes turn on the second one.
alter table orders add column if not exists tracking_attached_at timestamptz;

-- Demo time travel: the simulator ages an order by this many hours so a
-- "no tracking after 48h" scenario can be shown without waiting two days.
alter table orders add column if not exists demo_shift_hours integer not null default 0;

-- Every AI call is recorded: what it saw (hash of the input), what it said, which model.
-- Doubles as a cache (same input -> reuse the answer) and as an audit trail.
create table if not exists assessments (
  id          uuid primary key default gen_random_uuid(),
  order_id    uuid not null references orders(id) on delete cascade,
  input_hash  text not null,
  input       jsonb not null,
  result      jsonb not null,
  model       text not null,
  created_at  timestamptz not null default now()
);
create index if not exists assessments_order_idx on assessments (order_id, created_at desc);
create index if not exists assessments_hash_idx on assessments (order_id, input_hash);

alter table flags add column if not exists assessment_id uuid references assessments(id) on delete set null;
alter table flags add column if not exists updated_at timestamptz not null default now();

-- At most one open flag per order; a new assessment updates it.
create unique index if not exists flags_one_open_per_order on flags (order_id) where not resolved;
