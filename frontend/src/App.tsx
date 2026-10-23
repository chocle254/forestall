import { useEffect, useState } from "react";
import {
  advanceOrder, checkOrder, createCardOrder, createTestOrder, getHealth, getOrders, getReport,
  getScenarios, getTimeline, nudgeWatcher, shipOrder,
  type Health, type Order, type Report, type Scenario, type TimelineItem,
} from "./api";

const SIGNAL_LABEL: Record<string, string> = {
  no_tracking: "No tracking on PayPal",
  stalled: "Shipment stalled",
  buyer_concern: "Buyer worried",
  delivered_not_received: "Delivered, not received",
};

const KIND_ICON: Record<string, string> = {
  paid: "💳", shipped: "📦", scan: "🚚", delivered: "🏠", buyer_message: "💬", check: "🔎",
  flag: "⚠️", flag_cleared: "✅", paypal: "🅿️", action: "👆", protected: "🛡️",
};

const money = (o: Order) => `${o.currency === "USD" ? "$" : o.currency + " "}${Number(o.amount).toFixed(2)}`;
const when = (s: string) => new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

function Pill({ label, value }: { label: string; value: string }) {
  const good = value === "ok" || value === "sandbox";
  return (
    <div className="pill">
      <span>{label}</span>
      <strong className={good ? "good" : "warn"}>{value}</strong>
    </div>
  );
}

function CheckoutBanner() {
  const q = new URLSearchParams(window.location.search);
  const state = q.get("checkout");
  if (!state) return null;
  const text =
    state === "paid"
      ? `Payment captured by PayPal. Order ${q.get("order")} is now in Forestall.`
      : state === "cancelled"
        ? "Checkout was cancelled on PayPal."
        : `Capture failed: ${q.get("detail") ?? "unknown error"}`;
  return (
    <div className={`banner ${state === "paid" ? "ok" : "bad"}`}>
      <span>{text}</span>
      <button className="link" onClick={() => window.history.replaceState(null, "", "/")}>Dismiss</button>
    </div>
  );
}

function ReportCard({ r }: { r: Report | null }) {
  if (!r) return null;
  const stats: [string, string | number][] = [
    ["Orders watched", r.shipped],
    ["Watching now", r.watching],
    ["AI checks run", r.checks_run],
    ["Risks caught", r.orders_flagged],
    ["PayPal confirmations", r.paypal_confirmations],
    ["Protected", r.protected],
  ];
  return (
    <section className="card">
      <h2>Protection report</h2>
      <div className="stats">
        {stats.map(([k, v]) => (
          <div key={k} className="stat"><strong>{v}</strong><span>{k}</span></div>
        ))}
      </div>
      <p className="small muted">
        Proof on PayPal: <b>{r.proof_coverage_pct === null ? "—" : `${r.proof_coverage_pct}%`}</b> of shipped orders
        have tracking on the PayPal transaction · Disputes: <b>{r.disputes}</b>
      </p>
    </section>
  );
}

function Timeline({ id, version }: { id: string; version: number }) {
  const [items, setItems] = useState<TimelineItem[] | null>(null);
  useEffect(() => {
    getTimeline(id).then(setItems).catch(() => setItems([]));
  }, [id, version]);
  if (!items) return <p className="muted small">Loading timeline…</p>;
  return (
    <ol className="timeline">
      {items.map((t, i) => (
        <li key={i} className={`k-${t.kind}`}>
          <span className="icon">{KIND_ICON[t.kind] ?? "•"}</span>
          <div>
            <div><b>{t.title}</b> <span className="muted small">· {when(t.occurred_at)}</span></div>
            {typeof t.detail.reason === "string" && t.kind !== "check" && <div className="small">{t.detail.reason}</div>}
            {typeof t.detail.text === "string" && <div className="small quote">“{t.detail.text}”</div>}
            {t.kind === "check" && typeof t.detail.reason === "string" && (
              <div className="small muted">{t.detail.reason}</div>
            )}
            {t.kind === "paypal" && <div className="small muted">PayPal response stored as proof</div>}
            {t.kind === "investigation" && typeof t.detail.summary === "string" && (
              <div className="small">{t.detail.summary}</div>
            )}
            {t.kind === "investigation" && Array.isArray(t.detail.tools_used) && (
              <div className="small muted">Tools: {(t.detail.tools_used as string[]).map((x) => TOOL_LABEL[x] ?? x).join(", ")}</div>
            )}
            {t.kind === "protected" && Array.isArray(t.detail.trackers_on_paypal) && (
              <div className="small muted">
                Read back from PayPal: {t.detail.trackers_on_paypal.length} tracker(s) on the transaction
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

const FIX_TITLE: Record<string, string> = {
  attach_tracking: "Attach tracking to PayPal",
  message_buyer: "Message the buyer",
  partial_refund: "Goodwill partial refund",
};

const TOOL_LABEL: Record<string, string> = {
  get_carrier_tracking: "carrier tracking",
  get_carrier_facility_status: "facility alerts",
  get_paypal_order: "PayPal order (live)",
  get_buyer_messages: "buyer messages",
  get_seller_policy: "seller policy",
};

function FindingsCard({ o }: { o: Order }) {
  const f = o.findings;
  if (!f) return null;
  const tools = Array.from(new Set(o.tools_used)).map((t) => TOOL_LABEL[t] ?? t);
  return (
    <div className="findings">
      <div className="row">
        <b>🔍 Forestall investigated</b>
        <span className="chip">{f.confidence} confidence</span>
      </div>
      <div className="small">{f.summary}</div>
      <ul className="evidence">
        {f.evidence.map((e, i) => (
          <li key={i} className="small"><span className={`src src-${e.source}`}>{e.source}</span> {e.fact}</li>
        ))}
      </ul>
      {f.buyer_need && <div className="small"><b>Buyer needs:</b> {f.buyer_need}</div>}
      {tools.length > 0 && <div className="small muted">Checked: {tools.join(" · ")}</div>}
    </div>
  );
}

function ProposedFix({ o }: { o: Order }) {
  if (!o.action_type || !o.action_params) return null;
  const p = o.action_params;
  return (
    <div className="fix">
      <div className="row">
        <b>Recommended fix · {FIX_TITLE[o.action_type]}</b>
        <span className="chip">awaiting approval</span>
      </div>
      {o.action_type === "attach_tracking" && (
        <div className="small">
          {String(p.carrier)} <code>{String(p.tracking_number)}</code> goes onto the PayPal transaction, and PayPal
          emails the buyer the tracking.
        </div>
      )}
      {o.action_type === "partial_refund" && (
        <div className="small">
          Refund <b>${String(p.amount)}</b> ({String(p.share_of_order_pct)}% of the order) through PayPal.
          {p.note_to_payer && <div className="quote">Note PayPal shows the buyer: “{String(p.note_to_payer)}”</div>}
        </div>
      )}
      {o.action_type === "message_buyer" && (
        <div className="small">
          <div className="quote msg">“{String(p.message)}”</div>
          <div className="muted">Sent by you; PayPal has no pre-dispute messaging API, so Forestall records it.</div>
        </div>
      )}
      {o.action_justification && <div className="small why">Why: {o.action_justification}</div>}
      <div className="small muted">PayPal call: <code>{String(p.paypal_call)}</code></div>
    </div>
  );
}

function OrderCard({ o, scenarios, onChange }: { o: Order; scenarios: Scenario[]; onChange: () => void }) {
  const [scenario, setScenario] = useState("no_tracking");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [version, setVersion] = useState(0);

  const act = async (label: string, fn: () => Promise<{ error?: string; risk_level?: string; watch_status?: string }>) => {
    setBusy(label);
    setNote(null);
    try {
      const r = await fn();
      if (r.error) setNote(r.error);
      else if (r.watch_status === "protected") setNote("Delivered with proof on PayPal. Watch closed: protected.");
      setVersion((v) => v + 1);
      onChange();
    } catch (e) {
      setNote(`Failed: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const status =
    o.watch_status === "protected" ? <span className="badge protected">PROTECTED</span>
      : o.risk_level ? <span className={`badge ${o.risk_level}`}>{o.risk_level.toUpperCase()}</span>
        : o.watch_status === "watching" ? <span className="badge watching">WATCHING</span>
          : <span className="badge none">NOT SHIPPED</span>;

  return (
    <li>
      <div className="row">
        <strong>{o.buyer_name ?? "Unknown buyer"}</strong>
        {status}
      </div>
      <div className="row">
        <code>{o.paypal_order_id}</code>
        <strong>{money(o)}</strong>
      </div>
      <div className="muted small">
        Paid {when(o.captured_at)}
        {o.shipped_at && <> · {o.tracking_attached_at ? "Tracking on PayPal ✓" : "Tracking NOT on PayPal"}</>}
        {o.watch_status === "watching" && o.next_check_at && <> · next check {when(o.next_check_at)}</>}
      </div>

      {o.flag_reason && (
        <div className="flag">
          <div className="small"><b>{SIGNAL_LABEL[o.signal ?? ""] ?? o.signal}</b> · flagged by AI</div>
          <div>{o.flag_reason}</div>
        </div>
      )}
      <FindingsCard o={o} />
      <ProposedFix o={o} />
      {note && <div className="muted small note">{note}</div>}

      {o.watch_status === "not_shipped" && (
        <div className="sim">
          <select value={scenario} onChange={(e) => setScenario(e.target.value)} disabled={!!busy}>
            {scenarios.map((s) => <option key={s.id} value={s.id}>Demo: {s.label}</option>)}
            <option value="">Real shipment (attach tracking to PayPal now)</option>
          </select>
          <button onClick={() => act("ship", () => shipOrder(o.id, scenario || null))} disabled={!!busy}>
            {busy === "ship" ? "Shipping…" : "Mark shipped"}
          </button>
        </div>
      )}
      {o.watch_status === "watching" && (
        <div className="sim">
          {o.demo_scenario && (
            <button onClick={() => act("advance", () => advanceOrder(o.id, 24))} disabled={!!busy}>
              {busy === "advance" ? "AI is checking…" : "Advance 1 day ⏩"}
            </button>
          )}
          <button className="secondary" onClick={() => act("check", () => checkOrder(o.id))} disabled={!!busy}>
            {busy === "check" ? "Checking…" : "Check now"}
          </button>
        </div>
      )}

      {o.watch_status !== "not_shipped" && (
        <button className="link small" onClick={() => setOpen(!open)}>
          {open ? "Hide timeline" : "Show timeline (proof Forestall followed this order)"}
        </button>
      )}
      {open && <Timeline id={o.id} version={version} />}
    </li>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [orders, setOrders] = useState<Order[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [quickNote, setQuickNote] = useState<string | null>(null);

  const refresh = () => {
    getOrders().then(setOrders).catch(() => {});
    getReport().then(setReport).catch(() => {});
  };
  const load = () => {
    setError(null);
    getHealth().then(setHealth).catch((e) => setError(String(e.message ?? e)));
    getScenarios().then(setScenarios).catch(() => {});
    refresh();
  };
  useEffect(() => {
    load();
    // Opening the app also lets the watcher catch up on any orders that are due.
    nudgeWatcher().then(() => setTimeout(refresh, 500));
  }, []);

  const quickBuy = async () => {
    setBusy(true);
    setError(null);
    setQuickNote(null);
    try {
      const r = await createCardOrder();
      setQuickNote(`PayPal captured $${Number(r.order.amount).toFixed(2)} from ${r.order.buyer_name} for “${r.item.name}”.`);
      refresh();
    } catch (e) {
      setError(`Couldn't create the quick order: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const buy = async () => {
    setBusy(true);
    setError(null);
    try {
      const t = await createTestOrder();
      window.location.href = t.approve_url;
    } catch (e) {
      setError(`Couldn't create the test order: ${(e as Error).message}`);
      setBusy(false);
    }
  };

  const ready = health?.paypal === "ok";

  return (
    <main>
      <header>
        <h1>Forestall</h1>
        <p>Watches every PayPal order from shipping to delivery and stops “item not received” disputes before they start.</p>
      </header>

      <CheckoutBanner />
      <ReportCard r={report} />

      <section className="card">
        <h2>1 · Take a payment</h2>
        <p className="muted small">
          <b>Quick test order</b> pays instantly with PayPal’s sandbox test card. <b>PayPal checkout</b> opens the real
          PayPal flow for a sandbox buyer account.
        </p>
        <div className="sim">
          <button onClick={quickBuy} disabled={busy || !ready}>{busy ? "Working…" : "Quick test order"}</button>
          <button className="secondary" onClick={buy} disabled={busy || !ready}>PayPal checkout</button>
        </div>
        {quickNote && <p className="good small">{quickNote}</p>}
        {error && <p className="warn small">{error}</p>}
      </section>

      <section className="card">
        <h2>2 · Ship it and let Forestall watch ({orders.length})</h2>
        <p className="muted small">
          Mark an order shipped with a demo delivery story, then use <b>Advance 1 day</b> to watch it unfold. The AI
          checks the order whenever something happens and whenever time alone creates risk.
        </p>
        {orders.length === 0 && <p className="muted">No orders yet.</p>}
        <ul className="orders">
          {orders.map((o) => <OrderCard key={o.id} o={o} scenarios={scenarios} onChange={refresh} />)}
        </ul>
      </section>

      <section className="card">
        <h2>System status</h2>
        {!health && !error && <p>Checking…</p>}
        {health && (
          <>
            <Pill label="API" value={health.status} />
            <Pill label="Database" value={health.database} />
            <Pill label="PayPal" value={health.paypal} />
            <Pill label="PayPal mode" value={health.paypal_env} />
            <Pill label={`AI (${health.ai_model})`} value={health.ai} />
          </>
        )}
        <button className="secondary" onClick={load}>Recheck</button>
      </section>
    </main>
  );
}
