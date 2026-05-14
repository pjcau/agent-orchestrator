import React, {useEffect, useMemo, useRef, useState} from 'react';
import Layout from '@theme/Layout';
import BrowserOnly from '@docusaurus/BrowserOnly';
import useBaseUrl from '@docusaurus/useBaseUrl';
import * as d3 from 'd3';
import styles from './feature-map.module.css';

// Categorical palette — picked for maximum pairwise distinctness (hues
// spread across the wheel, also colour-blind friendly enough). Inspired by
// d3.schemeCategory10 but tuned for dark/light themes.
const CATEGORY_COLORS = {
  Core: '#1f77b4',        // deep blue
  Provider: '#9467bd',    // purple
  Skill: '#2ca02c',       // forest green
  Dashboard: '#ff7f0e',   // orange
  Integration: '#d62728', // red
};

const LAYER_COLORS = {
  harness: CATEGORY_COLORS.Core,
  app: CATEGORY_COLORS.Dashboard,
};

function ForceGraph({nodes, edges, githubBase, onSelect}) {
  const svgRef = useRef(null);
  const wrapperRef = useRef(null);
  const simulationRef = useRef(null);

  useEffect(() => {
    if (!svgRef.current || !wrapperRef.current) return;
    const wrapper = wrapperRef.current;
    const width = wrapper.clientWidth;
    const height = Math.max(600, wrapper.clientHeight || 700);
    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();
    svg.attr('viewBox', [0, 0, width, height]);

    // Working copies so d3 can mutate them without confusing React.
    const dataNodes = nodes.map((n) => ({...n}));
    const dataLinks = edges
      .filter((e) => dataNodes.find((n) => n.id === e.source) && dataNodes.find((n) => n.id === e.target))
      .map((e) => ({source: e.source, target: e.target}));

    const radius = (n) => 4 + Math.sqrt(n.weight) * 3.5;

    const container = svg.append('g');

    // Zoom + pan
    const zoomBehavior = d3
      .zoom()
      .scaleExtent([0.15, 6])
      .on('zoom', (event) => {
        container.attr('transform', event.transform);
      });
    svg.call(zoomBehavior);
    svg.on('dblclick.zoom', null);

    // Arrow marker for directed links
    svg
      .append('defs')
      .append('marker')
      .attr('id', 'arrow')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 18)
      .attr('refY', 0)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', '#94a3b8');

    const link = container
      .append('g')
      .attr('class', 'links')
      .selectAll('line')
      .data(dataLinks)
      .join('line')
      .attr('stroke', '#64748b')
      .attr('stroke-opacity', 0.35)
      .attr('stroke-width', 1)
      .attr('marker-end', 'url(#arrow)');

    const nodeGroup = container
      .append('g')
      .attr('class', 'nodes')
      .selectAll('g')
      .data(dataNodes)
      .join('g')
      .attr('class', styles.node)
      .style('cursor', 'pointer')
      .on('click', (_, d) => onSelect(d))
      .on('dblclick', (_, d) => {
        window.open(`${githubBase}/src/agent_orchestrator/${d.path}`, '_blank');
      });

    nodeGroup
      .append('circle')
      .attr('r', (d) => radius(d))
      .attr('fill', (d) => CATEGORY_COLORS[d.category] || '#888')
      .attr('stroke', '#fff')
      .attr('stroke-width', 1.5)
      .append('title')
      .text(
        (d) =>
          `${d.id}\nweight ${d.weight}  •  in ${d.in_degree}  •  out ${d.out_degree}\n${d.description}`,
      );

    nodeGroup
      .append('text')
      .attr('class', styles.nodeLabel)
      .attr('dy', (d) => radius(d) + 12)
      .attr('text-anchor', 'middle')
      .text((d) => (radius(d) > 8 ? d.name : ''));

    const drag = d3
      .drag()
      .on('start', (event, d) => {
        if (!event.active) simulationRef.current.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on('drag', (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on('end', (event, d) => {
        if (!event.active) simulationRef.current.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });
    nodeGroup.call(drag);

    const sim = d3
      .forceSimulation(dataNodes)
      .force(
        'link',
        d3
          .forceLink(dataLinks)
          .id((d) => d.id)
          .distance((l) => 60 + Math.sqrt((l.source.weight || 1) + (l.target.weight || 1)) * 4)
          .strength(0.5),
      )
      .force('charge', d3.forceManyBody().strength((d) => -120 - d.weight * 8))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force(
        'collide',
        d3.forceCollide().radius((d) => radius(d) + 4),
      )
      .on('tick', () => {
        link
          .attr('x1', (d) => d.source.x)
          .attr('y1', (d) => d.source.y)
          .attr('x2', (d) => d.target.x)
          .attr('y2', (d) => d.target.y);
        nodeGroup.attr('transform', (d) => `translate(${d.x},${d.y})`);
      });
    simulationRef.current = sim;

    // Fit-to-view helper exposed via ref
    svgRef.current.__resetZoom = () => {
      svg
        .transition()
        .duration(500)
        .call(zoomBehavior.transform, d3.zoomIdentity);
    };
    svgRef.current.__zoomIn = () => {
      svg.transition().duration(250).call(zoomBehavior.scaleBy, 1.4);
    };
    svgRef.current.__zoomOut = () => {
      svg.transition().duration(250).call(zoomBehavior.scaleBy, 1 / 1.4);
    };

    return () => sim.stop();
  }, [nodes, edges, githubBase, onSelect]);

  return (
    <div ref={wrapperRef} className={styles.graphWrapper}>
      <div className={styles.zoomControls}>
        <button onClick={() => svgRef.current?.__zoomIn?.()} aria-label="Zoom in">+</button>
        <button onClick={() => svgRef.current?.__zoomOut?.()} aria-label="Zoom out">−</button>
        <button onClick={() => svgRef.current?.__resetZoom?.()} aria-label="Reset">⤾</button>
      </div>
      <svg ref={svgRef} className={styles.svg} />
    </div>
  );
}

function FeatureMapClient() {
  const dataUrl = useBaseUrl('/feature-map.json');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [layer, setLayer] = useState('all');
  const [category, setCategory] = useState('all');
  const [query, setQuery] = useState('');
  const [showOrphans, setShowOrphans] = useState(false);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    fetch(dataUrl)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [dataUrl]);

  const filteredNodes = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    let nodes = data.nodes.filter((n) => {
      if (layer !== 'all' && n.layer !== layer) return false;
      if (category !== 'all' && n.category !== category) return false;
      if (!q) return true;
      const hay = `${n.name} ${n.id} ${n.description} ${(n.classes || []).join(' ')}`.toLowerCase();
      return hay.includes(q);
    });
    if (!showOrphans && data.edges.length) {
      const connected = new Set();
      for (const e of data.edges) {
        connected.add(e.source);
        connected.add(e.target);
      }
      nodes = nodes.filter((n) => connected.has(n.id));
    }
    return nodes;
  }, [data, layer, category, query, showOrphans]);

  const filteredEdges = useMemo(() => {
    if (!data) return [];
    const ids = new Set(filteredNodes.map((n) => n.id));
    return data.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  }, [data, filteredNodes]);

  if (error) {
    return (
      <div className={styles.error}>
        Failed to load <code>feature-map.json</code>: {error}
        <br />
        Run <code>python scripts/generate_feature_map.py</code> first.
      </div>
    );
  }
  if (!data) return <div className={styles.loading}>Loading feature map…</div>;

  const stats = data.stats;
  const heavy = [...filteredNodes].sort((a, b) => b.weight - a.weight).slice(0, 5);

  return (
    <div className={styles.wrapper}>
      <header className={styles.header}>
        <h1>Feature Map</h1>
        <p>
          Force-directed graph of every module under{' '}
          <code>src/agent_orchestrator/</code>. Node size scales with{' '}
          <strong>weight</strong> ={' '}
          <code>1 + in_degree·2 + classes + log₁₀(lines)</code>. Drag to move,
          scroll to zoom, double-click to open source.
        </p>
        <div className={styles.stats}>
          <span><strong>{stats.total_modules}</strong> modules</span>
          <span><strong>{stats.total_edges}</strong> import edges</span>
          {Object.entries(stats.by_category).map(([k, v]) => (
            <span key={k}>
              <span className={styles.dot} style={{background: CATEGORY_COLORS[k]}} />
              <strong>{v}</strong> {k}
            </span>
          ))}
        </div>
      </header>

      <div className={styles.controls}>
        <label>
          Layer&nbsp;
          <select value={layer} onChange={(e) => setLayer(e.target.value)}>
            <option value="all">All</option>
            <option value="harness">Harness</option>
            <option value="app">App</option>
          </select>
        </label>
        <label>
          Category&nbsp;
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="all">All</option>
            {data.categories.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </label>
        <label className={styles.search}>
          Search&nbsp;
          <input
            type="text"
            placeholder="name, path, class, description…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label className={styles.checkbox}>
          <input
            type="checkbox"
            checked={showOrphans}
            onChange={(e) => setShowOrphans(e.target.checked)}
          />
          Show orphan modules
        </label>
        <span className={styles.counter}>
          {filteredNodes.length} of {stats.total_modules} shown
        </span>
      </div>

      <div className={styles.layout}>
        <div className={styles.canvas}>
          {filteredNodes.length === 0 ? (
            <div className={styles.empty}>No modules match the current filters.</div>
          ) : (
            <ForceGraph
              nodes={filteredNodes}
              edges={filteredEdges}
              githubBase={data.github_base}
              onSelect={setSelected}
            />
          )}
        </div>
        <aside className={styles.sidebar}>
          {selected ? (
            <>
              <div className={styles.sidebarHeader}>
                <span
                  className={styles.tag}
                  style={{background: CATEGORY_COLORS[selected.category]}}>
                  {selected.category}
                </span>
                <button
                  className={styles.close}
                  onClick={() => setSelected(null)}
                  aria-label="Close">×</button>
              </div>
              <h3>{selected.name}</h3>
              <p className={styles.path}><code>{selected.path}</code></p>
              {selected.description && <p>{selected.description}</p>}
              <dl className={styles.metaList}>
                <div><dt>Weight</dt><dd>{selected.weight}</dd></div>
                <div><dt>In-degree</dt><dd>{selected.in_degree}</dd></div>
                <div><dt>Out-degree</dt><dd>{selected.out_degree}</dd></div>
                <div><dt>Lines</dt><dd>{selected.lines}</dd></div>
                <div><dt>Classes</dt><dd>{selected.classes.length || '—'}</dd></div>
              </dl>
              {selected.classes.length > 0 && (
                <p className={styles.classes}>{selected.classes.join(', ')}</p>
              )}
              <a
                className={styles.sourceLink}
                href={`${data.github_base}/src/agent_orchestrator/${selected.path}`}
                target="_blank"
                rel="noopener noreferrer">
                Open on GitHub →
              </a>
            </>
          ) : (
            <>
              <h3>Heaviest nodes</h3>
              <p className={styles.hint}>
                Click any node to inspect it. Double-click to jump to source.
              </p>
              <ol className={styles.heavyList}>
                {heavy.map((n) => (
                  <li key={n.id}>
                    <button onClick={() => setSelected(n)}>
                      <span
                        className={styles.dot}
                        style={{background: CATEGORY_COLORS[n.category]}}
                      />
                      <strong>{n.name}</strong>
                      <span className={styles.weightTag}>w {n.weight}</span>
                    </button>
                  </li>
                ))}
              </ol>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

export default function FeatureMap() {
  return (
    <Layout
      title="Feature Map"
      description="Interactive force-directed graph of every module">
      <BrowserOnly fallback={<div>Loading…</div>}>
        {() => <FeatureMapClient />}
      </BrowserOnly>
    </Layout>
  );
}
