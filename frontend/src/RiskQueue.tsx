import { useEffect, useMemo, useRef, useState } from "react";
import {
  AllCommunityModule, ModuleRegistry, themeQuartz,
  type CellClickedEvent, type ColDef, type GridApi, type ICellRendererParams,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { approveAction, type Order } from "./api";
import { Empty, Icon, SIGNAL_LABEL, StatusBadge, money, needsAttention, nextStep, shortDate, useMedia } from "./ui";

ModuleRegistry.registerModules([AllCommunityModule]);

// AG Grid's Quartz theme, wired to the app's color tokens so it follows light/dark mode.
const theme = themeQuartz.withParams({
  backgroundColor: "var(--surface)",
  foregroundColor: "var(--text)",
  chromeBackgroundColor: "var(--surface-2)",
  headerBackgroundColor: "var(--surface-2)",
  headerTextColor: "var(--muted)",
  borderColor: "var(--border)",
  accentColor: "var(--accent)",
  rowHoverColor: "var(--hover)",
  fontFamily: "inherit",
  fontSize: 14,
  headerFontSize: 12,
  headerFontWeight: 600,
  wrapperBorder: false,
  spacing: 7,
});

// Date filter: compare by calendar day, ignoring the time of payment.
const sameDay = (day: Date, value: Date | null) => {
  if (!value) return -1;
  const v = new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  return v < day.getTime() ? -1 : v > day.getTime() ? 1 : 0;
};

const RISK_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 };
const score = (o: Order) =>
  (o.risk_level && o.watch_status === "watching" ? RISK_RANK[o.risk_level] * 10 : 0)
  + (o.action_status === "proposed" ? 5 : 0) + (o.watch_status === "watching" ? 1 : 0);

type Filter = "attention" | "watching" | "unshipped" | "safe" | "all";
const FILTERS: [Filter, string][] = [
  ["attention", "Needs attention"],
  ["watching", "Watching"],
  ["unshipped", "Not shipped"],
  ["safe", "Protected"],
  ["all", "All orders"],
];

const matches = (o: Order, f: Filter) => {
  switch (f) {
    case "attention": return needsAttention(o);
    case "watching": return o.watch_status === "watching";
    case "unshipped": return o.watch_status === "not_shipped";
    case "safe": return o.watch_status === "protected" || o.watch_status === "closed";
    default: return true;
  }
};

function ApproveButton({ o, onDone }: { o: Order; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (o.action_status !== "proposed" || !o.action_id) return null;
  const run = async (e: React.MouseEvent) => {
    e.stopPropagation(); // don't open the order
    setBusy(true);
    setErr(null);
    try {
      if (o.action_type === "message_buyer" && o.action_params?.message) {
        try { await navigator.clipboard.writeText(String(o.action_params.message)); } catch { /* blocked */ }
      }
      const r = await approveAction(o.action_id!);
      if (r.status === "failed") setErr("PayPal rejected it");
      onDone();
    } catch (ex) {
      setErr((ex as Error).message.slice(0, 60));
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="approve">
      <button className="btn primary sm" onClick={run} disabled={busy}>{busy ? "Working…" : "Approve"}</button>
      {err && <span className="text-danger small">{err}</span>}
    </span>
  );
}

const BuyerCell = ({ data }: ICellRendererParams<Order>) => data && (
  <div className="cell-stack"><b>{data.buyer_name ?? "Unknown buyer"}</b><span className="muted mono small">{data.paypal_order_id}</span></div>
);
// The PayPal API each fix calls when it runs (actions.py).
const PAYPAL_CALL: Record<string, string> = {
  attach_tracking: "Orders v2 · POST /track",
  partial_refund: "Payments v2 · POST /refund",
  full_refund: "Payments v2 · POST /refund",
  ship_replacement: "Orders v2 · POST /track",
};
const StepCell = ({ data }: ICellRendererParams<Order>) => {
  const s = data && nextStep(data);
  const call = data?.action_type && PAYPAL_CALL[data.action_type];
  return s ? (
    <div className="cell-stack">
      <b>{s.title}</b><span className="muted small">{s.sub}</span>
      {call && <span className="paypal-call mono">PayPal {call}</span>}
    </div>
  ) : <span className="muted">—</span>;
};

// The AI's current risk call for an order: the open flag if there is one, else its latest check.
const aiRisk = (o: Order): string | null =>
  o.watch_status === "protected" || o.watch_status === "closed" ? null : o.risk_level ?? o.ai_check?.risk_level ?? null;
const ago = (iso: string) => {
  const m = Math.max(0, Math.round((Date.now() - +new Date(iso)) / 60000));
  return m < 60 ? `${m}m ago` : m < 48 * 60 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
};
const riskText = (o: Order) =>
  [aiRisk(o) ?? "no risk", o.signal && o.signal !== "none" ? SIGNAL_LABEL[o.signal] ?? o.signal : "", STAGE[o.watch_status][0]].filter(Boolean).join(" · ");
const RISK_BADGE: Record<string, string> = { high: "danger", medium: "warn", low: "info" };
const RiskCell = ({ data: o }: ICellRendererParams<Order>) => {
  if (!o) return null;
  const level = aiRisk(o);
  const conf = o.ai_check?.confidence;
  const [stage, tone] = STAGE[o.watch_status];
  const problem = o.signal && o.signal !== "none" ? SIGNAL_LABEL[o.signal] ?? o.signal : null;
  return (
    <div className="cell-stack risk-cell">
      {level
        ? <span className={`badge ${RISK_BADGE[level] ?? "neutral"}`}>{level[0].toUpperCase() + level.slice(1)} risk</span>
        : <span className={`badge ${tone}`}>{stage}</span>}
      {level && problem && <span className="small">{problem}</span>}
      <span className="muted small">
        {o.ai_checked_at ? <>AI{conf ? ` · ${conf} confidence` : ""} · {ago(o.ai_checked_at)}</> : "Not checked yet"}
      </span>
    </div>
  );
};

const aiWhy = (o: Order) => o.flag_reason ?? o.ai_check?.reason ?? "";

// Where the order is in its life, separate from the AI's risk call.
const STAGE: Record<Order["watch_status"], [string, string]> = {
  not_shipped: ["Not shipped", "neutral"], watching: ["Watching", "info"], protected: ["Protected", "ok"], closed: ["Resolved", "ok"],
};

function OrderCards({ rows, onSelect, onChange }: { rows: Order[]; onSelect: (id: string) => void; onChange: () => void }) {
  return (
    <ul className="order-cards">
      {rows.map((o) => {
        const s = nextStep(o);
        return (
          <li key={o.id}>
            <button className="order-card" onClick={() => onSelect(o.id)}>
              <div className="oc-top">
                <StatusBadge o={o} />
                <b className="num">{money(o.amount, o.currency)}</b>
              </div>
              <div className="oc-buyer">{o.buyer_name ?? "Unknown buyer"} <span className="muted small">· {shortDate(o.captured_at)}</span></div>
              {o.signal && <div className="oc-signal">{SIGNAL_LABEL[o.signal] ?? o.signal}</div>}
              {aiWhy(o) && <p className="oc-reason">{aiWhy(o)}</p>}
              <div className="oc-foot">
                {s ? <span className="small"><b>{s.title}</b> <span className="muted">· {s.sub}</span></span> : <span />}
                <Icon name="chevron" className="muted" />
              </div>
            </button>
            {o.action_status === "proposed" && <div className="oc-approve"><ApproveButton o={o} onDone={onChange} /></div>}
          </li>
        );
      })}
    </ul>
  );
}

// Fixes that can be approved from the grid: proposed, on an order still being watched.
const approvable = (o: Order) => o.action_status === "proposed" && !!o.action_id && o.watch_status === "watching";
const refundOf = (o: Order) =>
  o.action_type === "partial_refund" ? Number(o.action_params?.amount ?? 0) : o.action_type === "full_refund" ? Number(o.amount) : 0;
const atRisk = (o: Order) => aiRisk(o) === "high" || aiRisk(o) === "medium";

// The pinned totals row: one string per column, computed from the rows left after every filter.
type Summary = Record<string, string>;
function summarize(rows: Order[]): Summary {
  const risky = rows.filter(atRisk);
  const waiting = rows.filter(approvable);
  const refunds = waiting.reduce((t, o) => t + refundOf(o), 0);
  return {
    risk: `${risky.length} at risk of ${rows.length}`,
    buyer: "Totals",
    amount: money(risky.reduce((t, o) => t + Number(o.amount), 0)),
    why: risky.length ? "← order value at risk of a dispute" : "",
    fix: waiting.length ? `${waiting.length} waiting${refunds ? ` · ${money(refunds)} refunds` : ""}` : "No fixes waiting",
  };
}
// The pinned totals row has Summary data, not an Order: give every column a value and a plain cell for it.
function withSummaryRow(cols: ColDef<Order>[]): ColDef<Order>[] {
  return cols.map((c) => ({
    ...c,
    valueGetter: (p) => p.node?.rowPinned
      ? (p.data as unknown as Summary)[c.colId!] ?? ""
      : typeof c.valueGetter === "function" ? c.valueGetter(p) : undefined,
    valueFormatter: (p) => (p.node?.rowPinned ? String(p.value ?? "") : typeof c.valueFormatter === "function" ? c.valueFormatter(p) : String(p.value ?? "")),
    cellRendererSelector: (p) => (p.node.rowPinned ? { component: SumCell } : undefined),
  }));
}
const SumCell = ({ value }: ICellRendererParams) => <b className="sum-cell">{value as string}</b>;

// Plain-text values for CSV export (the grid's own values are sort keys and rich cells).
const EXPORT: Record<string, (o: Order) => string | number> = {
  risk: riskText,
  confidence: (o) => o.ai_check?.confidence ?? "",
  buyer: (o) => `${o.buyer_name ?? "Unknown buyer"} (${o.paypal_order_id})`,
  amount: (o) => `${o.amount} ${o.currency}`,
  why: aiWhy,
  fix: (o) => { const s = nextStep(o); return s ? `${s.title} (${s.sub})` : ""; },
  call: (o) => (o.action_type && PAYPAL_CALL[o.action_type] ? `PayPal ${PAYPAL_CALL[o.action_type]}` : ""),
  paid: (o) => o.captured_at,
};

function ApproveSelected({ selected, onDone }: { selected: Order[]; onDone: () => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => { if (!selected.length) setAsking(false); }, [selected.length]);
  const refunds = selected.reduce((t, o) => t + refundOf(o), 0);
  const run = async () => {
    setBusy(true);
    let ok = 0, failed = 0;
    for (const o of selected) {
      try { (await approveAction(o.action_id!)).status === "failed" ? failed++ : ok++; } catch { failed++; }
    }
    setNote(`${ok} of ${selected.length} fixes ran on PayPal${failed ? `, ${failed} failed (see each order's timeline)` : ""}.`);
    setBusy(false);
    setAsking(false);
    onDone();
  };
  if (!selected.length) return note ? <span className="small muted">{note}</span> : <span className="small muted">Tick fixes waiting for approval to run several at once.</span>;
  return asking ? (
    <span className="confirm-inline small">
      Run <b>{selected.length}</b> fix{selected.length > 1 ? "es" : ""} on PayPal{refunds ? <>, including <b>{money(refunds)}</b> in refunds</> : null}?
      <button className="btn primary sm" onClick={run} disabled={busy}>{busy ? "Running…" : "Run"}</button>
      <button className="btn ghost sm" onClick={() => setAsking(false)} disabled={busy}>Cancel</button>
    </span>
  ) : (
    <button className="btn primary sm" onClick={() => { setNote(null); setAsking(true); }}>
      <Icon name="check" size={15} />Approve selected ({selected.length})
    </button>
  );
}

export default function RiskQueue({ orders, onSelect, onChange }: {
  orders: Order[]; onSelect: (id: string) => void; onChange: () => void;
}) {
  const [filter, setFilter] = useState<Filter>(() => (orders.some(needsAttention) ? "attention" : "all"));
  const [search, setSearch] = useState("");
  const narrow = useMedia("(max-width: 760px)");
  const api = useRef<GridApi<Order> | null>(null);
  const [selected, setSelected] = useState<Order[]>([]);
  const [summary, setSummary] = useState<Summary>({});

  // Keep the queue live: re-read orders every 30s while the tab is visible, so AI changes flash in.
  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === "visible") onChange(); }, 30000);
    return () => clearInterval(t);
  }, [onChange]);

  const counts = useMemo(
    () => Object.fromEntries(FILTERS.map(([f]) => [f, orders.filter((o) => matches(o, f)).length])) as Record<Filter, number>,
    [orders],
  );
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return orders
      .filter((o) => matches(o, filter))
      .filter((o) => !q || [o.buyer_name, o.paypal_order_id, o.flag_reason, o.signal && SIGNAL_LABEL[o.signal]]
        .some((v) => v?.toLowerCase().includes(q)) || aiWhy(o).toLowerCase().includes(q))
      .sort((a, b) => score(b) - score(a) || +new Date(b.captured_at) - +new Date(a.captured_at));
  }, [orders, filter, search]);

  const columns = useMemo<ColDef<Order>[]>(() => withSummaryRow([
    {
      colId: "risk", headerName: "AI risk", width: 210, cellRenderer: RiskCell, autoHeight: true, enableCellChangeFlash: true,
      valueGetter: (p) => (p.data ? score(p.data) : 0), sort: "desc",
      // Filter on the words in the cell: level, problem and stage (e.g. "high", "stalled", "protected").
      filter: "agTextColumnFilter", filterValueGetter: (p) => (p.data ? riskText(p.data) : ""),
      headerTooltip: "The AI's latest risk call, the problem it found, its confidence and when it last checked",
    },
    { colId: "buyer", headerName: "Buyer · order", minWidth: 160, flex: 1, cellRenderer: BuyerCell, autoHeight: true,
      valueGetter: (p) => p.data?.buyer_name ?? "", filter: "agTextColumnFilter" },
    {
      colId: "amount", headerName: "Amount", width: 115, type: "rightAligned", cellClass: "num",
      valueGetter: (p) => Number(p.data?.amount ?? 0), filter: "agNumberColumnFilter",
      valueFormatter: (p) => (p.data ? money(p.data.amount, p.data.currency) : ""),
    },
    { colId: "why", headerName: "Why (AI)", valueGetter: (p) => (p.data ? aiWhy(p.data) : ""), filter: "agTextColumnFilter",
      minWidth: 230, flex: 2, wrapText: true, autoHeight: true, cellClass: "cell-wrap" },
    { colId: "fix", headerName: "AI fix · PayPal call", width: 200, cellRenderer: StepCell, autoHeight: true, sortable: false,
      enableCellChangeFlash: true, valueGetter: (p) => (p.data ? `${p.data.action_type}:${p.data.action_status}` : "") },
    { colId: "approve", headerName: "", width: 105, sortable: false,
      cellRenderer: (p: ICellRendererParams<Order>) => p.data && <ApproveButton o={p.data} onDone={onChange} /> },
    { colId: "paid", headerName: "Paid", width: 100, valueGetter: (p) => (p.data ? new Date(p.data.captured_at) : null),
      filter: "agDateColumnFilter", filterParams: { comparator: sameDay },
      valueFormatter: (p) => (p.value ? shortDate((p.value as Date).toISOString()) : "") },
  ]), [onChange]);

  const exportCsv = () => api.current?.exportDataAsCsv({
    fileName: `forestall-risk-queue-${new Date().toISOString().slice(0, 10)}.csv`,
    skipPinnedBottom: true,
    columnKeys: ["risk", "buyer", "amount", "why", "fix", "paid"],
    processHeaderCallback: (p) => p.column.getColDef().headerName ?? "",
    processCellCallback: (p) => {
      const o = p.node?.data as Order | undefined;
      const id = p.column.getColId();
      if (!o) return "";
      if (id === "risk") return `${riskText(o)}${o.ai_check?.confidence ? ` (AI ${EXPORT.confidence(o)} confidence)` : ""}`;
      if (id === "fix") return [EXPORT.fix(o), EXPORT.call(o)].filter(Boolean).join(" · ");
      return String(EXPORT[id]?.(o) ?? "");
    },
  });

  return (
    <div className="queue">
      <div className="queue-bar">
        <div className="tabs" role="tablist">
          {FILTERS.map(([f, label]) => (
            <button key={f} role="tab" aria-selected={filter === f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
              {label}<span className="count">{counts[f]}</span>
            </button>
          ))}
        </div>
        <label className="search">
          <Icon name="search" size={16} />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search buyer, order, reason" aria-label="Search orders" />
        </label>
      </div>

      {rows.length === 0 ? (
        <Empty icon={filter === "attention" ? "shield" : "orders"} title={filter === "attention" ? "Nothing needs you right now" : "No orders here"}>
          {filter === "attention" ? "Forestall keeps watching and will flag anything that starts to go wrong." : "Try another filter or clear the search."}
        </Empty>
      ) : narrow ? (
        <OrderCards rows={rows} onSelect={onSelect} onChange={onChange} />
      ) : (
        <div className="grid-wrap">
          <div className="grid-tools">
            <ApproveSelected selected={selected} onDone={() => { api.current?.deselectAll(); onChange(); }} />
            <button className="btn ghost sm" onClick={exportCsv}><Icon name="download" size={15} />Export CSV</button>
          </div>
          <AgGridReact<Order>
            theme={theme}
            rowData={rows}
            columnDefs={columns}
            onGridReady={(e) => { api.current = e.api; }}
            rowSelection={{
              mode: "multiRow", checkboxes: true, headerCheckbox: true, hideDisabledCheckboxes: true,
              enableClickSelection: false, isRowSelectable: (n) => !!n.data && approvable(n.data),
            }}
            selectionColumnDef={{ width: 48, pinned: "left" }}
            onSelectionChanged={(e) => setSelected(e.api.getSelectedRows().filter(approvable))}
            pinnedBottomRowData={[summary]}
            onModelUpdated={(e) => {
              const shown: Order[] = [];
              e.api.forEachNodeAfterFilter((n) => { if (n.data) shown.push(n.data); });
              const next = summarize(shown);
              setSummary((cur) => (JSON.stringify(cur) === JSON.stringify(next) ? cur : next));
            }}
            getRowClass={(p) => (p.node.rowPinned ? "grid-sum" : undefined)}
            defaultColDef={{ sortable: true, resizable: true, suppressMovable: true }}
            getRowId={(p) => (p.data as Partial<Order>).id ?? "summary"}
            domLayout="autoHeight"
            pagination={rows.length > 15}
            paginationPageSize={15}
            paginationPageSizeSelector={[15, 30, 60]}
            rowClass="grid-row"
            onCellClicked={(e: CellClickedEvent<Order>) => {
              // Open the order, except from the totals row, the checkbox column or the Approve button.
              const col = e.column.getColId();
              if (!e.node.rowPinned && e.data && col !== "approve" && !col.startsWith("ag-Grid-")) onSelect(e.data.id);
            }}
          />
        </div>
      )}
    </div>
  );
}
