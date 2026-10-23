-- Slice 9: disputes, evidence drafting and submission; plus small key/value settings.
create table if not exists app_settings (
  key         text primary key,
  value       text not null,
  updated_at  timestamptz not null default now()
);

alter table disputes add column if not exists stage text;
alter table disputes add column if not exists amount numeric(12,2);
alter table disputes add column if not exists currency text;
alter table disputes add column if not exists messages jsonb not null default '[]';
alter table disputes add column if not exists allowed_actions jsonb not null default '[]';
alter table disputes add column if not exists due_at timestamptz;
alter table disputes add column if not exists outcome text;
alter table disputes add column if not exists raw jsonb;
alter table disputes add column if not exists evidence jsonb;
alter table disputes add column if not exists evidence_status text not null default 'none';
alter table disputes add column if not exists paypal_response jsonb;
alter table disputes add column if not exists updated_at timestamptz not null default now();
