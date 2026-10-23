import { useEffect, useRef, useState } from "react";
import RiskQueue from "./RiskQueue";
import Disputes from "./Disputes";
import {
  approveAction, buyerReply, dismissAction, editAction,
  advanceOrder, checkOrder, createCardOrder, createTestOrder, getHealth, getOrders, getReport,
  getDisputes, getEasyPostCodes, getScenarios, getTimeline, nudgeWatcher, shipOrder,
  type Dispute, type Health, type Order, type Report, type Scenario, type ShipChoice, type TimelineItem,
} from "./api";

const SIGNAL_LABEL: Record<string, string> = {
  no_tracking: "No tracking on PayPal",
  stalled: "Shipment stalled",
  buyer_concern: "Buyer worried",
  delivered_not_received: "Delivered, not received",
  carrier_exception: "Carrier reports a problem",
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
    ["Fixes executed", r.fixes_executed],
    ["PayPal confirmations", r.paypal_confirmations],
    ["Protected / resolved", r.protected + r.resolved_by_refund],
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
  full_refund: "Full refund",
  ship_replacement: "Ship a replacement",
};

const RESOLUTION_LABEL: Record<string, string> = {
  tracking_fixed: "Tracking fixed on PayPal",
  goodwill_refund: "Goodwill refund sent",
  awaiting_buyer_reply: "Waiting for the buyer's reply",
  buyer_reassured: "Buyer happy to wait",
  refunded: "Refunded in full, no dispute",
  replacement_shipped: "Replacement on its way",
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

function FixDetails({ o }: { o: Order }) {
  const p = o.action_params!;
  return (
    <>
      {o.action_type === "attach_tracking" && (
        <div className="small">
          {String(p.carrier)} <code>{String(p.tracking_number)}</code> goes onto the PayPal transaction, and PayPal
          emails the buyer the tracking.
        </div>
      )}
      {(o.action_type === "partial_refund" || o.action_type === "full_refund") && (
        <div className="small">
          Refund <b>${String(p.amount)}</b>{p.share_of_order_pct ? ` (${String(p.share_of_order_pct)}% of the order)` : ""} through PayPal.
          {p.note_to_payer && <div className="quote">Note PayPal shows the buyer: “{String(p.note_to_payer)}”</div>}
        </div>
      )}
      {o.action_type === "ship_replacement" && (
        <div className="small">Ship a replacement by {String(p.service ?? "Priority Mail")}; its tracking goes onto the PayPal transaction.</div>
      )}
      {o.action_type === "message_buyer" && (
        <div className="small">
          <div className="quote msg">“{String(p.message)}”</div>
          <div className="muted">You send it; PayPal has no pre-dispute messaging API, so Forestall records it.</div>
        </div>
      )}
      {o.action_justification && <div className="small why">Why: {o.action_justification}</div>}
      <div className="small muted">PayPal call: <code>{String(p.paypal_call)}</code></div>
    </>
  );
}

function ActionPanel({ o, onChange }: { o: Order; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [amount, setAmount] = useState("");
  const [reply, setReply] = useState("");
  if (!o.action_type || !o.action_params || !o.action_id) return null;
  const p = o.action_params;
  const id = o.action_id;

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setErr(null);
    try {
      const r = (await fn()) as { status?: string; paypal_error?: unknown } | undefined;
      if (r && r.status === "failed") setErr(`PayPal rejected it: ${JSON.stringify(r.paypal_error).slice(0, 200)}`);
      setEditing(false);
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const approve = () =>
    run("approve", async () => {
      if (o.action_type === "message_buyer") {
        try { await navigator.clipboard.writeText(String(p.message)); } catch { /* clipboard may be blocked */ }
      }
      return approveAction(id);
    });

  const startEdit = () => {
    setDraft(String(o.action_type === "message_buyer" ? p.message : p.note_to_payer ?? ""));
    setAmount(String(p.amount ?? ""));
    setEditing(true);
  };
  const saveEdit = () =>
    run("edit", () =>
      editAction(id, o.action_type === "message_buyer"
        ? { message: draft }
        : { note_to_payer: draft, ...(o.action_type === "partial_refund" ? { amount: Number(amount) } : {}) }));

  if (o.action_status === "proposed") {
    return (
      <div className="fix">
        <div className="row">
          <b>{o.action_is_followup ? "Buyer's choice" : "Recommended fix"} · {FIX_TITLE[o.action_type]}</b>
          <span className="chip">{o.action_edited ? "edited · " : ""}awaiting approval</span>
        </div>
        {!editing && <FixDetails o={o} />}
        {editing && (
          <div className="edit">
            {(o.action_type === "message_buyer" || o.action_type.endsWith("refund")) && (
              <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={5} />
            )}
            {o.action_type === "partial_refund" && (
              <label className="small">Amount ($) <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" /></label>
            )}
          </div>
        )}
        {err && <div className="warn small">{err}</div>}
        <div className="sim">
          {!editing ? (
            <>
              <button onClick={approve} disabled={!!busy}>
                {busy === "approve" ? "Working…" : o.action_type === "message_buyer" ? "Approve · copy & mark sent" : "Approve"}
              </button>
              {o.action_type !== "attach_tracking" && o.action_type !== "ship_replacement" && (
                <button className="secondary" onClick={startEdit} disabled={!!busy}>Edit</button>
              )}
              <button className="secondary" onClick={() => run("dismiss", () => dismissAction(id))} disabled={!!busy}>Dismiss</button>
            </>
          ) : (
            <>
              <button onClick={saveEdit} disabled={!!busy}>{busy === "edit" ? "Saving…" : "Save"}</button>
              <button className="secondary" onClick={() => setEditing(false)} disabled={!!busy}>Cancel</button>
            </>
          )}
        </div>
      </div>
    );
  }

  if (o.action_status === "executed") {
    const r = o.action_result ?? {};
    const awaiting = o.action_type === "message_buyer" && o.resolution === "awaiting_buyer_reply";
    return (
      <div className="fix done">
        <div className="row"><b>✓ Done · {FIX_TITLE[o.action_type]}</b>{o.resolution && <span className="chip ok">{RESOLUTION_LABEL[o.resolution] ?? o.resolution}</span>}</div>
        {o.action_type === "attach_tracking" && <div className="small">PayPal now holds the tracking on this transaction and emailed the buyer.</div>}
        {o.action_type.endsWith("refund") && (
          <div className="small">PayPal refunded <b>${String(r.amount)}</b> · refund <code>{String(r.refund_id)}</code> ({String(r.status)})</div>
        )}
        {o.action_type === "ship_replacement" && <div className="small">Replacement tracking <code>{String(r.tracking_number)}</code> is on the PayPal transaction.</div>}
        {o.action_type === "message_buyer" && <div className="small quote msg">“{String(p.message)}”</div>}
        {awaiting && (
          <div className="reply">
            <div className="small muted">Demo: reply as the buyer</div>
            <div className="sim">
              <button className="secondary" onClick={() => run("reply", () => buyerReply(o.id, "1"))} disabled={!!busy}>“1”</button>
              <button className="secondary" onClick={() => run("reply", () => buyerReply(o.id, "2"))} disabled={!!busy}>“2”</button>
              <button className="secondary" onClick={() => run("reply", () => buyerReply(o.id, "No worries, I can wait a few more days."))} disabled={!!busy}>“I can wait”</button>
            </div>
            <div className="sim">
              <input value={reply} onChange={(e) => setReply(e.target.value)} placeholder="…or type a reply" />
              <button onClick={() => run("reply", () => buyerReply(o.id, reply))} disabled={!!busy || !reply.trim()}>
                {busy === "reply" ? "AI is reading it…" : "Send reply"}
              </button>
            </div>
          </div>
        )}
        {err && <div className="warn small">{err}</div>}
      </div>
    );
  }

  if (o.action_status === "failed") {
    return <div className="fix bad small">PayPal rejected the {FIX_TITLE[o.action_type].toLowerCase()}. See the timeline for details.</div>;
  }
  if (o.action_status === "dismissed") {
    return <div className="fix small muted">You dismissed the suggested fix ({FIX_TITLE[o.action_type].toLowerCase()}).</div>;
  }
  return null;
}

function OrderCard({ o, scenarios, epCodes, onChange }: {
  o: Order; scenarios: Scenario[]; epCodes: EpCode[]; onChange: () => void;
}) {
  const [scenario, setScenario] = useState("sim:no_tracking");
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
      : o.watch_status === "closed" ? <span className="badge protected">RESOLVED</span>
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
        {o.shipped_at && (
          <> · carrier data: {o.carrier_source === "easypost"
            ? (o.tracking_public_url ? <a href={o.tracking_public_url} target="_blank" rel="noreferrer">EasyPost (live)</a> : "EasyPost (live)")
            : o.carrier_source === "simulator" ? "simulated" : "manual"}</>
        )}
        {o.watch_status === "watching" && o.next_check_at && <> · next check {when(o.next_check_at)}</>}
      </div>

      {o.flag_reason && (
        <div className="flag">
          <div className="small"><b>{SIGNAL_LABEL[o.signal ?? ""] ?? o.signal}</b> · flagged by AI</div>
          <div>{o.flag_reason}</div>
        </div>
      )}
      <FindingsCard o={o} />
      <ActionPanel o={o} onChange={onChange} />
      {note && <div className="muted small note">{note}</div>}

      {o.watch_status === "not_shipped" && (
        <div className="sim">
          <select value={scenario} onChange={(e) => setScenario(e.target.value)} disabled={!!busy}>
            <optgroup label="Delivery stories (simulated carrier)">
              {scenarios.map((s) => <option key={s.id} value={`sim:${s.id}`}>{s.label}</option>)}
            </optgroup>
            {epCodes.length > 0 && (
              <optgroup label="Real carrier API: EasyPost test parcels">
                {epCodes.map((c) => <option key={c.code} value={`ep:${c.code}`}>EasyPost · {c.label}</option>)}
              </optgroup>
            )}
          </select>
          <button onClick={() => act("ship", () => shipOrder(o.id, parseChoice(scenario)))} disabled={!!busy}>
            {busy === "ship" ? "Shipping…" : "Mark shipped"}
          </button>
        </div>
      )}
      {o.watch_status === "watching" && (
        <div className="sim">
          {(
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
  const [epCodes, setEpCodes] = useState<EpCode[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [quickNote, setQuickNote] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);
  const selected = orders.find((o) => o.id === selectedId) ?? null;
  const select = (id: string) => {
    setSelectedId(id);
    setTimeout(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };

  const [disputes, setDisputes] = useState<Dispute[]>([]);
  const refresh = () => {
    getDisputes().then(setDisputes).catch(() => {});
    getOrders().then(setOrders).catch(() => {});
    getReport().then(setReport).catch(() => {});
  };
  const load = () => {
    setError(null);
    getHealth().then(setHealth).catch((e) => setError(String(e.message ?? e)));
    getScenarios().then(setScenarios).catch(() => {});
    getEasyPostCodes().then(setEpCodes).catch(() => {});
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
      setQuickNote(`PayPal captured $${Number(r.order.amount).toFixed(2)} from ${r.order.buyer_name} for “${r.item.name}”. Ship it below.`);
      refresh();
      select(r.order.id);
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
        <h2>2 · Risk queue</h2>
        <p className="muted small">
          Every order, ranked by risk. Approve a fix right in the row, or tap a row to see the investigation,
          edit the fix, ship an order, or step through time with <b>Advance 1 day</b>.
        </p>
        <RiskQueue orders={orders} selectedId={selectedId} onSelect={select} onChange={refresh} />
      </section>

      <div ref={detailRef}>
        {selected && (
          <section className="card">
            <div className="row">
              <h2>Order details</h2>
              <button className="link small" onClick={() => setSelectedId(null)}>Close</button>
            </div>
            <ul className="orders">
              <OrderCard key={selected.id} o={selected} scenarios={scenarios} epCodes={epCodes} onChange={refresh} />
            </ul>
          </section>
        )}
      </div>

      <Disputes items={disputes} onChange={refresh} />

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
            <Pill label={`Carrier API (EasyPost${health.easypost_mode ? ", " + health.easypost_mode : ""})`} value={health.easypost} />
          </>
        )}
        <button className="secondary" onClick={load}>Recheck</button>
      </section>
    </main>
  );
}
