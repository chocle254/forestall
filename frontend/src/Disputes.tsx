import { useState } from "react";
import {
  draftEvidence, editDisputeNotes, evidencePdfUrl, submitDispute, syncDisputes, type Dispute,
} from "./api";
import { Empty, Icon, PageHead, money, shortDate } from "./ui";

const REASON: Record<string, string> = {
  MERCHANDISE_OR_SERVICE_NOT_RECEIVED: "Item not received",
  MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED: "Not as described",
  UNAUTHORISED: "Unauthorised payment",
};

function DisputeCard({ d, onChange }: { d: Dispute; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const ev = d.evidence;
  const resolved = d.status === "RESOLVED";

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setErr(null);
    setNote(null);
    try {
      const r = (await fn()) as { status?: string; how?: string; paypal_error?: unknown };
      if (r?.status === "failed") setErr(`PayPal rejected it: ${JSON.stringify(r.paypal_error).slice(0, 240)}`);
      else if (r?.how) setNote(`Done: Forestall ${r.how}.`);
      setEditing(false);
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <li className="card dispute">
      <div className="card-head">
        <div>
          <h2>{REASON[d.reason] ?? d.reason}</h2>
          <p className="muted small">{d.buyer_name ?? "Unlinked order"}{d.amount && <> · <span className="num">{money(d.amount, d.currency ?? "USD")}</span></>}</p>
        </div>
        <span className={`badge ${resolved ? "ok" : "danger"}`}>{d.status.replace(/_/g, " ").toLowerCase()}</span>
      </div>
      <dl className="facts-strip compact">
        <div><dt>Dispute</dt><dd className="mono">{d.paypal_dispute_id}</dd></div>
        {d.paypal_order_id && <div><dt>Order</dt><dd className="mono">{d.paypal_order_id}</dd></div>}
        {d.stage && <div><dt>Stage</dt><dd>{d.stage.toLowerCase()}</dd></div>}
        {d.due_at && <div><dt>Respond by</dt><dd>{shortDate(d.due_at)}</dd></div>}
        {d.outcome && <div><dt>Outcome</dt><dd>{d.outcome}</dd></div>}
      </dl>
      {d.messages.length > 0 && <blockquote>Buyer: “{d.messages[d.messages.length - 1].content}”</blockquote>}

      {ev ? (
        <section className={`panel ${d.evidence_status === "submitted" ? "tone-ok" : "tone-ai"}`}>
          <div className="panel-head">
            <span className="panel-title">
              <Icon name={d.evidence_status === "submitted" ? "check" : "sparkle"} />
              {d.evidence_status === "submitted" ? "Response submitted to PayPal" : "Evidence drafted by AI"}
            </span>
            <span className="tag">{ev.strength} case</span>
          </div>
          <ul className="facts">{ev.key_facts.map((k, i) => <li key={i}>{k}</li>)}</ul>
          {!editing ? <p className="msg">{ev.notes}</p> : (
            <div className="form"><textarea rows={8} value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Evidence notes" /></div>
          )}
          <p className="small muted">
            {ev.strength_reason} · Sends: {ev.evidences.map((e) => e.evidence_type.replace(/_/g, " ").toLowerCase()).join(", ")}
          </p>
          <a className="btn sm" href={evidencePdfUrl(d.paypal_dispute_id)} target="_blank" rel="noreferrer">
            <Icon name="doc" size={15} />Evidence report (PDF)
          </a>
          {d.evidence_status !== "submitted" && (
            <div className="actions">
              {!editing ? (
                <>
                  <button className="btn primary" onClick={() => run("submit", () => submitDispute(d.paypal_dispute_id))} disabled={!!busy}>
                    <Icon name="check" size={16} />
                    {busy === "submit" ? "Submitting…" : ev.recommended_response === "accept_claim" ? "Approve and accept claim" : "Approve and submit to PayPal"}
                  </button>
                  <button className="btn" onClick={() => { setDraft(ev.notes); setEditing(true); }} disabled={!!busy}>Edit</button>
                  <button className="btn ghost" onClick={() => run("redraft", () => draftEvidence(d.paypal_dispute_id))} disabled={!!busy}>
                    {busy === "redraft" ? "Drafting…" : "Redraft"}
                  </button>
                </>
              ) : (
                <>
                  <button className="btn primary" onClick={() => run("save", () => editDisputeNotes(d.paypal_dispute_id, draft))} disabled={!!busy}>Save</button>
                  <button className="btn ghost" onClick={() => setEditing(false)}>Cancel</button>
                </>
              )}
            </div>
          )}
        </section>
      ) : (
        d.order_id && (
          <div className="actions">
            <button className="btn primary" onClick={() => run("draft", () => draftEvidence(d.paypal_dispute_id))} disabled={!!busy}>
              <Icon name="sparkle" size={16} />{busy === "draft" ? "AI is drafting…" : "Draft evidence with AI"}
            </button>
          </div>
        )
      )}
      {note && <p className="alert ok small">{note}</p>}
      {err && <p className="alert danger small">{err}</p>}
    </li>
  );
}

export default function Disputes({ items, onChange }: { items: Dispute[]; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const sync = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await syncDisputes();
      setMsg(`Checked PayPal: ${r.synced.length} dispute(s) found.`);
      onChange();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHead title="Disputes" sub="If a buyer opens a dispute anyway, PayPal tells Forestall straight away. The AI drafts your response from everything Forestall recorded, and you approve it before it goes to PayPal.">
        <button className="btn" onClick={sync} disabled={busy}><Icon name="refresh" size={16} />{busy ? "Checking…" : "Check PayPal"}</button>
      </PageHead>
      {msg && <p className="alert neutral small">{msg}</p>}
      {items.length === 0
        ? <Empty icon="shield" title="No disputes">That's the goal. Forestall works to keep it this way.</Empty>
        : <ul className="stack plain">{items.map((d) => <DisputeCard key={d.paypal_dispute_id} d={d} onChange={onChange} />)}</ul>}
    </>
  );
}
