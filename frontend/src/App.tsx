import { useEffect, useState } from "react";
import {
  createCardOrder, createTestOrder, getHealth, getOrders, getScenarios, simulate,
  type Health, type Order, type Scenario,
} from "./api";

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
  const clear = () => window.history.replaceState(null, "", "/");
  const text =
    state === "paid"
      ? `Payment captured by PayPal. Order ${q.get("order")} is now in Forestall.`
      : state === "cancelled"
        ? "Checkout was cancelled on PayPal."
        : `Capture failed: ${q.get("detail") ?? "unknown error"}`;
  return (
    <div className={`banner ${state === "paid" ? "ok" : "bad"}`}>
      <span>{text}</span>
      <button className="link" onClick={clear}>Dismiss</button>
    </div>
  );
}

const SIGNAL_LABEL: Record<string, string> = {
  no_tracking: "No tracking on PayPal",
  stalled: "Shipment stalled",
  buyer_concern: "Buyer worried",
  delivered_not_received: "Delivered, not received",
};

function OrderRow({ o, scenarios, onChange }: { o: Order; scenarios: Scenario[]; onChange: () => void }) {
  const [scenario, setScenario] = useState(scenarios[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await simulate(o.id, scenario || scenarios[0].id);
      if (r.ai_error) setNote(`Scenario applied, but the AI couldn't assess it: ${r.ai_error}`);
      else if (r.assessment && r.assessment.risk_level === "low") setNote(`AI: low risk. ${r.assessment.reason}`);
      onChange();
    } catch (e) {
      setNote(`Failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <li>
      <div className="row">
        <strong>{o.buyer_name ?? "Unknown buyer"}</strong>
        {o.risk_level ? (
          <span className={`badge ${o.risk_level}`}>{o.risk_level.toUpperCase()}</span>
        ) : (
          <span className="badge none">OK</span>
        )}
      </div>
      <div className="row">
        <code>{o.paypal_order_id}</code>
        <strong>{money(o)}</strong>
      </div>
      <div className="muted small">
        Paid {when(o.captured_at)} ·{" "}
        {o.tracking_attached_at ? `Tracking on PayPal (${o.tracking_number})` : o.tracking_number ? "Tracking NOT on PayPal" : "No tracking yet"}
      </div>
      {o.flag_reason && (
        <div className="flag">
          <div className="small"><b>{SIGNAL_LABEL[o.signal ?? ""] ?? o.signal}</b> · flagged by AI</div>
          <div>{o.flag_reason}</div>
        </div>
      )}
      {note && <div className="muted small note">{note}</div>}
      {scenarios.length > 0 && (
        <div className="sim">
          <select value={scenario} onChange={(e) => setScenario(e.target.value)} disabled={busy}>
            {scenarios.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          <button className="secondary" onClick={run} disabled={busy}>
            {busy ? "AI is assessing…" : "Simulate + assess"}
          </button>
        </div>
      )}
    </li>
  );
}

const money = (o: Order) => `${o.currency === "USD" ? "$" : o.currency + " "}${Number(o.amount).toFixed(2)}`;
const when = (s: string) => new Date(s).toLocaleString();

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [orders, setOrders] = useState<Order[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);

  const load = () => {
    setError(null);
    getHealth().then(setHealth).catch((e) => setError(String(e.message ?? e)));
    getOrders().then(setOrders).catch(() => {});
    getScenarios().then(setScenarios).catch(() => {});
  };
  const refreshOrders = () => getOrders().then(setOrders).catch(() => {});
  useEffect(load, []);

  const [quickNote, setQuickNote] = useState<string | null>(null);
  const quickBuy = async () => {
    setBusy(true);
    setError(null);
    setQuickNote(null);
    try {
      const r = await createCardOrder();
      setQuickNote(`PayPal captured $${Number(r.order.amount).toFixed(2)} from ${r.order.buyer_name} for “${r.item.name}”.`);
      await refreshOrders();
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
      window.location.href = t.approve_url; // PayPal sandbox checkout
    } catch (e) {
      setError(`Couldn't create the test order: ${(e as Error).message}`);
      setBusy(false);
    }
  };

  return (
    <main>
      <header>
        <h1>Forestall</h1>
        <p>Stops “item not received” disputes before buyers file them.</p>
      </header>

      <CheckoutBanner />

      <section className="card">
        <h2>System status</h2>
        {error && <p className="warn">{error}</p>}
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

      <section className="card">
        <h2>Create an order</h2>
        <p className="muted">
          <b>Quick test order</b> pays instantly with PayPal’s sandbox test card, no login needed.
          <b> PayPal checkout</b> opens the real PayPal flow for a sandbox buyer account.
        </p>
        <div className="sim">
          <button onClick={quickBuy} disabled={busy || health?.paypal !== "ok"}>
            {busy ? "Working…" : "Quick test order"}
          </button>
          <button className="secondary" onClick={buy} disabled={busy || health?.paypal !== "ok"}>
            PayPal checkout
          </button>
        </div>
        {quickNote && <p className="good small">{quickNote}</p>}
      </section>

      <section className="card">
        <h2>Orders ({orders.length})</h2>
        {orders.length === 0 && <p className="muted">No orders yet.</p>}
        <p className="muted small">
          No real carrier data exists in the sandbox, so pick a delivery story for an order and let the AI judge it.
        </p>
        <ul className="orders">
          {orders.map((o) => (
            <OrderRow key={o.id} o={o} scenarios={scenarios} onChange={refreshOrders} />
          ))}
        </ul>
      </section>
    </main>
  );
}
