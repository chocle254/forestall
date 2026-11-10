import { useMemo, useState } from "react";
import {
  AllCommunityModule, ModuleRegistry, themeQuartz,
  type ColDef, type ICellRendererParams, type RowClickedEvent,
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
const RISK_BADGE: Record<string, string> = { high: "danger", medium: "warn", low: "info" };
const RiskCell = ({ data: o }: ICellRendererParams<Order>) => {
  if (!o) return null;
  const level = aiRisk(o);
  if (!level) return <span className="muted small">{o.watch_status === "not_shipped" ? "Not checked yet" : "No risk"}</span>;
  const conf = o.ai_check?.confidence;
  return (
    <div className="cell-stack">
      <span className={`badge ${RISK_BADGE[level] ?? "neutral"}`}>{level[0].toUpperCase() + level.slice(1)} risk</span>
      <span className="muted small">AI{conf ? ` · ${conf} confidence` : ""}{o.ai_checked_at ? ` · ${ago(o.ai_checked_at)}` : ""}</span>
    </div>
  );
};

const aiWhy = (o: Order) => o.flag_reason ?? o.ai_check?.reason ?? "";

// Where the order is in its life, separate from the AI's risk call.
const STAGE: Record<Order["watch_status"], [string, string]> = {
  not_shipped: ["Not shipped", "neutral"], watching: ["Watching", "info"], protected: ["Protected", "ok"], closed: ["Resolved", "ok"],
};
const StageCell = ({ data: o }: ICellRendererParams<Order>) => {
  if (!o) return null;
  const [label, tone] = STAGE[o.watch_status];
  return <span className={`badge ${tone}`}>{label}</span>;
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

export default function RiskQueue({ orders, onSelect, onChange }: {
  orders: Order[]; onSelect: (id: string) => void; onChange: () => void;
}) {
  const [filter, setFilter] = useState<Filter>(() => (orders.some(needsAttention) ? "attention" : "all"));
  const [search, setSearch] = useState("");
  const narrow = useMedia("(max-width: 760px)");

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

  const columns = useMemo<ColDef<Order>[]>(() => [
    {
      headerName: "AI risk", width: 190, cellRenderer: RiskCell, autoHeight: true,
      valueGetter: (p) => (p.data ? score(p.data) : 0), sort: "desc",
      filter: "agTextColumnFilter", filterValueGetter: (p) => (p.data ? aiRisk(p.data) ?? "none" : ""),
      headerTooltip: "The AI's latest risk call on this order, with its confidence and when it last checked",
    },
    { headerName: "Stage", width: 120, cellRenderer: StageCell, valueGetter: (p) => p.data?.watch_status ?? "",
      filter: "agTextColumnFilter" },
    { headerName: "Buyer · order", minWidth: 180, flex: 1, cellRenderer: BuyerCell, autoHeight: true,
      valueGetter: (p) => p.data?.buyer_name ?? "", filter: "agTextColumnFilter" },
    {
      headerName: "Amount", width: 110, type: "rightAligned", cellClass: "num",
      valueGetter: (p) => Number(p.data?.amount ?? 0), filter: "agNumberColumnFilter",
      valueFormatter: (p) => (p.data ? money(p.data.amount, p.data.currency) : ""),
    },
    { headerName: "Problem", minWidth: 160, filter: "agTextColumnFilter", valueGetter: (p) => (p.data?.signal ? SIGNAL_LABEL[p.data.signal] ?? p.data.signal : "—") },
    { headerName: "Why (AI)", valueGetter: (p) => (p.data ? aiWhy(p.data) : ""), filter: "agTextColumnFilter", minWidth: 260, flex: 2, wrapText: true, autoHeight: true, cellClass: "cell-wrap" },
    { headerName: "AI fix · PayPal call", minWidth: 210, cellRenderer: StepCell, autoHeight: true, sortable: false },
    { headerName: "", width: 120, sortable: false,
      cellRenderer: (p: ICellRendererParams<Order>) => p.data && <ApproveButton o={p.data} onDone={onChange} /> },
    { headerName: "Paid", width: 110, valueGetter: (p) => (p.data ? new Date(p.data.captured_at) : null),
      filter: "agDateColumnFilter", filterParams: { comparator: sameDay },
      valueFormatter: (p) => (p.value ? shortDate((p.value as Date).toISOString()) : "") },
  ], [onChange]);

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
          <AgGridReact<Order>
            theme={theme}
            rowData={rows}
            columnDefs={columns}
            defaultColDef={{ sortable: true, resizable: true, suppressMovable: true }}
            getRowId={(p) => p.data.id}
            domLayout="autoHeight"
            pagination={rows.length > 15}
            paginationPageSize={15}
            paginationPageSizeSelector={[15, 30, 60]}
            rowClass="grid-row"
            onRowClicked={(e: RowClickedEvent<Order>) => e.data && onSelect(e.data.id)}
          />
        </div>
      )}
    </div>
  );
}
