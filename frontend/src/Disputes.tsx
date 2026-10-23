import { useState } from "react";
import {
  draftEvidence, editDisputeNotes, evidencePdfUrl, submitDispute, syncDisputes, type Dispute,
} from "./api";

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
    <li>
      <div className="row">
        <strong>{d.buyer_name ?? "Unlinked order"} · {REASON[d.reason] ?? d.reason}</strong>
        <span className={`badge ${d.status === "RESOLVED" ? "protected" : "high"}`}>{d.status.replace(/_/g, " ")}</span>
      </div>
      <div className="muted small">
        Dispute <code>{d.paypal_dispute_id}</code>{d.paypal_order_id && <> · order <code>{d.paypal_order_id}</code></>}
        {d.amount && <> · ${Number(d.amount).toFixed(2)}</>}{d.stage && <> · {d.stage.toLowerCase()}</>}
        {d.due_at && <> · respond by {new Date(d.due_at).toLocaleDateString()}</>}
        {d.outcome && <> · outcome <b>{d.outcome}</b></>}
      </div>
      {d.messages.length > 0 && (
        <div className="small quote">Buyer: “{d.messages[d.messages.length - 1].content}”</div>
      )}

      {ev ? (
        <div className={`fix ${d.evidence_status === "submitted" ? "done" : ""}`}>
          <div className="row">
            <b>{d.evidence_status === "submitted" ? "✓ Response submitted to PayPal" : "Evidence drafted by AI"}</b>
            <span className="chip">{ev.strength} case</span>
          </div>
          <ul className="evidence">
            {ev.key_facts.map((k, i) => <li key={i} className="small">• {k}</li>)}
          </ul>
          {!editing ? (
            <div className="small msg">{ev.notes}</div>
          ) : (
            <div className="edit"><textarea rows={8} value={draft} onChange={(e) => setDraft(e.target.value)} /></div>
          )}
          <div className="small muted">
            {ev.strength_reason} · Sends: {ev.evidences.map((e) => e.evidence_type.replace(/_/g, " ").toLowerCase()).join(", ")}
            {" "}+ <a href={evidencePdfUrl(d.paypal_dispute_id)} target="_blank" rel="noreferrer">evidence report (PDF)</a>
          </div>
          {d.evidence_status !== "submitted" && (
            <div className="sim">
              {!editing ? (
                <>
                  <button onClick={() => run("submit", () => submitDispute(d.paypal_dispute_id))} disabled={!!busy}>
                    {busy === "submit" ? "Submitting…" : ev.recommended_response === "accept_claim" ? "Approve · accept claim" : "Approve · submit to PayPal"}
                  </button>
                  <button className="secondary" onClick={() => { setDraft(ev.notes); setEditing(true); }} disabled={!!busy}>Edit</button>
                  <button className="secondary" onClick={() => run("redraft", () => draftEvidence(d.paypal_dispute_id))} disabled={!!busy}>
                    {busy === "redraft" ? "Drafting…" : "Redraft"}
                  </button>
                </>
              ) : (
                <>
                  <button onClick={() => run("save", () => editDisputeNotes(d.paypal_dispute_id, draft))} disabled={!!busy}>Save</button>
                  <button className="secondary" onClick={() => setEditing(false)}>Cancel</button>
                </>
              )}
            </div>
          )}
        </div>
      ) : (
        d.order_id && (
          <div className="sim">
            <button onClick={() => run("draft", () => draftEvidence(d.paypal_dispute_id))} disabled={!!busy}>
              {busy === "draft" ? "AI is drafting…" : "Draft evidence"}
            </button>
          </div>
        )
      )}
      {note && <div className="good small">{note}</div>}
      {err && <div className="warn small">{err}</div>}
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
      setMsg(`Checked PayPal: ${r.synced.length} dispute(s).`);
      onChange();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card">
      <div className="row">
        <h2>3 · Disputes ({items.length})</h2>
        <button className="secondary" onClick={sync} disabled={busy} style={{ marginTop: 0 }}>
          {busy ? "Checking…" : "Check PayPal"}
        </button>
      </div>
      <p className="muted small">
        If a buyer opens a dispute anyway, PayPal tells Forestall instantly (webhook). The AI drafts the response from
        everything Forestall recorded, and you approve before it's submitted.
      </p>
      {msg && <p className="small muted">{msg}</p>}
      {items.length === 0 && <p className="muted small">No disputes. That's the goal.</p>}
      <ul className="orders">{items.map((d) => <DisputeCard key={d.paypal_dispute_id} d={d} onChange={onChange} />)}</ul>
    </section>
  );
}
