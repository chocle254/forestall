-- New risk signal: the carrier itself reports a problem (delivery failure, return to sender, lost, damaged).
alter table flags drop constraint if exists flags_signal_check;
alter table flags add constraint flags_signal_check
  check (signal in ('no_tracking', 'stalled', 'buyer_concern', 'delivered_not_received', 'carrier_exception'));
