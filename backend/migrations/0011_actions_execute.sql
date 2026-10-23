-- Slice 7: approving and executing fixes.
alter table actions drop constraint if exists actions_type_check;
alter table actions add constraint actions_type_check
  check (type in ('attach_tracking', 'message_buyer', 'partial_refund', 'full_refund', 'ship_replacement'));
alter table actions add column if not exists executed_at timestamptz;
alter table actions add column if not exists edited boolean not null default false;
-- A follow-up (e.g. the refund the buyer chose) points at the action that offered it.
alter table actions add column if not exists parent_action_id uuid references actions(id) on delete set null;

-- How an order's risk ended: tracking_fixed, refunded, replaced, buyer_reassured, ...
alter table orders add column if not exists resolution text;
alter table orders add column if not exists refunded_amount numeric(12,2) not null default 0;
