-- Test mode only: EasyPost's test trackers carry fixed historical timestamps. We shift them
-- (keeping order and spacing) so the history starts just after payment. Seconds to add.
alter table orders add column if not exists carrier_time_offset_s bigint not null default 0;
