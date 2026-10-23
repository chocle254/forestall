-- Slice 6: AI-recommended fixes.
alter table actions drop constraint if exists actions_status_check;
alter table actions add constraint actions_status_check
  check (status in ('proposed', 'approved', 'executed', 'failed', 'dismissed', 'superseded'));

alter table actions add column if not exists order_id uuid references orders(id) on delete cascade;
alter table actions add column if not exists model text;

-- At most one pending proposal per flag.
create unique index if not exists actions_one_proposed_per_flag on actions (flag_id) where status = 'proposed';
create index if not exists actions_order_idx on actions (order_id, created_at desc);
