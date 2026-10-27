import { useEffect, useState } from "react";
import {
  getEmail, getStores, testEmail, testStore, type EmailStatus, type StorePlatform, type StoreStatus,
} from "./api";
import { Icon, PageHead, go, when } from "./ui";

const STORE: Record<StorePlatform, { name: string; steps: string[]; env: string }> = {
  woocommerce: {
    name: "WooCommerce",
    env: "WOOCOMMERCE_WEBHOOK_SECRET",
    steps: [
      "Use WooCommerce PayPal Payments and the Shipment Tracking plugin (it adds tracking numbers to orders).",
      "Open WooCommerce → Settings → Advanced → Webhooks and add one for “Order updated” with the URL below.",
      "Choose a secret, save it there and set the same value as WOOCOMMERCE_WEBHOOK_SECRET.",
    ],
  },
};

function Copy({ value }: { value: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="copy">
      <code>{value}</code>
      <button className="btn sm" onClick={async () => {
        try { await navigator.clipboard.writeText(value); setDone(true); setTimeout(() => setDone(false), 1500); } catch { /* blocked */ }
      }}>{done ? "Copied" : "Copy"}</button>
    </div>
  );
}

function StoreCard({ s, onDone }: { s: StoreStatus; onDone: () => void }) {
  const meta = STORE[s.platform];
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string; orderId?: string } | null>(null);
  const live = !!s.last_event && s.last_event.valid;
  const test = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await testStore(s.platform);
      const res = r.result;
      if (res.shipped) setMsg({ ok: true, orderId: res.order_id,
        text: `${meta.name} fulfilment received. Order matched by ${res.matched_by}, marked shipped and tracking attached to PayPal. Forestall is watching it now.` });
      else setMsg({ ok: false, text: res.ignored ?? "The order was linked but not shipped." });
      onDone();
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>{meta.name}</h2>
          <p className="muted small">Shipped orders arrive with their tracking number, so nobody copies it by hand.</p>
        </div>
        <span className={`badge ${s.secret_set ? (live ? "ok" : "info") : "neutral"}`}>
          {s.secret_set ? (live ? "Connected" : "Ready") : "Not set up"}
        </span>
      </div>
      <dl className="facts-strip compact">
        <div><dt>Orders linked</dt><dd>{s.linked_orders}</dd></div>
        <div><dt>Last event</dt><dd>{s.last_event ? `${s.last_event.event ?? "event"} · ${when(s.last_event.received_at)}` : "None yet"}</dd></div>
        <div><dt>Signing secret</dt><dd>{s.secret_set ? "Set" : <span className="mono">{meta.env}</span>}</dd></div>
      </dl>
      <ol className="setup">{meta.steps.map((t, i) => <li key={i}>{t}</li>)}</ol>
      <Copy value={s.webhook_url} />
      <div className="actions">
        <button className="btn primary" onClick={test} disabled={busy}>
          <Icon name="box" size={16} />{busy ? "Sending…" : "Send a test fulfilment"}
        </button>
      </div>
      <p className="small muted">The test sends a {meta.name}-shaped fulfilment for your newest unshipped order through the same code a real webhook uses.</p>
      {msg && (
        <p className={`alert ${msg.ok ? "ok" : "danger"} small`}>
          <span style={{ flex: 1 }}>{msg.text}</span>
          {msg.orderId && <button className="btn sm" onClick={() => go(`orders/${msg.orderId}`)}>Open order</button>}
        </p>
      )}
    </section>
  );
}

function EmailCard({ e }: { e: EmailStatus | null }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const on = e?.status === "ok";
  const test = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await testEmail();
      setMsg({ ok: true, text: `Test email sent to ${r.to}.` });
    } catch (err) {
      setMsg({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Buyer email (SMTP)</h2>
          <p className="muted small">Forestall emails buyers from your address: a shipping confirmation with their tracking page, and any message you approve.</p>
        </div>
        <span className={`badge ${on ? "ok" : "neutral"}`}>{on ? "Connected" : "Not set up"}</span>
      </div>
      {e && (
        <dl className="facts-strip compact">
          <div><dt>Sends from</dt><dd>{e.from ?? <span className="mono">SMTP_FROM</span>}</dd></div>
          <div><dt>Server</dt><dd>{e.host ?? <span className="mono">SMTP_HOST</span>}</dd></div>
          <div><dt>Shipping emails</dt><dd>{e.auto_shipping_email ? "Automatic" : "Off"}</dd></div>
          {e.redirect_to && <div><dt>Sandbox inbox</dt><dd>{e.redirect_to}</dd></div>}
        </dl>
      )}
      {!on && (
        <p className="small muted">
          Set <span className="mono">SMTP_HOST</span>, <span className="mono">SMTP_PORT</span>, <span className="mono">SMTP_USER</span>,{" "}
          <span className="mono">SMTP_PASSWORD</span> and <span className="mono">SMTP_FROM</span>. In the sandbox, also set{" "}
          <span className="mono">EMAIL_REDIRECT_TO</span> to your own inbox, because sandbox buyer addresses don't receive mail.
        </p>
      )}
      <div className="actions">
        <button className="btn" onClick={test} disabled={busy || !on}><Icon name="chat" size={16} />{busy ? "Sending…" : "Send a test email"}</button>
      </div>
      {msg && <p className={`alert ${msg.ok ? "ok" : "danger"} small`}>{msg.text}</p>}
    </section>
  );
}

export default function Connect({ onChange }: { onChange: () => void }) {
  const [stores, setStores] = useState<StoreStatus[] | null>(null);
  const [email, setEmail] = useState<EmailStatus | null>(null);
  const load = () => {
    getStores().then(setStores).catch(() => setStores([]));
    getEmail().then(setEmail).catch(() => {});
  };
  useEffect(load, []);
  return (
    <>
      <PageHead title="Connect" sub="Link your store so shipped orders flow in by themselves, and email buyers from your own address." />
      <div className="connect-grid">
        {stores === null ? <div className="skeleton" style={{ height: 320 }} />
          : stores.map((s) => <StoreCard key={s.platform} s={s} onDone={() => { load(); onChange(); }} />)}
        <EmailCard e={email} />
      </div>
    </>
  );
}
