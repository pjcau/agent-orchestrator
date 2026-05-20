import React, {useEffect, useMemo, useState} from 'react';
import Layout from '@theme/Layout';
import BrowserOnly from '@docusaurus/BrowserOnly';
import useBaseUrl from '@docusaurus/useBaseUrl';
import styles from './architecture-map.module.css';

function hexWithAlpha(hex, alpha) {
  const v = hex.replace('#', '');
  const r = parseInt(v.slice(0, 2), 16);
  const g = parseInt(v.slice(2, 4), 16);
  const b = parseInt(v.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function Constellation({payload, onSelect, focusedClusterId}) {
  const {clusters, view_box} = payload;
  return (
    <svg
      className={styles.svg}
      viewBox={view_box}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="Agent Orchestrator architecture constellation">
      {/* Cluster ellipses (drawn first so they sit behind the bubbles) */}
      {clusters.map((c) => {
        const dim = focusedClusterId && focusedClusterId !== c.id;
        return (
          <g key={`cluster-${c.id}`} opacity={dim ? 0.25 : 1}>
            <ellipse
              cx={c.cx}
              cy={c.cy}
              rx={c.rx}
              ry={c.ry}
              fill={hexWithAlpha(c.color, 0.12)}
              stroke={c.color}
              strokeWidth="2"
              strokeDasharray="8 6"
            />
            <text
              x={c.cx}
              y={c.cy - c.ry + 28}
              textAnchor="middle"
              className={styles.clusterLabel}
              fill={c.color}>
              {c.label}
            </text>
          </g>
        );
      })}

      {/* Item bubbles */}
      {clusters.map((c) => {
        const dim = focusedClusterId && focusedClusterId !== c.id;
        return (
          <g key={`items-${c.id}`} opacity={dim ? 0.3 : 1}>
            {c.items.map((item) => {
              const r = item.size / 2;
              return (
                <g
                  key={item.name}
                  className={styles.bubble}
                  onClick={() => onSelect({cluster: c, item})}
                  tabIndex={0}
                  role="button"
                  aria-label={item.name}>
                  <circle
                    cx={item.cx}
                    cy={item.cy}
                    r={r}
                    fill={c.color}
                    stroke="#fff"
                    strokeWidth="2"
                  />
                  <text
                    x={item.cx}
                    y={item.cy + r + 14}
                    textAnchor="middle"
                    className={styles.itemLabel}>
                    {item.name}
                  </text>
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}

function Sidebar({selection, onClose}) {
  if (!selection) {
    return (
      <aside className={styles.sidebar}>
        <h3>Select a feature</h3>
        <p className={styles.hint}>
          Click any bubble in the constellation to see its description and
          jump to the source files on GitHub.
        </p>
      </aside>
    );
  }
  const {cluster, item} = selection;
  return (
    <aside className={styles.sidebar}>
      <div className={styles.sidebarHeader} style={{borderColor: cluster.color}}>
        <span className={styles.tag} style={{background: cluster.color}}>
          {cluster.label}
        </span>
        <button className={styles.close} onClick={onClose} aria-label="Close">×</button>
      </div>
      <h3>{item.name}</h3>
      <p>{item.description}</p>
      <h4>Source files</h4>
      <ul>
        {item.files.map((f, i) => (
          <li key={f}>
            <a href={item.urls[i]} target="_blank" rel="noopener noreferrer">
              <code>{f}</code>
            </a>
          </li>
        ))}
      </ul>
    </aside>
  );
}

function ArchitectureMapClient() {
  const dataUrl = useBaseUrl('/architecture-map.json');
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(null);
  const [selection, setSelection] = useState(null);
  const [focusedClusterId, setFocusedClusterId] = useState(null);

  useEffect(() => {
    fetch(dataUrl)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setPayload)
      .catch((e) => setError(e.message));
  }, [dataUrl]);

  const legend = useMemo(() => {
    if (!payload) return [];
    return payload.clusters.map((c) => ({
      id: c.id,
      label: c.label,
      color: c.color,
      count: c.items.length,
      description: c.description,
    }));
  }, [payload]);

  if (error) {
    return (
      <div className={styles.error}>
        Failed to load <code>architecture-map.json</code>: {error}
        <br />
        Run <code>python scripts/generate_architecture_map.py</code> first.
      </div>
    );
  }
  if (!payload) {
    return <div className={styles.loading}>Loading architecture map…</div>;
  }

  return (
    <div className={styles.wrapper}>
      <header className={styles.header}>
        <h1>Architecture Map</h1>
        <p>
          Logical constellation of the project's feature clusters. Curated in{' '}
          <code>docs/website/architecture-map.yaml</code> · {payload.stats.total_clusters}{' '}
          clusters · {payload.stats.total_items} features. Click a bubble to
          inspect.
        </p>
      </header>

      <div className={styles.legend}>
        {legend.map((c) => (
          <button
            key={c.id}
            className={`${styles.legendItem} ${focusedClusterId === c.id ? styles.legendActive : ''}`}
            onClick={() =>
              setFocusedClusterId(focusedClusterId === c.id ? null : c.id)
            }
            title={c.description}
            style={{borderColor: c.color}}>
            <span className={styles.swatch} style={{background: c.color}} />
            <strong>{c.label}</strong>
            <span className={styles.legendCount}>{c.count}</span>
          </button>
        ))}
        {focusedClusterId && (
          <button
            className={styles.clearFocus}
            onClick={() => setFocusedClusterId(null)}>
            Show all
          </button>
        )}
      </div>

      <div className={styles.canvas}>
        <Constellation
          payload={payload}
          onSelect={setSelection}
          focusedClusterId={focusedClusterId}
        />
        <Sidebar selection={selection} onClose={() => setSelection(null)} />
      </div>
    </div>
  );
}

export default function ArchitectureMap() {
  return (
    <Layout
      title="Architecture Map"
      description="Logical constellation of Agent Orchestrator feature clusters">
      <BrowserOnly fallback={<div>Loading…</div>}>
        {() => <ArchitectureMapClient />}
      </BrowserOnly>
    </Layout>
  );
}
