-- One row that records the last sweep, so the public tick endpoint can run
-- at most once per interval no matter how often it is called.
create table if not exists sweep_state (
  id           int primary key default 1 check (id = 1),
  last_run_at  timestamptz not null default 'epoch'
);
insert into sweep_state (id) values (1) on conflict do nothing;
