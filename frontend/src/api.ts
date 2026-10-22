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
};

export type Scenario = { id: string; label: string };

export type SimulateResult = {
  simulation: { scenario: string; label: string; shift_hours: number };
  assessment: { risk_level: string; signal: string; reason: string; facts_used: string[]; cached: boolean } | null;
  ai_error?: string;
};

export type TestOrder = {
  paypal_order_id: string;
  item: { sku: string; name: string; price: number };
  approve_url: string;
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
export const simulate = (orderId: string, scenario: string) =>
  call<SimulateResult>(`/api/orders/${orderId}/simulate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenario }),
  });
