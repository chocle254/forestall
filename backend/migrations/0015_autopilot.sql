-- Autopilot: who ran a fix ('seller' or 'autopilot').
alter table actions add column if not exists approved_by text;
