export const API_URL = (import.meta.env.VITE_API_URL as string) || "";

export type Health = {
  status: string;
  service: string;
  database: string;
  paypal: string;
  paypal_env: string;
  ai: string;
  ai_model: string;
};

export type Order = {
  id: string;
  paypal_order_id: string;
  capture_id: string | null;
  buyer_name: string | null;
  buyer_email: string | null;
  amount: string;
  currency: string;
  captured_at: string;
  tracking_number: string | null;
  tracking_attached_at: string | null;
  risk_level: "low" | "medium" | "high" | null;
  signal: string | null;
  flag_reason: string | null;
  shipped_at: string | null;
  watch_status: "not_shipped" | "watching" | "protected" | "closed";
  next_check_at: string | null;
  demo_scenario: string | null;
  demo_shift_hours: number;
  action_id: string | null;
  action_type: "attach_tracking" | "message_buyer" | "partial_refund" | null;
  action_params: Record<string, string | number | boolean> | null;
  action_justification: string | null;
  action_status: string | null;
  findings: Findings | null;
  tools_used: string[];
};

export type Findings = {
  summary: string;
  parcel_status: string;
  likely_cause: string;
  evidence: { source: string; fact: string }[];
  buyer_need: string | null;
  resolution_options: string[];
  confidence: string;
};

export type Scenario = { id: string; label: string };

export type CheckResult = {
  risk_level?: string;
  signal?: string;
  reason?: string;
  watch_status?: string;
  next_check_in_hours?: number;
  error?: string;
  skipped?: boolean;
};

export type TimelineItem = {
  kind: string;
  title: string;
  detail: Record<string, unknown>;
  occurred_at: string;
  recorded_at: string;
};

export type Report = {
  orders: number;
  shipped: number;
  watching: number;
  protected: number;
  checks_run: number;
  orders_flagged: number;
  paypal_confirmations: number;
  shipped_with_proof: number;
  proof_coverage_pct: number | null;
  disputes: number;
  risks_caught_by_signal: Record<string, number>;
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API_URL}${path}`, init);
  if (!r.ok) {
    let detail = `${r.status}`;
    try {
      const body = await r.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* non-JSON error */
    }
    throw new Error(detail);
  }
  return r.json();
}

export const getHealth = () => call<Health>("/api/health");
export const getOrders = () => call<Order[]>("/api/orders");
export const createTestOrder = () => call<TestOrder>("/api/demo/test-order", { method: "POST" });
export const getScenarios = () => call<Scenario[]>("/api/demo/scenarios");
const post = <T,>(path: string, body?: unknown) =>
  call<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const shipOrder = (id: string, scenario: string | null) =>
  post<CheckResult>(`/api/orders/${id}/ship`, scenario ? { scenario } : {});
export const advanceOrder = (id: string, hours = 24) => post<CheckResult>(`/api/orders/${id}/advance?hours=${hours}`);
export const checkOrder = (id: string) => post<CheckResult>(`/api/orders/${id}/check`);
export const getTimeline = (id: string) => call<TimelineItem[]>(`/api/orders/${id}/timeline`);
export const getReport = () => call<Report>("/api/report");
export const createCardOrder = () =>
  call<{ order: Order; item: { name: string }; paypal_status: string }>("/api/demo/card-order", { method: "POST" });
// Nudge the watcher whenever the app opens (the server throttles it).
export const nudgeWatcher = () => fetch(`${API_URL}/api/watch/tick`, { method: "POST" }).catch(() => {});
