export const API_URL = (import.meta.env.VITE_API_URL as string) || "";

export type Health = {
  status: string;
  service: string;
  database: string;
  paypal: string;
};

export async function getHealth(): Promise<Health> {
  const r = await fetch(`${API_URL}/api/health`);
  if (!r.ok) throw new Error(`API returned ${r.status}`);
  return r.json();
}
