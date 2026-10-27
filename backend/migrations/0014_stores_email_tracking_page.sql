-- Store connections (Shopify / WooCommerce), buyer emails, and the buyer's tracking page.

-- Unguessable code for the buyer's public tracking page (/track/<code>).
alter table orders add column if not exists track_token text not null
  default replace(gen_random_uuid()::text, '-', '');
create unique index if not exists orders_track_token_idx on orders (track_token);

-- Which store order this PayPal order belongs to (filled in by store webhooks).
alter table orders add column if not exists store_platform text;
alter table orders add column if not exists store_order_id text;
create unique index if not exists orders_store_ref_idx on orders (store_platform, store_order_id)
  where store_order_id is not null;
