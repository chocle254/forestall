import { useEffect, useState } from "react";
import { getTrack, sendTrackReply, type TrackInfo } from "./api";
import { Icon, money, when } from "./ui";

// What each offer in the seller's message means, as a one-tap reply the AI can read.
const OFFER_REPLY: Record<string, [string, string]> = {
  replacement: ["Send me a replacement", "I'd like a replacement, please."],
  partial_refund: ["I'll take the partial refund", "I'll take the partial refund, thank you."],
  full_refund: ["I'd like a full refund", "I'd like a full refund, please."],
};

const STEPS = ["Ordered", "Shipped", "In transit", "Delivered"];

function progress(t: TrackInfo): number {
  if (t.delivered) return 3;
  if (!t.shipped_at) return 0;
  return t.events.length > 1 ? 2 : 1;
}

export default function TrackPage({ token }: { token: string }) {
  const [t, setT] = useState<TrackInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  const load = () => getTrack(token).then(setT).catch((e) => setErr((e as Error).message));
  useEffect(() => { load(); }, [token]);
  useEffect(() => { if (t) document.title = `Your order · ${t.store_name}`; }, [t]);

  const send = async (msg: string) => {
    if (!msg.trim()) return;
    setBusy(true);
    try {
      await sendTrackReply(token, msg.trim());
      setText("");
      setSent(true);
      await load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (err && !t) {
    return (
      <div className="track"><div className="track-card">
        <h1>We couldn't find that order</h1>
        <p className="muted">Check the link in your email, or contact the shop.</p>
      </div></div>
    );
  }
  if (!t) return <div className="track"><div className="track-card skeleton" style={{ height: 360 }} /></div>;
  const step = progress(t);

  return (
    <div className="track">
      <header className="track-brand">{t.store_name}</header>
      <div className="track-card">
        <p className="muted small">{t.buyer_first_name ? `Hi ${t.buyer_first_name}, here's your order` : "Your order"} · {money(t.amount, t.currency)}</p>
        <h1>{t.headline}</h1>
        <ol className="progress" aria-label="Delivery progress">
          {STEPS.map((s, i) => (
            <li key={s} className={i <= step ? "done" : ""} aria-current={i === step ? "step" : undefined}>
              <span className="pdot">{i <= step && <Icon name="check" size={12} />}</span><span>{s}</span>
            </li>
          ))}
        </ol>
        {t.tracking_number && (
          <p className="small">
            {t.carrier ?? "Carrier"} <span className="mono">{t.tracking_number}</span>
            {t.carrier_url && <> · <a href={t.carrier_url} target="_blank" rel="noreferrer">Carrier page <Icon name="external" size={12} /></a></>}
          </p>
        )}
      </div>

      {t.messages.length > 0 && (
        <div className="track-card">
          <h2>Messages</h2>
          <ul className="chat">
            {t.messages.map((m, i) => (
              <li key={i} className={m.from}>
                <span className="who small muted">{m.from === "seller" ? t.store_name : "You"} · {when(m.at)}</span>
                <div className="bubble">{m.text}</div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="track-card">
        <h2>{t.awaiting_reply ? "Reply to the shop" : "Something wrong? Message the shop"}</h2>
        {sent && <p className="alert ok small"><Icon name="check" size={16} />Thanks, the shop has your message.</p>}
        {t.awaiting_reply && t.offers.length > 0 && (
          <div className="actions">
            {t.offers.map((o) => OFFER_REPLY[o] && (
              <button key={o} className="btn" disabled={busy} onClick={() => send(OFFER_REPLY[o][1])}>{OFFER_REPLY[o][0]}</button>
            ))}
            <button className="btn" disabled={busy} onClick={() => send("No worries, I'm happy to wait a few more days.")}>I'm happy to wait</button>
          </div>
        )}
        <div className="form">
          <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} maxLength={1000}
                    placeholder="Write your message" aria-label="Your message" />
          <div><button className="btn primary" disabled={busy || !text.trim()} onClick={() => send(text)}>{busy ? "Sending…" : "Send message"}</button></div>
        </div>
        {err && <p className="alert danger small">{err}</p>}
      </div>

      {t.events.length > 0 && (
        <div className="track-card">
          <h2>Tracking history</h2>
          <ol className="timeline">
            {t.events.map((e, i) => (
              <li key={i} className={i === 0 ? "tone-accent" : ""}>
                <span className="tl-ico"><Icon name={e.status === "Delivered" ? "pin" : "truck"} size={14} /></span>
                <div className="tl-body">
                  <div className="tl-head"><b>{e.status}</b><time className="muted small">{when(e.at)}</time></div>
                  {e.location && <div className="small muted">{e.location}</div>}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
      <p className="track-foot small muted">Order updates powered by Forestall</p>
    </div>
  );
}
