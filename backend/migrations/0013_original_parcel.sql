-- After a replacement ships, the first (failed) parcel is still followed in the background.
alter table orders add column if not exists original_tracking_number text;
alter table orders add column if not exists original_easypost_tracker_id text;
alter table orders add column if not exists original_parcel_status text;
