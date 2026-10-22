import { useEffect, useState } from "react";
import { createTestOrder, getHealth, getOrders, type Health, type Order } from "./api";

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

const money = (o: Order) => `${o.currency === "USD" ? "$" : o.currency + " "}${Number(o.amount).toFixed(2)}`;
const when = (s: string) => new Date(s).toLocaleString();

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [orders, setOrders] = useState<Order[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => {
    setError(null);
    getHealth().then(setHealth).catch((e) => setError(String(e.message ?? e)));
    getOrders().then(setOrders).catch(() => {});
  };
  useEffect(load, []);

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
          </>
        )}
        <button className="secondary" onClick={load}>Recheck</button>
      </section>

      <section className="card">
        <h2>Test checkout</h2>
        <p className="muted">
          Creates a sandbox order and opens PayPal. Log in with your sandbox <b>personal</b> (buyer) account
          and pay. You’ll come back here with the order captured.
        </p>
        <button onClick={buy} disabled={busy || health?.paypal !== "ok"}>
          {busy ? "Opening PayPal…" : "Create test order"}
        </button>
      </section>

      <section className="card">
        <h2>Orders ({orders.length})</h2>
        {orders.length === 0 && <p className="muted">No orders yet.</p>}
        <ul className="orders">
          {orders.map((o) => (
            <li key={o.id}>
              <div>
                <strong>{o.buyer_name ?? "Unknown buyer"}</strong>
                <span className="muted"> · {when(o.captured_at)}</span>
              </div>
              <div className="row">
                <code>{o.paypal_order_id}</code>
                <strong>{money(o)}</strong>
              </div>
              <div className="muted small">
                Capture {o.capture_id ?? "—"} · {o.tracking_number ? `Tracking ${o.tracking_number}` : "No tracking yet"}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
