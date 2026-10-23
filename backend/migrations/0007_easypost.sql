-- Where an order's carrier data comes from: the scripted simulator, EasyPost, or nowhere yet.
alter table orders add column if not exists carrier_source text not null default 'simulator';
alter table orders add column if not exists easypost_tracker_id text;
alter table orders add column if not exists tracking_public_url text;
create index if not exists orders_easypost_tracker_idx on orders (easypost_tracker_id);

alter table shipment_events add column if not exists message text;
-- Carrier updates can arrive twice (API response + webhook): keep one copy.
create unique index if not exists shipment_events_dedupe on shipment_events (order_id, status, occurred_at);
