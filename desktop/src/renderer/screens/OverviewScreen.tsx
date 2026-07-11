import { useCallback, useEffect, useState } from "react";
import type { Overview } from "../../shared/contracts";
import { ErrorState, LoadingState } from "../components/States";

export function OverviewScreen() {
  const [overview, setOverview] = useState<Overview>();
  const [error, setError] = useState<unknown>();
  const [restarting, setRestarting] = useState(false);

  const load = useCallback(() => {
    setError(undefined);
    void window.mneme
      .overview()
      .then(setOverview)
      .catch(setError);
  }, []);

  useEffect(load, [load]);

  const restart = async () => {
    setRestarting(true);
    setError(undefined);
    try {
      await window.mneme.server.restart();
      load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setRestarting(false);
    }
  };

  if (!overview && !error) return <LoadingState label="Connecting to Mneme" />;
  if (!overview) return <ErrorState error={error} retry={load} />;

  const stats = Object.entries(overview.stats);
  return (
    <section>
      <header className="page-header">
        <div>
          <span className="eyebrow">Local workspace</span>
          <h1>Overview</h1>
          <p>Your private catalog and gateway are running on this device.</p>
        </div>
        <button className="button secondary" disabled={restarting} onClick={restart}>
          {restarting ? "Restarting…" : "Restart server"}
        </button>
      </header>
      {error !== undefined && <ErrorState error={error} />}
      <div className="hero-card">
        <div className={`health-orb ${overview.healthy ? "healthy" : "unhealthy"}`}>✓</div>
        <div>
          <span className="eyebrow">Service status</span>
          <h2>{overview.healthy ? "Mneme is ready" : "Mneme needs attention"}</h2>
          <p>
            Version {overview.version} · Server {overview.server.state}
            {overview.server.port ? ` · localhost:${overview.server.port}` : ""}
          </p>
        </div>
      </div>
      <div className="section-heading">
        <h2>Catalog at a glance</h2>
      </div>
      <div className="stat-grid">
        {stats.length > 0 ? (
          stats.slice(0, 8).map(([label, value]) => (
            <article className="stat-card" key={label}>
              <span>{label.replaceAll("_", " ")}</span>
              <strong>{value ?? "—"}</strong>
            </article>
          ))
        ) : (
          <article className="stat-card">
            <span>Catalog</span>
            <strong>Empty</strong>
          </article>
        )}
      </div>
      <div className="privacy-note">
        <span>◈</span>
        <div>
          <strong>Local by design</strong>
          <p>
            API credentials are encrypted by your operating system. Secret values and the
            per-launch server token never enter the web renderer.
          </p>
        </div>
      </div>
    </section>
  );
}
