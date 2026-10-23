-- When a replacement ships, the watch follows the new parcel only.
alter table orders add column if not exists replacement_shipped_at timestamptz;
