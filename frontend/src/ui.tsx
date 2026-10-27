import { useEffect, useState, type ReactNode } from "react";
import type { Order } from "./api";

// ---------- Icons (inline SVG, inherit currentColor) ----------
const PATHS: Record<string, ReactNode> = {
  home: <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />,
  orders: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18M8 14h4" /></>,
  dispute: <><path d="M12 3 2 20h20z" /><path d="M12 10v4M12 17.5v.01" /></>,
  status: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z" />,
  monitor: <><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></>,
  card: <><rect x="2" y="5" width="20" height="14" rx="2" /><path d="M2 10h20" /></>,
  box: <><path d="M21 8 12 3 3 8v8l9 5 9-5z" /><path d="M3 8l9 5 9-5M12 13v8" /></>,
  truck: <><path d="M1 6h13v10H1zM14 10h4l3 3v3h-7" /><circle cx="5.5" cy="18" r="2" /><circle cx="17.5" cy="18" r="2" /></>,
  pin: <><path d="M12 21s7-6.5 7-12a7 7 0 0 0-14 0c0 5.5 7 12 7 12z" /><circle cx="12" cy="9" r="2.5" /></>,
  chat: <path d="M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-6.4A8 8 0 1 1 21 12z" />,
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
  alert: <><circle cx="12" cy="12" r="9" /><path d="M12 7v6M12 16.5v.01" /></>,
  check: <path d="m5 12 5 5 9-10" />,
  shield: <><path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z" /><path d="m8.5 12 2.5 2.5 4.5-5" /></>,
  bolt: <path d="M13 2 4 14h7l-1 8 9-12h-7z" />,
  hand: <path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V11m0-1.5V4.5a1.5 1.5 0 0 1 3 0V11m0-4.5a1.5 1.5 0 0 1 3 0V14a7 7 0 0 1-7 7h-.5a6 6 0 0 1-4.6-2.2L2.6 15a1.6 1.6 0 0 1 2.4-2.1L8 15" />,
  back: <path d="M15 18 9 12l6-6" />,
  chevron: <path d="m9 18 6-6-6-6" />,
  sparkle: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 17l.8 2.2L22 20l-2.2.8L19 23l-.8-2.2L16 20l2.2-.8z" />,
  refresh: <><path d="M20 11a8 8 0 0 0-14.9-3M4 13a8 8 0 0 0 14.9 3" /><path d="M5 3v5h5M19 21v-5h-5" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  external: <><path d="M14 4h6v6M20 4l-9 9" /><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  forward: <path d="m5 5 7 7-7 7M13 5l7 7-7 7" />,
  doc: <><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></>,
  x: <path d="M6 6l12 12M18 6 6 18" />,
};

export function Icon({ name, size = 18, className }: { name: string; size?: number; className?: string }) {
  return (
    <svg className={`ico ${className ?? ""}`} width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name] ?? PATHS.alert}
    </svg>
  );
}

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path d="M16 6 9 17h4.5L10 24h12l-3.5-7H23z" fill="var(--on-accent)" />
      <rect x="15" y="23" width="2" height="4" rx="1" fill="var(--on-accent)" />
    </svg>
  );
}

// ---------- Theme (system / light / dark), remembered per browser ----------
export type ThemePref = "system" | "light" | "dark";
const THEME_KEY = "forestall-theme";

export function useTheme(): [ThemePref, (t: ThemePref) => void] {
  const [pref, setPref] = useState<ThemePref>(() => {
    try {
      const v = localStorage.getItem(THEME_KEY);
      if (v === "light" || v === "dark") return v;
    } catch { /* storage blocked */ }
    return "system";
  });
  useEffect(() => {
    const root = document.documentElement;
    if (pref === "system") delete root.dataset.theme;
    else root.dataset.theme = pref;
    try {
      if (pref === "system") localStorage.removeItem(THEME_KEY);
      else localStorage.setItem(THEME_KEY, pref);
    } catch { /* storage blocked */ }
  }, [pref]);
  return [pref, setPref];
}

export function ThemeSwitch({ pref, onChange }: { pref: ThemePref; onChange: (t: ThemePref) => void }) {
  const opts: [ThemePref, string, string][] = [["light", "sun", "Light"], ["system", "monitor", "System"], ["dark", "moon", "Dark"]];
  return (
    <div className="seg" role="radiogroup" aria-label="Color theme">
      {opts.map(([v, icon, label]) => (
        <button key={v} role="radio" aria-checked={pref === v} className={pref === v ? "on" : ""}
                onClick={() => onChange(v)} title={label}>
          <Icon name={icon} size={16} /><span className="seg-label">{label}</span>
        </button>
      ))}
    </div>
  );
}

// ---------- Hooks ----------
export function useMedia(query: string) {
  const [m, setM] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setM(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return m;
}

// Hash routes: #/overview, #/orders, #/orders/<id>, #/disputes, #/system
export function useRoute(): string[] {
  const parse = () => (window.location.hash.replace(/^#\/?/, "") || "overview").split("/");
  const [r, setR] = useState(parse);
  useEffect(() => {
    const on = () => { setR(parse()); window.scrollTo({ top: 0 }); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return r;
}
export const go = (path: string) => { window.location.hash = `/${path}`; };

// ---------- Shared labels and formatting ----------
export const SIGNAL_LABEL: Record<string, string> = {
  no_tracking: "No tracking on PayPal",
  stalled: "Shipment stalled",
  buyer_concern: "Buyer worried",
  delivered_not_received: "Delivered, not received",
  carrier_exception: "Carrier reports a problem",
};

export const FIX_TITLE: Record<string, string> = {
  attach_tracking: "Attach tracking to PayPal",
  message_buyer: "Message the buyer",
  partial_refund: "Goodwill partial refund",
  full_refund: "Full refund",
  ship_replacement: "Ship a replacement",
};

export const FIX_SHORT: Record<string, string> = {
  attach_tracking: "Attach tracking",
  message_buyer: "Message buyer",
  partial_refund: "Goodwill refund",
  full_refund: "Full refund",
  ship_replacement: "Ship replacement",
};

export const money = (amount: string | number, currency = "USD") =>
  new Intl.NumberFormat(undefined, { style: "currency", currency }).format(Number(amount));
export const when = (s: string) => new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
export const shortDate = (s: string) => new Date(s).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export const needsAttention = (o: Order) =>
  o.action_status === "proposed" || (!!o.risk_level && o.watch_status === "watching") || o.resolution === "awaiting_buyer_reply";

export function StatusBadge({ o }: { o: Order }) {
  if (o.watch_status === "protected") return <span className="badge ok"><Icon name="shield" size={13} />Protected</span>;
  if (o.watch_status === "closed") return <span className="badge ok"><Icon name="check" size={13} />Resolved</span>;
  if (o.risk_level === "high") return <span className="badge danger">High risk</span>;
  if (o.risk_level === "medium") return <span className="badge warn">Medium risk</span>;
  if (o.risk_level === "low") return <span className="badge info">Low risk</span>;
  if (o.watch_status === "watching") return <span className="badge info"><span className="dot" />Watching</span>;
  return <span className="badge neutral">Not shipped</span>;
}

export function nextStep(o: Order): { title: string; sub: string } | null {
  if (!o.action_type) {
    if (o.watch_status === "not_shipped") return { title: "Ship the order", sub: "Watching starts at shipping" };
    return null;
  }
  const sub =
    o.action_status === "proposed" ? (o.action_is_followup ? "Buyer chose, awaiting you" : "Awaiting your approval")
      : o.action_status === "executed" ? (o.resolution === "awaiting_buyer_reply" ? "Sent, awaiting buyer" : "Done")
        : o.action_status === "dismissed" ? "Dismissed" : o.action_status === "failed" ? "PayPal rejected it" : String(o.action_status);
  return { title: FIX_SHORT[o.action_type] ?? o.action_type, sub };
}

export function PageHead({ title, sub, children }: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  );
}

export function Empty({ icon, title, children }: { icon: string; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty-ico"><Icon name={icon} size={22} /></span>
      <b>{title}</b>
      {children && <span className="muted">{children}</span>}
    </div>
  );
}
