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
  Clock,
  ChevronRight,
  TrendingUp,
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

function MetricCard({ icon: Icon, label, value, tone, trend }) {
  return (
    <div className="metric-card">
      <div className={`metric-icon ${tone}`}>
        <Icon size={22} />
      </div>
      <div className="metric-value">{value}</div>
      <div className="metric-label">
        {label}
        {trend && (
          <span className="trend-indicator">
             <TrendingUp size={12} style={{ marginLeft: '4px', verticalAlign: 'middle' }} />
          </span>
        )}
      </div>
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
            <Brain size={28} />
          </div>
          <div>
            <h1>Kernel Omega</h1>
            <p>Deterministic Autonomous Repair Fabric v19</p>
          </div>
        </div>
        <div className="status-row">
          <div className="pill">
            <div className="live-indicator" />
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
          <AlertTriangle size={18} />
          <span><strong>Connection Lost:</strong> Telemetry API unreachable ({error})</span>
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
              trend={true}
            />
            <MetricCard
              icon={Zap}
              label="Evals / Sec"
              value={metrics.evalsPerSec.toFixed(1)}
              tone="tone-gold"
            />
          </div>
        </section>

        <section className="panel panel-main">
          <div className="panel-header">
            <h2><Activity size={18} style={{ marginRight: '8px', verticalAlign: 'text-bottom' }} /> Live Verification Runs</h2>
            <span className="pill">{liveRuns.length} active events</span>
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
                    <td colSpan="4" className="empty-cell" style={{ textAlign: 'center', padding: '40px' }}>
                      <Clock size={24} style={{ opacity: 0.3, marginBottom: '12px' }} />
                      <p>Monitoring for verification traffic...</p>
                    </td>
                  </tr>
                ) : (
                  liveRuns.map((run) => (
                    <tr key={run.id}>
                      <td className="capsule-id">{run.id}</td>
                      <td>{run.runtime}s</td>
                      <td>
                        <span className={`pill ${run.status === "FORMAL PROOF" ? "status-success" : "status-muted"}`}>
                          {run.status}
                        </span>
                      </td>
                      <td className={run.reward?.startsWith("+") ? "reward-up" : "reward-down"} style={{ fontWeight: 600 }}>
                        {run.reward}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel panel-side">
          <div className="panel-header">
            <h2><Brain size={18} style={{ marginRight: '8px', verticalAlign: 'text-bottom' }} /> Search Forest</h2>
            <span className="pill">{forestState.length} Trees</span>
          </div>
          <div className="forest-list">
            {forestState.length === 0 ? (
              <div className="empty-forest" style={{ textAlign: 'center', padding: '20px' }}>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>Waiting for repair tasks...</p>
              </div>
            ) : (
              forestState.map((tree) => (
                <div key={tree.id} className="forest-card">
                  <div className="forest-title">{tree.id}</div>
                  <div className="forest-strategy" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <ChevronRight size={14} /> {tree.strategy}
                  </div>
                  <div className="forest-metrics" style={{ marginTop: '16px', paddingTop: '12px', borderTop: '1px solid var(--glass-border)' }}>
                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                      <span style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)' }}>Depth</span>
                      <span style={{ fontWeight: 600 }}>{tree.depth}</span>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', textAlign: 'right' }}>
                      <span style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)' }}>Best Reward</span>
                      <span style={{ fontWeight: 600, color: 'var(--success)' }}>{tree.best_reward}</span>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </section>

        <section className="panel panel-wide">
          <div className="panel-header">
            <h2>Audit Performance Baseline</h2>
          </div>
          <div className="metrics-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
            {[
              ["Seed", performance.avgSeedMs],
              ["Coverage", performance.avgCoverageMs],
              ["Generation", performance.avgGenerationMs],
              ["Queue Wait", performance.avgQueueWaitMs],
              ["Executor", performance.avgExecutorMs],
              ["Full Suite", performance.avgFullSuiteMs],
            ].map(([label, value]) => (
              <div key={label} style={{ padding: '16px', borderRadius: '16px', background: 'rgba(255,255,255,0.02)', border: '1px solid var(--glass-border)' }}>
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '8px' }}>{label}</div>
                <div style={{ fontSize: '1.2rem', fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{value.toFixed(1)}ms</div>
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
