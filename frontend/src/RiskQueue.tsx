import { useMemo, useState } from "react";
import {
  AllCommunityModule, ModuleRegistry, themeQuartz,
  type ColDef, type ICellRendererParams, type RowClickedEvent,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { approveAction, type Order } from "./api";

ModuleRegistry.registerModules([AllCommunityModule]);

// AG Grid's Quartz theme, wired to the app's own color tokens so it follows light/dark mode.
const theme = themeQuartz.withParams({
  backgroundColor: "var(--card)",
  foregroundColor: "var(--text)",
  chromeBackgroundColor: "var(--bg)",
  headerTextColor: "var(--muted)",
  borderColor: "var(--border)",
  accentColor: "var(--accent)",
  fontFamily: "inherit",
  fontSize: 14,
  headerFontWeight: 600,
  wrapperBorder: false,
});

export const SIGNAL_LABEL: Record<string, string> = {
  no_tracking: "No tracking on PayPal",
  stalled: "Shipment stalled",
  buyer_concern: "Buyer worried",
  delivered_not_received: "Delivered, not received",
  carrier_exception: "Carrier reports a problem",
};

const FIX_SHORT: Record<string, string> = {
  attach_tracking: "Attach tracking",
  message_buyer: "Message buyer",
  partial_refund: "Goodwill refund",
  full_refund: "Full refund",
  ship_replacement: "Ship replacement",
};

const RISK_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 };

type Filter = "attention" | "approval" | "risk" | "watching" | "unshipped" | "safe" | "all";
const FILTERS: [Filter, string][] = [
  ["attention", "Needs attention"],
  ["approval", "Needs approval"],
  ["risk", "At risk"],
  ["watching", "Watching"],
  ["unshipped", "Not shipped"],
  ["safe", "Protected / resolved"],
  ["all", "All"],
];

const matches = (o: Order, f: Filter) => {
  switch (f) {
    case "approval": return o.action_status === "proposed";
    case "risk": return !!o.risk_level;
    case "watching": return o.watch_status === "watching";
    case "unshipped": return o.watch_status === "not_shipped";
    case "safe": return o.watch_status === "protected" || o.watch_status === "closed";
    case "attention": return o.action_status === "proposed" || !!o.risk_level || o.resolution === "awaiting_buyer_reply";
    default: return true;
  }
};

function RiskCell({ data }: ICellRendererParams<Order>) {
  if (!data) return null;
  if (data.watch_status === "protected") return <span className="badge protected">PROTECTED</span>;
  if (data.watch_status === "closed") return <span className="badge protected">RESOLVED</span>;
  if (data.risk_level) return <span className={`badge ${data.risk_level}`}>{data.risk_level.toUpperCase()}</span>;
  if (data.watch_status === "watching") return <span className="badge watching">WATCHING</span>;
  return <span className="badge none">NOT SHIPPED</span>;
}

function BuyerCell({ data }: ICellRendererParams<Order>) {
  if (!data) return null;
  return (
    <div className="cell-stack">
      <b>{data.buyer_name ?? "Unknown"}</b>
      <span className="muted small">{data.paypal_order_id}</span>
    </div>
  );
}

function NextStepCell({ data }: ICellRendererParams<Order>) {
  if (!data?.action_type) {
    if (data?.watch_status === "not_shipped") return <span className="muted">Ship it to start watching</span>;
    return <span className="muted">—</span>;
  }
  const state =
    data.action_status === "proposed" ? (data.action_is_followup ? "buyer's choice, awaiting you" : "awaiting approval")
      : data.action_status === "executed" ? (data.resolution === "awaiting_buyer_reply" ? "sent, awaiting buyer" : "done")
        : data.action_status;
  return (
    <div className="cell-stack">
      <b>{FIX_SHORT[data.action_type] ?? data.action_type}</b>
      <span className="muted small">{state}</span>
    </div>
  );
}

function ApproveCell({ data, context }: ICellRendererParams<Order, unknown, { onDone: () => void }>) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!data || data.action_status !== "proposed" || !data.action_id) return null;
  const go = async (e: React.MouseEvent) => {
    e.stopPropagation(); // don't open the row
    setBusy(true);
    setErr(null);
    try {
      if (data.action_type === "message_buyer" && data.action_params?.message) {
        try { await navigator.clipboard.writeText(String(data.action_params.message)); } catch { /* blocked */ }
      }
      const r = await approveAction(data.action_id!);
      if (r.status === "failed") setErr("PayPal rejected it");
      context.onDone();
    } catch (ex) {
      setErr((ex as Error).message.slice(0, 60));
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="cell-approve">
      <button className="grid-btn" onClick={go} disabled={busy}>{busy ? "…" : "Approve"}</button>
      {err && <span className="warn small"> {err}</span>}
    </span>
  );
}

export default function RiskQueue({ orders, selectedId, onSelect, onChange }: {
  orders: Order[]; selectedId: string | null; onSelect: (id: string) => void; onChange: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("attention");
  const [search, setSearch] = useState("");

  const counts = useMemo(
    () => Object.fromEntries(FILTERS.map(([f]) => [f, orders.filter((o) => matches(o, f)).length])) as Record<Filter, number>,
    [orders],
  );
  const rows = useMemo(() => orders.filter((o) => matches(o, filter)), [orders, filter]);

  const columns = useMemo<ColDef<Order>[]>(() => [
    {
      headerName: "Risk", width: 118, pinned: "left", cellRenderer: RiskCell, sort: "desc",
      valueGetter: (p) => (p.data?.risk_level ? RISK_RANK[p.data.risk_level] * 10 : 0)
        + (p.data?.action_status === "proposed" ? 5 : 0) + (p.data?.watch_status === "watching" ? 1 : 0),
      filter: false,
    },
    {
      headerName: "Buyer · order", minWidth: 170, flex: 1, cellRenderer: BuyerCell, autoHeight: true,
      valueGetter: (p) => `${p.data?.buyer_name ?? ""} ${p.data?.paypal_order_id ?? ""}`,
      filter: "agTextColumnFilter",
    },
    {
      headerName: "Amount", field: "amount", width: 100, type: "rightAligned", filter: "agNumberColumnFilter",
      valueGetter: (p) => Number(p.data?.amount ?? 0),
      valueFormatter: (p) => `$${Number(p.value).toFixed(2)}`,
    },
    {
      headerName: "Problem", minWidth: 150,
      valueGetter: (p) => (p.data?.signal ? SIGNAL_LABEL[p.data.signal] ?? p.data.signal : ""),
      filter: "agTextColumnFilter",
    },
    {
      headerName: "Why (AI)", field: "flag_reason", minWidth: 260, flex: 2, wrapText: true, autoHeight: true,
      cellClass: "cell-wrap", filter: "agTextColumnFilter",
    },
    {
      headerName: "Next step", minWidth: 170, cellRenderer: NextStepCell, autoHeight: true,
      valueGetter: (p) => `${p.data?.action_type ?? ""} ${p.data?.action_status ?? ""}`,
      filter: "agTextColumnFilter",
    },
    { headerName: "", width: 118, cellRenderer: ApproveCell, sortable: false, filter: false },
    {
      headerName: "Paid", field: "captured_at", width: 130, filter: "agDateColumnFilter",
      valueGetter: (p) => (p.data ? new Date(p.data.captured_at) : null),
      valueFormatter: (p) => (p.value ? (p.value as Date).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : ""),
    },
  ], []);

  return (
    <div className="queue">
      <div className="chips">
        {FILTERS.map(([f, label]) => (
          <button key={f} className={`chip-btn ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>
            {label} <span className="count">{counts[f]}</span>
          </button>
        ))}
      </div>
      <input className="search" value={search} onChange={(e) => setSearch(e.target.value)}
             placeholder="Search buyer, order ID, reason…" />
      <div className="grid-wrap">
        <AgGridReact<Order>
          theme={theme}
          rowData={rows}
          columnDefs={columns}
          defaultColDef={{ sortable: true, resizable: true, filter: true, suppressHeaderMenuButton: false }}
          getRowId={(p) => p.data.id}
          quickFilterText={search}
          domLayout="autoHeight"
          pagination
          paginationPageSize={10}
          paginationPageSizeSelector={[10, 25, 50]}
          context={{ onDone: onChange }}
          rowClass="grid-row"
          getRowClass={(p) => (p.data?.id === selectedId ? "row-selected" : "")}
          onRowClicked={(e: RowClickedEvent<Order>) => e.data && onSelect(e.data.id)}
          overlayNoRowsTemplate="<span>Nothing here. Try another filter.</span>"
        />
      </div>
    </div>
  );
}
