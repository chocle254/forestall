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
const StepCell = ({ data }: ICellRendererParams<Order>) => {
  const s = data && nextStep(data);
  return s ? <div className="cell-stack"><b>{s.title}</b><span className="muted small">{s.sub}</span></div> : <span className="muted">—</span>;
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
              {o.flag_reason && <p className="oc-reason">{o.flag_reason}</p>}
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
        .some((v) => v?.toLowerCase().includes(q)))
      .sort((a, b) => score(b) - score(a) || +new Date(b.captured_at) - +new Date(a.captured_at));
  }, [orders, filter, search]);

  const columns = useMemo<ColDef<Order>[]>(() => [
    {
      headerName: "Status", width: 140, cellRenderer: (p: ICellRendererParams<Order>) => p.data && <StatusBadge o={p.data} />,
      valueGetter: (p) => (p.data ? score(p.data) : 0), sort: "desc",
    },
    { headerName: "Buyer · order", minWidth: 180, flex: 1, cellRenderer: BuyerCell, autoHeight: true,
      valueGetter: (p) => p.data?.buyer_name ?? "" },
    {
      headerName: "Amount", width: 110, type: "rightAligned", cellClass: "num",
      valueGetter: (p) => Number(p.data?.amount ?? 0),
      valueFormatter: (p) => (p.data ? money(p.data.amount, p.data.currency) : ""),
    },
    { headerName: "Problem", minWidth: 160, valueGetter: (p) => (p.data?.signal ? SIGNAL_LABEL[p.data.signal] ?? p.data.signal : "—") },
    { headerName: "Why (AI)", field: "flag_reason", minWidth: 260, flex: 2, wrapText: true, autoHeight: true, cellClass: "cell-wrap" },
    { headerName: "Next step", minWidth: 180, cellRenderer: StepCell, autoHeight: true, sortable: false },
    { headerName: "", width: 120, sortable: false,
      cellRenderer: (p: ICellRendererParams<Order>) => p.data && <ApproveButton o={p.data} onDone={onChange} /> },
    { headerName: "Paid", width: 96, valueGetter: (p) => (p.data ? new Date(p.data.captured_at) : null),
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
