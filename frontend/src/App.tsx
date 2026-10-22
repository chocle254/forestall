import { useEffect, useState } from "react";
import { getHealth, type Health, API_URL } from "./api";

function Pill({ label, value }: { label: string; value: string }) {
  const good = value === "ok" || value === "configured";
  return (
    <div className="pill">
      <span>{label}</span>
      <strong className={good ? "good" : "warn"}>{value}</strong>
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    getHealth().then(setHealth).catch((e) => setError(String(e.message ?? e)));
  };
  useEffect(load, []);

  return (
    <main>
      <header>
        <h1>Forestall</h1>
        <p>Stops “item not received” disputes before buyers file them.</p>
      </header>
      <section className="card">
        <h2>System status</h2>
        {error && <p className="warn">Can’t reach the API at {API_URL}: {error}</p>}
        {!error && !health && <p>Checking…</p>}
        {health && (
          <>
            <Pill label="API" value={health.status} />
            <Pill label="Supabase" value={health.supabase} />
            <Pill label="PayPal" value={health.paypal} />
          </>
        )}
        <button onClick={load}>Recheck</button>
      </section>
    </main>
  );
}
