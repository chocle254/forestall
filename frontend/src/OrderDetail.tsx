import { useEffect, useState } from "react";
import {
  advanceOrder, approveAction, buyerReply, checkOrder, dismissAction, editAction, getTimeline, shipOrder,
  type CheckResult, type Order, type Scenario, type ShipChoice, type TimelineItem,
} from "./api";
import { Empty, FIX_TITLE, Icon, SIGNAL_LABEL, StatusBadge, go, money, when } from "./ui";

export type EpCode = { code: string; label: string };

function parseChoice(v: string): ShipChoice {
  if (v.startsWith("sim:")) return { kind: "sim", scenario: v.slice(4) };
  if (v.startsWith("ep:")) return { kind: "ep", code: v.slice(3) };
  return { kind: "manual" };
}

const KIND_ICON: Record<string, string> = {
  paid: "card", shipped: "box", scan: "truck", delivered: "pin", buyer_message: "chat", check: "sparkle",
  flag: "alert", flag_cleared: "check", paypal: "shield", action: "hand", protected: "shield", investigation: "sparkle",
  email: "mail", store: "store", webhook: "bolt",
};
const KIND_TONE: Record<string, string> = {
  flag: "danger", flag_cleared: "ok", paypal: "ok", protected: "ok", investigation: "ai", check: "ai", action: "accent", buyer_message: "warn",
  email: "accent", store: "accent",
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
  get_carrier_tracking: "Carrier tracking",
  get_carrier_facility_status: "Facility alerts",
  get_paypal_order: "PayPal order (live)",
  get_buyer_messages: "Buyer messages",
  get_seller_policy: "Seller policy",
};

function Timeline({ id, version }: { id: string; version: number }) {
  const [items, setItems] = useState<TimelineItem[] | null>(null);
  useEffect(() => {
    getTimeline(id).then(setItems).catch(() => setItems([]));
  }, [id, version]);
  if (!items) return <div className="skeleton" style={{ height: 160 }} />;
  if (items.length === 0) return <p className="muted small">No events yet.</p>;
  return (
    <ol className="timeline">
      {items.map((t, i) => (
        <li key={i} className={`tone-${KIND_TONE[t.kind] ?? "neutral"}`}>
          <span className="tl-ico"><Icon name={KIND_ICON[t.kind] ?? "clock"} size={14} /></span>
          <div className="tl-body">
            <div className="tl-head"><b>{t.title}</b><time className="muted small">{when(t.occurred_at)}</time></div>
            {typeof t.detail.reason === "string" && <div className="small muted">{t.detail.reason}</div>}
            {typeof t.detail.text === "string" && <blockquote className="small">“{t.detail.text}”</blockquote>}
            {t.kind === "paypal" && <div className="small muted">PayPal's response is stored as proof.</div>}
            {(t.kind === "investigation" || t.kind === "check") && typeof t.detail.summary === "string" && <div className="small">{t.detail.summary}</div>}
            {t.kind === "check" && typeof t.detail.next_check_reason === "string" && (
              <div className="small muted">Next look: {t.detail.next_check_reason}</div>
            )}
            {(t.kind === "investigation" || t.kind === "check") && Array.isArray(t.detail.tools_used) && t.detail.tools_used.length > 0 && (
              <div className="small muted">Checked: {(t.detail.tools_used as string[]).map((x) => TOOL_LABEL[x] ?? x).join(", ")}</div>
            )}
            {t.kind === "protected" && Array.isArray(t.detail.trackers_on_paypal) && (
              <div className="small muted">Read back from PayPal: {t.detail.trackers_on_paypal.length} tracker(s) on the transaction.</div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

const MOOD_LABEL: Record<string, string> = {
  calm: "calm", curious: "curious", worried: "worried", frustrated: "frustrated", angry: "angry",
};

const SOURCE_LABEL: Record<string, string> = {
  carrier: "carrier", paypal: "paypal", buyer: "buyer", seller_policy: "policy", forestall: "forestall",
};

/** The AI's latest investigation: what it checked, what it learned, what it decided, and why. */
function AiInvestigation({ o }: { o: Order }) {
  const c = o.ai_check;
  const f = o.findings;
  if (!c || !c.steps?.length) return null;
  const tools = Array.from(new Set(c.tools_used ?? [])).map((t) => TOOL_LABEL[t] ?? t);
  return (
    <section className="panel tone-ai">
      <div className="panel-head">
        <span className="panel-title"><Icon name="sparkle" />AI investigation</span>
        <span className="tag">{o.ai_model ?? "AI"}{o.ai_checked_at ? ` · ${when(o.ai_checked_at)}` : ""}</span>
      </div>
      {c.summary && <p>{c.summary}</p>}
      <ol className="ai-steps">
        {c.steps.map((st, i) => (
          <li key={i}>
            <span className={`src src-${st.source}`}>{SOURCE_LABEL[st.source] ?? st.source}</span>
            <div><b>{st.checked}</b><div className="small">{st.learned}</div></div>
          </li>
        ))}
      </ol>
      <div className="ai-decision">
        <div><span className="small muted">Decision</span><b className={`risk-${c.risk_level}`}>{c.risk_level} risk</b>
          {c.signal && c.signal !== "none" && <span className="small"> · {SIGNAL_LABEL[c.signal] ?? c.signal}</span>}</div>
        {c.buyer_mood && MOOD_LABEL[c.buyer_mood] && <div><span className="small muted">Buyer's tone</span><b>{MOOD_LABEL[c.buyer_mood]}</b></div>}
        {c.confidence && <div><span className="small muted">Confidence</span><b>{c.confidence}</b></div>}
      </div>
      {f?.buyer_need && <p className="small"><b>What the buyer needs:</b> {f.buyer_need}</p>}
      {o.watch_status === "watching" && c.next_check_reason && (
        <p className="small"><b>Next look{c.next_check_in_hours ? ` in ${c.next_check_in_hours}h` : ""}:</b> {c.next_check_reason}</p>
      )}
      {tools.length > 0 && <p className="small muted">Tools the AI chose to call: {tools.join(" · ")}</p>}
    </section>
  );
}

function Findings({ o }: { o: Order }) {
  const f = o.findings;
  if (!f || !f.evidence?.length) return null;
  return (
    <section className="panel">
      <div className="panel-head">
        <span className="panel-title"><Icon name="search" />Evidence the AI found</span>
        <span className="tag">{f.confidence} confidence</span>
      </div>
      <ul className="facts">
        {f.evidence.map((e, i) => <li key={i}><span className={`src src-${e.source}`}>{e.source}</span>{e.fact}</li>)}
      </ul>
    </section>
  );
}

function FixDetails({ o, emailOn }: { o: Order; emailOn: boolean }) {
  const p = o.action_params!;
  return (
    <div className="fix-body">
      {o.action_type === "attach_tracking" && (
        <p>{String(p.carrier)} <code>{String(p.tracking_number)}</code> goes onto the PayPal transaction, and PayPal emails the buyer the tracking.</p>
      )}
      {(o.action_type === "partial_refund" || o.action_type === "full_refund") && (
        <>
          <p>Refund <b>{money(String(p.amount), o.currency)}</b>{p.share_of_order_pct ? ` (${String(p.share_of_order_pct)}% of the order)` : ""} through PayPal.</p>
          {p.note_to_payer && <blockquote>Note PayPal shows the buyer: “{String(p.note_to_payer)}”</blockquote>}
        </>
      )}
      {o.action_type === "ship_replacement" && (
        <p>Ship a replacement by {String(p.service ?? "Priority Mail")}. Its tracking goes onto the PayPal transaction.</p>
      )}
      {o.action_type === "message_buyer" && (
        <>
          <blockquote className="msg">“{String(p.message)}”</blockquote>
          <p className="small muted">{emailOn
            ? "Forestall emails this from your shop's address, with a link to the buyer's tracking page where they can reply."
            : "You send this message (email isn't set up). PayPal has no messaging API before a dispute, so Forestall records that it was sent."}</p>
        </>
      )}
      {o.action_justification && <p className="small"><b>Why this fix:</b> {o.action_justification}</p>}
      {o.action_model && o.action_model !== "rule" && <p className="small muted"><Icon name="sparkle" size={12} /> Fix chosen and written by AI ({o.action_model}). The backend checked every PayPal parameter.</p>}
      <p className="small muted">PayPal call: <code>{String(p.paypal_call)}</code></p>
    </div>
  );
}

function ActionPanel({ o, emailOn, onChange }: { o: Order; emailOn: boolean; onChange: () => void }) {
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
      if (o.action_type === "message_buyer" && !emailOn) {
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
      <section className="panel tone-accent">
        <div className="panel-head">
          <span className="panel-title"><Icon name="bolt" />{o.action_is_followup ? "Buyer's choice" : "Recommended fix"}: {FIX_TITLE[o.action_type]}</span>
          <span className="tag">{o.action_edited ? "Edited · " : ""}Awaiting approval</span>
        </div>
        {!editing ? <FixDetails o={o} emailOn={emailOn} /> : (
          <div className="form">
            {(o.action_type === "message_buyer" || o.action_type.endsWith("refund")) && (
              <label>
                <span className="small muted">{o.action_type === "message_buyer" ? "Message" : "Note to buyer"}</span>
                <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={5} />
              </label>
            )}
            {o.action_type === "partial_refund" && (
              <label className="inline">
                <span className="small muted">Amount ({o.currency})</span>
                <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" />
              </label>
            )}
          </div>
        )}
        {err && <p className="alert danger small">{err}</p>}
        <div className="actions">
          {!editing ? (
            <>
              <button className="btn primary" onClick={approve} disabled={!!busy}>
                <Icon name="check" size={16} />
                {busy === "approve" ? "Working…" : o.action_type === "message_buyer" ? (emailOn ? "Approve and email the buyer" : "Approve, copy and mark sent") : "Approve and run on PayPal"}
              </button>
              {o.action_type !== "attach_tracking" && o.action_type !== "ship_replacement" && (
                <button className="btn" onClick={startEdit} disabled={!!busy}>Edit</button>
              )}
              <button className="btn ghost" onClick={() => run("dismiss", () => dismissAction(id))} disabled={!!busy}>Dismiss</button>
            </>
          ) : (
            <>
              <button className="btn primary" onClick={saveEdit} disabled={!!busy}>{busy === "edit" ? "Saving…" : "Save changes"}</button>
              <button className="btn ghost" onClick={() => setEditing(false)} disabled={!!busy}>Cancel</button>
            </>
          )}
        </div>
        <p className="small muted">Nothing runs on PayPal until you approve.</p>
      </section>
    );
  }

  if (o.action_status === "executed") {
    const r = o.action_result ?? {};
    const awaiting = o.action_type === "message_buyer" && o.resolution === "awaiting_buyer_reply";
    return (
      <section className="panel tone-ok">
        <div className="panel-head">
          <span className="panel-title"><Icon name="check" />Done: {FIX_TITLE[o.action_type]}</span>
          {o.resolution && <span className="tag">{RESOLUTION_LABEL[o.resolution] ?? o.resolution}</span>}
        </div>
        {o.action_type === "attach_tracking" && <p>PayPal now holds the tracking on this transaction and has emailed the buyer.</p>}
        {o.action_type.endsWith("refund") && (
          <p>PayPal refunded <b>{money(String(r.amount), o.currency)}</b> · refund <code>{String(r.refund_id)}</code> ({String(r.status)})</p>
        )}
        {o.action_type === "ship_replacement" && <p>Replacement tracking <code>{String(r.tracking_number)}</code> is on the PayPal transaction.</p>}
        {o.action_type === "message_buyer" && <blockquote className="msg">“{String(p.message)}”</blockquote>}
        {awaiting && (
          <div className="sandbox-box">
            <div className="sandbox-label"><Icon name="chat" size={14} />Sandbox: reply as the buyer</div>
            <div className="actions">
              <button className="btn sm" onClick={() => run("reply", () => buyerReply(o.id, "1"))} disabled={!!busy}>“1”</button>
              <button className="btn sm" onClick={() => run("reply", () => buyerReply(o.id, "2"))} disabled={!!busy}>“2”</button>
              <button className="btn sm" onClick={() => run("reply", () => buyerReply(o.id, "No worries, I can wait a few more days."))} disabled={!!busy}>“I can wait”</button>
            </div>
            <div className="input-row">
              <input value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Or type a reply" aria-label="Buyer reply" />
              <button className="btn primary" onClick={() => run("reply", () => buyerReply(o.id, reply))} disabled={!!busy || !reply.trim()}>
                {busy === "reply" ? "AI is reading…" : "Send"}
              </button>
            </div>
          </div>
        )}
        {err && <p className="alert danger small">{err}</p>}
      </section>
    );
  }

  if (o.action_status === "failed") {
    return <p className="alert danger">PayPal rejected the {FIX_TITLE[o.action_type].toLowerCase()}. The timeline has the details.</p>;
  }
  if (o.action_status === "dismissed") {
    return <p className="alert neutral">You dismissed the suggested fix ({FIX_TITLE[o.action_type].toLowerCase()}).</p>;
  }
  return null;
}

function SandboxControls({ o, scenarios, epCodes, onDone }: {
  o: Order; scenarios: Scenario[]; epCodes: EpCode[]; onDone: () => void;
}) {
  const [scenario, setScenario] = useState("sim:no_tracking");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  if (o.watch_status !== "not_shipped" && o.watch_status !== "watching") return null;

  const act = async (label: string, fn: () => Promise<CheckResult>) => {
    setBusy(label);
    setNote(null);
    try {
      let r = await fn();
      // The AI is shared by everyone trying the demo; when it's rate limited, wait and check again.
      for (let attempt = 1; r.busy && attempt <= 6; attempt++) {
        const wait = Math.max(5, r.retry_in_s ?? 10);
        setNote(`The AI is busy with other orders. Trying again in ${wait} seconds (attempt ${attempt} of 6)…`);
        onDone();
        await new Promise((res) => setTimeout(res, wait * 1000));
        setNote("AI is checking again…");
        r = await checkOrder(o.id);
      }
      if (r.busy) setNote("The AI is still busy. Forestall will check this order again by itself in a few minutes.");
      else if (r.error) setNote(r.error);
      else if (r.watch_status === "protected") setNote("Delivered with proof on PayPal. This order is now protected.");
      onDone();
    } catch (e) {
      setNote(`Failed: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel sandbox">
      <div className="panel-head">
        <span className="panel-title"><Icon name="forward" />Sandbox controls</span>
        <span className="tag">Demo only</span>
      </div>
      {o.watch_status === "not_shipped" ? (
        <>
          <p className="small muted">Pick what happens to this parcel, then ship it. Forestall starts watching from here.</p>
          <div className="input-row">
            <select value={scenario} onChange={(e) => setScenario(e.target.value)} disabled={!!busy} aria-label="Delivery story">
              <optgroup label="Delivery stories (simulated carrier)">
                {scenarios.map((s) => <option key={s.id} value={`sim:${s.id}`}>{s.label}</option>)}
              </optgroup>
              {epCodes.length > 0 && (
                <optgroup label="Real carrier API: EasyPost test parcels">
                  {epCodes.map((c) => <option key={c.code} value={`ep:${c.code}`}>EasyPost · {c.label}</option>)}
                </optgroup>
              )}
            </select>
            <button className="btn primary" onClick={() => act("ship", () => shipOrder(o.id, parseChoice(scenario)))} disabled={!!busy}>
              <Icon name="box" size={16} />{busy === "ship" ? "Shipping…" : "Mark shipped"}
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="small muted">Move the clock forward to see how Forestall reacts as the delivery unfolds.</p>
          <div className="actions">
            <button className="btn primary" onClick={() => act("advance", () => advanceOrder(o.id, 24))} disabled={!!busy}>
              <Icon name="forward" size={16} />{busy === "advance" ? "AI is checking…" : "Advance 1 day"}
            </button>
            <button className="btn" onClick={() => act("check", () => checkOrder(o.id))} disabled={!!busy}>
              <Icon name="refresh" size={16} />{busy === "check" ? "Checking…" : "Check now"}
            </button>
          </div>
        </>
      )}
      {note && <p className="small muted">{note}</p>}
    </section>
  );
}

export default function OrderDetail({ o, scenarios, epCodes, emailOn, onChange }: {
  o: Order | null; scenarios: Scenario[]; epCodes: EpCode[]; emailOn: boolean; onChange: () => void;
}) {
  const [version, setVersion] = useState(0);
  if (!o) {
    return (
      <>
        <button className="back" onClick={() => go("orders")}><Icon name="back" size={16} />All orders</button>
        <Empty icon="orders" title="Order not found">It may still be loading, or it was removed.</Empty>
      </>
    );
  }
  const refresh = () => { setVersion((v) => v + 1); onChange(); };
  const carrier = o.carrier_source === "easypost" ? "EasyPost (live API)" : o.carrier_source === "simulator" ? "Simulated" : "Manual";

  return (
    <>
      <button className="back" onClick={() => go("orders")}><Icon name="back" size={16} />All orders</button>
      <div className="detail-head">
        <div>
          <div className="detail-title"><h1>{o.buyer_name ?? "Unknown buyer"}</h1><StatusBadge o={o} /></div>
          <p className="muted mono small">{o.paypal_order_id}</p>
        </div>
        <div className="detail-amount num">{money(o.amount, o.currency)}</div>
      </div>

      <dl className="facts-strip">
        <div><dt>Paid</dt><dd>{when(o.captured_at)}</dd></div>
        <div><dt>Shipped</dt><dd>{o.shipped_at ? when(o.shipped_at) : "Not yet"}</dd></div>
        <div>
          <dt>Tracking on PayPal</dt>
          <dd className={o.shipped_at ? (o.tracking_attached_at ? "text-ok" : "text-danger") : ""}>
            {!o.shipped_at ? "—" : o.tracking_attached_at ? "Yes" : "Missing"}
          </dd>
        </div>
        <div>
          <dt>Carrier data</dt>
          <dd>{!o.shipped_at ? "—" : o.tracking_public_url
            ? <a href={o.tracking_public_url} target="_blank" rel="noreferrer">{carrier} <Icon name="external" size={13} /></a> : carrier}</dd>
        </div>
        {o.watch_status === "watching" && o.next_check_at && <div><dt>Next check</dt><dd>{when(o.next_check_at)}</dd></div>}
        {o.store_platform && (
          <div><dt>Store order</dt><dd>WooCommerce #{o.store_order_id}</dd></div>
        )}
        <div>
          <dt>Buyer's tracking page</dt>
          <dd><a href={`/track/${o.track_token}`} target="_blank" rel="noreferrer">Open <Icon name="external" size={13} /></a></dd>
        </div>
      </dl>

      <div className="detail-grid">
        <div className="stack">
          {o.flag_reason && (
            <section className="panel tone-danger">
              <div className="panel-head">
                <span className="panel-title"><Icon name="alert" />{SIGNAL_LABEL[o.signal ?? ""] ?? o.signal}</span>
                <span className="tag">Flagged by AI{o.ai_model ? ` · ${o.ai_model}` : ""}</span>
              </div>
              <p className="lead">{o.flag_reason}</p>
            </section>
          )}
          <AiInvestigation o={o} />
          <Findings o={o} />
          <ActionPanel o={o} emailOn={emailOn} onChange={refresh} />
          {!o.flag_reason && !o.action_type && o.watch_status === "watching" && (
            <section className="panel tone-ok">
              <div className="panel-head"><span className="panel-title"><Icon name="check" />On track</span></div>
              <p>{o.ai_check?.reason ?? "No warning signs."} The AI looks again {o.next_check_at ? when(o.next_check_at) : "soon"}.</p>
            </section>
          )}
          <SandboxControls o={o} scenarios={scenarios} epCodes={epCodes} onDone={refresh} />
        </div>
        <section className="card">
          <div className="card-head">
            <h2>Timeline</h2>
            <span className="muted small">Every step, recorded as proof</span>
          </div>
          <Timeline id={o.id} version={version} />
        </section>
      </div>
    </>
  );
}
