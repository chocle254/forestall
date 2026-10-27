import { useCallback, useEffect, useState } from "react";
import RiskQueue from "./RiskQueue";
import Disputes from "./Disputes";
import OrderDetail, { type EpCode } from "./OrderDetail";
import Connect from "./Connect";
import TrackPage from "./TrackPage";
import {
  createCardOrder, createTestOrder, getDisputes, getEasyPostCodes, getHealth, getOrders, getReport, getScenarios,
  nudgeWatcher, type Dispute, type Health, type Order, type Report, type Scenario,
} from "./api";
import {
  Empty, Icon, Logo, PageHead, SIGNAL_LABEL, StatusBadge, ThemeSwitch, go, money, needsAttention, nextStep,
  useRoute, useTheme,
} from "./ui";

function CheckoutBanner() {
  const [q, setQ] = useState(() => new URLSearchParams(window.location.search));
  const state = q.get("checkout");
  if (!state) return null;
  const ok = state === "paid";
  const text = ok
    ? `Payment captured by PayPal. Order ${q.get("order")} is now in Forestall.`
    : state === "cancelled" ? "Checkout was cancelled on PayPal." : `Capture failed: ${q.get("detail") ?? "unknown error"}`;
  const close = () => {
    window.history.replaceState(null, "", window.location.pathname + window.location.hash);
    setQ(new URLSearchParams());
  };
  return (
    <div className={`alert ${ok ? "ok" : "danger"} banner`}>
      <Icon name={ok ? "check" : "alert"} />
      <span>{text}</span>
      <button className="icon-btn" onClick={close} aria-label="Dismiss"><Icon name="x" size={16} /></button>
    </div>
  );
}

function Kpis({ r }: { r: Report | null }) {
  const tiles: [string, string, string][] = r ? [
    ["Orders watched", String(r.shipped), `${r.watching} in transit now`],
    ["Risks caught", String(r.orders_flagged), `${r.checks_run} AI checks run`],
    ["Fixes run on PayPal", String(r.fixes_executed), `${r.paypal_confirmations} PayPal confirmations`],
    ["Proof on PayPal", r.proof_coverage_pct === null ? "—" : `${r.proof_coverage_pct}%`, "shipped orders with tracking attached"],
    ["Protected", String(r.protected + r.resolved_by_refund), `${money(r.refunded_total)} refunded as goodwill`],
    ["Disputes", String(r.disputes), "opened by buyers"],
  ] : [];
  return (
    <div className="kpis">
      {r ? tiles.map(([label, value, sub]) => (
        <div key={label} className="kpi">
          <span className="kpi-label">{label}</span>
          <strong className="kpi-value num">{value}</strong>
          <span className="kpi-sub">{sub}</span>
        </div>
      )) : Array.from({ length: 6 }, (_, i) => <div key={i} className="kpi skeleton" />)}
    </div>
  );
}

function SignalBreakdown({ r }: { r: Report | null }) {
  const entries = Object.entries(r?.risks_caught_by_signal ?? {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, n]) => n));
  return (
    <section className="card">
      <div className="card-head"><h2>Risks caught, by cause</h2></div>
      {entries.length === 0 ? <p className="muted small">No risks caught yet.</p> : (
        <ul className="bars">
          {entries.map(([k, n]) => (
            <li key={k}>
              <span className="bar-label">{SIGNAL_LABEL[k] ?? k}</span>
              <span className="bar-track"><span className="bar-fill" style={{ width: `${(n / max) * 100}%` }} /></span>
              <span className="bar-value num">{n}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function AttentionList({ orders }: { orders: Order[] }) {
  const items = orders.filter(needsAttention).slice(0, 5);
  return (
    <section className="card">
      <div className="card-head">
        <h2>Needs your attention</h2>
        <button className="btn ghost sm" onClick={() => go("orders")}>All orders<Icon name="chevron" size={15} /></button>
      </div>
      {items.length === 0 ? (
        <Empty icon="shield" title="You're all caught up">Forestall is watching every shipped order.</Empty>
      ) : (
        <ul className="list">
          {items.map((o) => {
            const s = nextStep(o);
            return (
              <li key={o.id}>
                <button className="list-row" onClick={() => go(`orders/${o.id}`)}>
                  <StatusBadge o={o} />
                  <span className="list-main">
                    <b>{o.buyer_name ?? "Unknown buyer"}</b>
                    <span className="muted small">{o.signal ? SIGNAL_LABEL[o.signal] ?? o.signal : s?.sub}</span>
                  </span>
                  {s && <span className="list-step small">{s.title}</span>}
                  <span className="num list-amt">{money(o.amount, o.currency)}</span>
                  <Icon name="chevron" className="muted" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function DemoCard({ ready, onCreated }: { ready: boolean; onCreated: (id: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const quick = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await createCardOrder();
      onCreated(r.order.id);
    } catch (e) {
      setErr(`Couldn't create the order: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };
  const checkout = async () => {
    setBusy(true);
    setErr(null);
    try {
      window.location.href = (await createTestOrder()).approve_url;
    } catch (e) {
      setErr(`Couldn't start checkout: ${(e as Error).message}`);
      setBusy(false);
    }
  };
  return (
    <section className="card demo">
      <div className="card-head">
        <h2>Try it with a sandbox order</h2>
        <span className="tag">PayPal sandbox</span>
      </div>
      <p className="muted small">
        <b>Quick test order</b> pays instantly with PayPal's sandbox test card. <b>PayPal checkout</b> opens the real
        PayPal flow with a sandbox buyer account. Then ship the order and step through the delivery.
      </p>
      <div className="actions">
        <button className="btn primary" onClick={quick} disabled={busy || !ready}><Icon name="plus" size={16} />{busy ? "Working…" : "Quick test order"}</button>
        <button className="btn" onClick={checkout} disabled={busy || !ready}><Icon name="card" size={16} />PayPal checkout</button>
      </div>
      {!ready && <p className="small muted">Waiting for PayPal to connect…</p>}
      {err && <p className="alert danger small">{err}</p>}
    </section>
  );
}

const STEPS: [string, string, string][] = [
  ["card", "Payment captured", "Every PayPal order lands in Forestall."],
  ["search", "AI keeps watch", "Tracking, carrier scans and buyer messages are checked on a schedule."],
  ["bolt", "One clear fix", "When something slips, you get one fix with a reason. You approve it."],
  ["shield", "Proof on PayPal", "Fixes run through PayPal's API and every step is kept as evidence."],
];

function HowItWorks() {
  return (
    <section className="card">
      <div className="card-head"><h2>How Forestall protects an order</h2></div>
      <ol className="steps">
        {STEPS.map(([icon, title, text], i) => (
          <li key={title}>
            <span className="step-ico"><Icon name={icon} /></span>
            <div><b>{i + 1}. {title}</b><p className="muted small">{text}</p></div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function SystemPage({ health, error, onRecheck }: { health: Health | null; error: string | null; onRecheck: () => void }) {
  const rows: [string, string, string][] = health ? [
    ["API", health.status, "Forestall backend"],
    ["Database", health.database, "Orders, timeline and audit log"],
    ["PayPal", health.paypal, `Mode: ${health.paypal_env}`],
    ["AI", health.ai, health.ai_model],
    ["Carrier API", health.easypost, `EasyPost${health.easypost_mode ? ` (${health.easypost_mode} mode)` : ""}`],
    ["Buyer email", health.email, health.email === "ok" ? (health.email_redirect ? "SMTP · sandbox inbox" : "SMTP") : "SMTP not set up"],
  ] : [];
  return (
    <>
      <PageHead title="System" sub="Live status of every service Forestall depends on.">
        <button className="btn" onClick={onRecheck}><Icon name="refresh" size={16} />Recheck</button>
      </PageHead>
      {error && <p className="alert danger">Can't reach the Forestall API: {error}</p>}
      <section className="card">
        {!health && !error && <div className="skeleton" style={{ height: 200 }} />}
        <ul className="list">
          {rows.map(([name, value, sub]) => {
            const good = value === "ok";
            return (
              <li key={name} className="status-row">
                <span className={`status-dot ${good ? "ok" : "bad"}`} />
                <span className="list-main"><b>{name}</b><span className="muted small">{sub}</span></span>
                <span className={`badge ${good ? "ok" : "warn"}`}>{good ? "Operational" : value}</span>
              </li>
            );
          })}
        </ul>
      </section>
    </>
  );
}

const NAV: [string, string, string][] = [
  ["overview", "home", "Overview"],
  ["orders", "orders", "Orders"],
  ["disputes", "dispute", "Disputes"],
  ["connect", "plug", "Connect"],
  ["system", "status", "System"],
];

export default function App() {
  // The buyer's tracking page is public and has no seller chrome.
  const track = window.location.pathname.match(/^\/track\/([A-Za-z0-9]+)/);
  if (track) return <TrackPage token={track[1]} />;
  return <SellerApp />;
}

function SellerApp() {
  const [themePref, setThemePref] = useTheme();
  const route = useRoute();
  const page = route[0];
  const [health, setHealth] = useState<Health | null>(null);
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [disputes, setDisputes] = useState<Dispute[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [epCodes, setEpCodes] = useState<EpCode[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getDisputes().then(setDisputes).catch(() => {});
    getOrders().then(setOrders).catch(() => setOrders((o) => o ?? []));
    getReport().then(setReport).catch(() => {});
  }, []);
  const loadHealth = () => {
    setError(null);
    getHealth().then(setHealth).catch((e) => setError(String(e.message ?? e)));
  };
  useEffect(() => {
    loadHealth();
    getScenarios().then(setScenarios).catch(() => {});
    getEasyPostCodes().then(setEpCodes).catch(() => {});
    refresh();
    // Opening the app also lets the watcher catch up on any orders that are due.
    nudgeWatcher().then(() => setTimeout(refresh, 500));
  }, [refresh]);

  const attention = orders?.filter(needsAttention).length ?? 0;
  const openDisputes = disputes.filter((d) => d.status !== "RESOLVED").length;
  const badge: Record<string, number> = { orders: attention, disputes: openDisputes };
  const sandbox = health?.paypal_env === "sandbox";
  const navPage = ["orders", "disputes", "connect", "system"].includes(page) ? page : "overview";

  let body;
  if (page === "orders" && route[1]) {
    body = orders === null ? <div className="skeleton" style={{ height: 320 }} />
      : <OrderDetail o={orders.find((o) => o.id === route[1]) ?? null} scenarios={scenarios} epCodes={epCodes}
                       emailOn={health?.email === "ok"} onChange={refresh} />;
  } else if (page === "orders") {
    body = (
      <>
        <PageHead title="Orders" sub="Every PayPal order, ranked by risk. Approve a fix in the row, or open an order to see the full investigation." />
        <section className="card flush">
          {orders === null ? <div className="skeleton" style={{ height: 320 }} />
            : <RiskQueue orders={orders} onSelect={(id) => go(`orders/${id}`)} onChange={refresh} />}
        </section>
      </>
    );
  } else if (page === "disputes") {
    body = <Disputes items={disputes} onChange={refresh} />;
  } else if (page === "connect") {
    body = <Connect onChange={refresh} />;
  } else if (page === "system") {
    body = <SystemPage health={health} error={error} onRecheck={loadHealth} />;
  } else {
    body = (
      <>
        <PageHead title="Overview" sub="Forestall watches every PayPal order from payment to delivery and stops “item not received” disputes before buyers file them." />
        <Kpis r={report} />
        <div className="overview-grid">
          <AttentionList orders={orders ?? []} />
          <DemoCard ready={health?.paypal === "ok"} onCreated={(id) => { refresh(); go(`orders/${id}`); }} />
          <SignalBreakdown r={report} />
          <HowItWorks />
        </div>
      </>
    );
  }

  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <aside className="sidebar">
        <a className="brand" href="#/overview"><Logo /><span>Forestall</span></a>
        <nav aria-label="Main">
          {NAV.map(([id, icon, label]) => (
            <a key={id} href={`#/${id}`} className={`nav-item ${navPage === id ? "on" : ""}`} aria-current={navPage === id ? "page" : undefined}>
              <Icon name={icon} /><span>{label}</span>
              {badge[id] > 0 && <span className="nav-count">{badge[id]}</span>}
            </a>
          ))}
        </nav>
        <div className="sidebar-foot">
          {health && <span className={`env ${sandbox ? "sandbox" : ""}`}><span className="dot" />{sandbox ? "PayPal sandbox" : `PayPal ${health.paypal_env}`}</span>}
          <ThemeSwitch pref={themePref} onChange={setThemePref} />
        </div>
      </aside>

      <header className="topbar">
        <a className="brand" href="#/overview"><Logo size={26} /><span>Forestall</span></a>
        {health && sandbox && <span className="env sandbox"><span className="dot" />Sandbox</span>}
        <button className="icon-btn" aria-label="Change theme" title="Change theme"
                onClick={() => setThemePref(themePref === "system" ? "light" : themePref === "light" ? "dark" : "system")}>
          <Icon name={themePref === "light" ? "sun" : themePref === "dark" ? "moon" : "monitor"} />
        </button>
      </header>

      <main id="main" className="content">
        <CheckoutBanner />
        {body}
      </main>

      <nav className="tabbar" aria-label="Main">
        {NAV.map(([id, icon, label]) => (
          <a key={id} href={`#/${id}`} className={navPage === id ? "on" : ""} aria-current={navPage === id ? "page" : undefined}>
            <span className="tab-ico"><Icon name={icon} size={20} />{badge[id] > 0 && <span className="tab-count">{badge[id]}</span>}</span>
            <span>{label}</span>
          </a>
        ))}
      </nav>
    </div>
  );
}
