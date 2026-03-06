import React, { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Brain,
  Database,
  GitCommit,
  Server,
  ShieldCheck,
  Zap,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8787";

const defaultSnapshot = {
  metrics: {
    activeWorkers: 0,
    queueDepth: 0,
    totalEvals: 0,
    evalsPerSec: 0,
  },
  performance: {
    avgSeedMs: 0,
    avgCoverageMs: 0,
    avgGenerationMs: 0,
    avgQueueWaitMs: 0,
    avgExecutorMs: 0,
    avgFullSuiteMs: 0,
    avgSnapshotBytes: 0,
  },
  liveRuns: [],
  forestState: [],
};

function useDashboardSnapshot() {
  const [snapshot, setSnapshot] = useState(defaultSnapshot);
  const [error, setError] = useState(null);

  useEffect(() => {
    let mounted = true;

    const load = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/dashboard`);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const body = await response.json();
        if (mounted) {
          setSnapshot(body);
          setError(null);
        }
      } catch (err) {
        if (mounted) {
          setError(err.message);
        }
      }
    };

    load();
    const timer = window.setInterval(load, 1000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, []);

  return { snapshot, error };
}

function MetricCard({ icon: Icon, label, value, tone }) {
  return (
    <div className="metric-card">
      <div className={`metric-icon ${tone}`}>
        <Icon size={18} />
      </div>
      <div className="metric-value">{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}

export default function App() {
  const { snapshot, error } = useDashboardSnapshot();
  const { metrics, performance, liveRuns, forestState } = snapshot;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            <Brain size={26} />
          </div>
          <div>
            <h1>Kernel Omega</h1>
            <p>Deterministic Autonomous Repair Fabric</p>
          </div>
        </div>
        <div className="status-row">
          <div className="pill">
            <ShieldCheck size={14} />
            Policy Gate Armed
          </div>
          <div className="pill">
            <Database size={14} />
            Redis Telemetry Live
          </div>
        </div>
      </header>

      {error ? (
        <div className="error-banner">
          <AlertTriangle size={16} />
          Telemetry API unreachable: {error}
        </div>
      ) : null}

      <main className="grid">
        <section className="panel panel-wide">
          <div className="metrics-grid">
            <MetricCard
              icon={Server}
              label="Active Workers"
              value={metrics.activeWorkers}
              tone="tone-green"
            />
            <MetricCard
              icon={Activity}
              label="In-Flight Capsules"
              value={metrics.queueDepth}
              tone="tone-red"
            />
            <MetricCard
              icon={GitCommit}
              label="Global Evals"
              value={metrics.totalEvals}
              tone="tone-blue"
            />
            <MetricCard
              icon={Zap}
              label="Evals / Sec"
              value={metrics.evalsPerSec.toFixed(1)}
              tone="tone-gold"
            />
          </div>
        </section>

        <section className="panel panel-wide">
          <div className="panel-header">
            <h2>Live Verification Runs</h2>
            <span>{liveRuns.length} recent events</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Capsule</th>
                  <th>Runtime</th>
                  <th>Status</th>
                  <th>Reward</th>
                </tr>
              </thead>
              <tbody>
                {liveRuns.length === 0 ? (
                  <tr>
                    <td colSpan="4" className="empty-cell">
                      No results yet
                    </td>
                  </tr>
                ) : (
                  liveRuns.map((run) => (
                    <tr key={run.id}>
                      <td className="capsule-id">{run.id}</td>
                      <td>{run.runtime}s</td>
                      <td className={run.status === "FORMAL PROOF" ? "status-success" : "status-muted"}>
                        {run.status}
                      </td>
                      <td className={run.reward?.startsWith("+") ? "reward-up" : "reward-down"}>
                        {run.reward}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel panel-wide">
          <div className="panel-header">
            <h2>Performance Hotspots</h2>
            <span>Current audit baseline</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Stage</th>
                  <th>Average</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["Seed", `${performance.avgSeedMs.toFixed(1)} ms`],
                  ["Coverage", `${performance.avgCoverageMs.toFixed(1)} ms`],
                  ["Generation", `${performance.avgGenerationMs.toFixed(1)} ms`],
                  ["Queue wait", `${performance.avgQueueWaitMs.toFixed(1)} ms`],
                  ["Executor", `${performance.avgExecutorMs.toFixed(1)} ms`],
                  ["Full suite", `${performance.avgFullSuiteMs.toFixed(1)} ms`],
                  ["Snapshot size", `${Math.round(performance.avgSnapshotBytes)} bytes`],
                ].map(([label, value]) => (
                  <tr key={label}>
                    <td>{label}</td>
                    <td>{value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Search Forest State</h2>
            <span>{forestState.length} active trees</span>
          </div>
          <div className="forest-list">
            {forestState.length === 0 ? (
              <div className="forest-card empty-forest">No active repair tasks</div>
            ) : (
              forestState.map((tree) => (
                <div key={tree.id} className="forest-card">
                  <div className="forest-title">{tree.id}</div>
                  <div className="forest-strategy">{tree.strategy}</div>
                  <div className="forest-metrics">
                    <span>Depth {tree.depth}</span>
                    <span>Best {tree.best_reward}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </section>
      </main>
    </div>
  );
}
