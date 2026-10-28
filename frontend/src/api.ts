export const API_URL = (import.meta.env.VITE_API_URL as string) || "";

export type Health = {
  status: string;
  service: string;
  database: string;
  paypal: string;
  paypal_env: string;
  ai: string;
  ai_model: string;
  easypost: string;
  easypost_mode: string | null;
  email: string;
  email_redirect: boolean;
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
  action_type: "attach_tracking" | "message_buyer" | "partial_refund" | "full_refund" | "ship_replacement" | null;
  action_params: Record<string, string | number | boolean> | null;
  action_justification: string | null;
  action_status: "proposed" | "approved" | "executed" | "failed" | "dismissed" | null;
  action_edited: boolean | null;
  action_is_followup: boolean | null;
  action_result: Record<string, unknown> | null;
  resolution: string | null;
  refunded_amount: string;
  carrier_source: "simulator" | "easypost" | "manual";
  tracking_public_url: string | null;
  carrier: string | null;
  track_token: string;
  store_platform: "woocommerce" | null;
  store_order_id: string | null;
  findings: Findings | null;
  tools_used: string[];
  action_model: string | null;
  ai_check: AiCheck | null;
  ai_model: string | null;
  ai_checked_at: string | null;
};

export type AiStep = { source: string; checked: string; learned: string };

/** The AI's latest check of an order: its investigation steps and decisions. */
export type AiCheck = {
  steps?: AiStep[];
  risk_level: string;
  signal: string;
  reason: string;
  summary?: string;
  buyer_mood?: string;
  confidence?: string;
  next_check_in_hours?: number;
  next_check_reason?: string;
  safe_to_close?: boolean;
  tools_used?: string[];
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

export type TestOrder = {
  paypal_order_id: string;
  item: { sku: string; name: string; price: number };
  approve_url: string;
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
  fixes_executed: number;
  refunded_total: string;
  resolved_by_refund: number;
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

export type ShipChoice = { kind: "sim"; scenario: string } | { kind: "ep"; code: string } | { kind: "manual" };
export const shipOrder = (id: string, c: ShipChoice) =>
  post<CheckResult>(`/api/orders/${id}/ship`,
    c.kind === "sim" ? { scenario: c.scenario } : c.kind === "ep" ? { easypost_code: c.code } : {});
export const getEasyPostCodes = () => call<{ code: string; label: string }[]>("/api/easypost/test-codes");
export const advanceOrder = (id: string, hours = 24) => post<CheckResult>(`/api/orders/${id}/advance?hours=${hours}`);
export const checkOrder = (id: string) => post<CheckResult>(`/api/orders/${id}/check`);
export const getTimeline = (id: string) => call<TimelineItem[]>(`/api/orders/${id}/timeline`);
export const getReport = () => call<Report>("/api/report");
export const createCardOrder = () =>
  call<{ order: Order; item: { name: string }; paypal_status: string }>("/api/demo/card-order", { method: "POST" });
// Nudge the watcher whenever the app opens (the server throttles it).
export const nudgeWatcher = () => fetch(`${API_URL}/api/watch/tick`, { method: "POST" }).catch(() => {});
export const approveAction = (id: string) => post<{ status: string; paypal_error?: unknown }>(`/api/actions/${id}/approve`);
export const dismissAction = (id: string) => post<unknown>(`/api/actions/${id}/dismiss`);
export const editAction = (id: string, body: { message?: string; note_to_payer?: string; amount?: number }) =>
  call<unknown>(`/api/actions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
export const buyerReply = (orderId: string, text: string) =>
  post<{ intent: { choice: string; summary: string } | null; proposal: unknown }>(`/api/orders/${orderId}/buyer-reply`, { text });

export type Dispute = {
  paypal_dispute_id: string;
  order_id: string | null;
  buyer_name: string | null;
  paypal_order_id: string | null;
  reason: string;
  status: string;
  stage: string | null;
  amount: string | null;
  currency: string | null;
  due_at: string | null;
  outcome: string | null;
  allowed_actions: string[];
  messages: { posted_by?: string; content?: string; time_posted?: string }[];
  evidence: {
    notes: string; key_facts: string[]; strength: string; strength_reason: string;
    recommended_response: string; evidences: { evidence_type: string }[];
  } | null;
  evidence_status: "none" | "drafted" | "submitted" | "failed";
  paypal_response: Record<string, unknown> | null;
};

export const getDisputes = () => call<Dispute[]>("/api/disputes");
export const syncDisputes = () => post<{ synced: string[] }>("/api/disputes/sync");
export const draftEvidence = (id: string) => post<Dispute>(`/api/disputes/${id}/draft`);
export const editDisputeNotes = (id: string, notes: string) =>
  call<Dispute>(`/api/disputes/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notes }),
  });
export const submitDispute = (id: string) =>
  post<{ status: string; how?: string; paypal_error?: unknown }>(`/api/disputes/${id}/submit`);
export const evidencePdfUrl = (id: string) => `${API_URL}/api/disputes/${id}/evidence.pdf`;

// --- Store connections, email, and the buyer's tracking page ---

export type StorePlatform = "woocommerce";
export type StoreStatus = {
  platform: StorePlatform;
  secret_set: boolean;
  linked_orders: number;
  webhook_url: string;
  last_event: { event: string | null; valid: boolean; received_at: string } | null;
};
export const getStores = () => call<StoreStatus[]>("/api/stores");
export const testStore = (platform: StorePlatform, orderId?: string) =>
  post<{ signed: boolean; result: { order_id?: string; shipped?: boolean; matched_by?: string; ignored?: string } }>(
    `/api/stores/${platform}/test`, { order_id: orderId ?? null });

export type EmailStatus = {
  status: string; host: string | null; from: string | null; redirect_to: string | null;
  auto_shipping_email: boolean; store_name: string;
};
export const getEmail = () => call<EmailStatus>("/api/email");
export const testEmail = () => post<{ sent: boolean; to: string }>("/api/email/test");

export type TrackInfo = {
  store_name: string;
  buyer_first_name: string | null;
  amount: string;
  currency: string;
  paid_at: string;
  shipped_at: string | null;
  headline: string;
  delivered: boolean;
  carrier: string | null;
  tracking_number: string | null;
  carrier_url: string | null;
  events: { status: string; location: string | null; at: string }[];
  messages: { from: "buyer" | "seller"; text: string; at: string }[];
  awaiting_reply: boolean;
  offers: string[];
};
export const getTrack = (token: string) => call<TrackInfo>(`/api/track/${encodeURIComponent(token)}`);
export const sendTrackReply = (token: string, text: string) =>
  post<{ received: boolean }>(`/api/track/${encodeURIComponent(token)}/reply`, { text });
