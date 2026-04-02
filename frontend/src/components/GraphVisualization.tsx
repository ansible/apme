import { useCallback, useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import * as dagre from 'dagre';
import type { GraphData } from '../services/api';

const NODE_COLORS: Record<string, string> = {
  playbook: '#f85149',
  play: '#f0883e',
  role: '#d2a8ff',
  taskfile: '#79c0ff',
  task: '#58a6ff',
  handler: '#3fb950',
  block: '#d29922',
  vars_file: '#8b949e',
  module: '#56d364',
  collection: '#a371f7',
};

const MARKER_COLORS: Record<string, string> = {
  flow: '#8b949e',
  contains: '#30363d',
  import: '#58a6ff',
  include: '#d2a8ff',
  dependency: '#f0883e',
  data_flow: '#f778ba',
  notify: '#3fb950',
  listen: '#3fb950',
  vars_include: '#79c0ff',
  rescue: '#f85149',
  always: '#d29922',
  invokes: '#56d364',
  py_imports: '#a371f7',
};

const GROUP_COLORS: Record<string, string> = {
  play: '#f0883e',
  role: '#d2a8ff',
  block: '#d29922',
};

interface NodeInfo {
  id: string;
  type: string;
  name: string;
  fullName: string;
  module: string;
  modLabel: string;
  file: string;
  line: number;
  scope: string;
  yaml: string;
  w: number;
  h: number;
}

function textWidth(str: string, fontSize: number): number {
  return str.length * fontSize * 0.58 + 16;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

interface Props {
  data: GraphData;
}

export function GraphVisualization({ data }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [groupState, setGroupState] = useState<Record<string, boolean>>({
    play: false,
    role: false,
    block: false,
  });
  const [incVisible, setIncVisible] = useState(false);

  const graphRef = useRef<dagre.graphlib.Graph | null>(null);
  const nodeMapRef = useRef<Record<string, NodeInfo>>({});
  const containsChildrenRef = useRef<Record<string, Array<{ target: string; pos: number }>>>({});
  const edgeDataRef = useRef<Array<{ source: string; target: string; type: string; pos: number }>>([]);
  const zoomBehaviorRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const groupLayerRef = useRef<d3.Selection<SVGGElement, unknown, null, undefined> | null>(null);

  const buildGraph = useCallback(() => {
    if (!svgRef.current || !containerRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const W = containerRef.current.clientWidth;
    const H = containerRef.current.clientHeight;
    svg.attr('viewBox', `0 0 ${W} ${H}`);

    const g = new dagre.graphlib.Graph({ multigraph: true, compound: false });
    g.setGraph({
      rankdir: 'TB',
      nodesep: 20,
      ranksep: 40,
      edgesep: 6,
      marginx: 40,
      marginy: 40,
    });
    g.setDefaultEdgeLabel(() => ({}));
    graphRef.current = g;

    const nodeSet = new Set(data.nodes.map((n) => n.id));
    const containsChildren: Record<string, Array<{ target: string; pos: number }>> = {};
    const edgeData: Array<{ source: string; target: string; type: string; pos: number }> = [];

    data.edges.forEach((e) => {
      if (!nodeSet.has(e.source) || !nodeSet.has(e.target)) return;
      const type = e.edge_type || 'contains';
      const pos = e.position || 0;
      edgeData.push({ source: e.source, target: e.target, type, pos });
      if (type === 'contains') {
        if (!containsChildren[e.source]) containsChildren[e.source] = [];
        containsChildren[e.source]!.push({ target: e.target, pos });
      }
    });

    Object.values(containsChildren).forEach((arr) => arr.sort((a, b) => a.pos - b.pos));
    containsChildrenRef.current = containsChildren;
    edgeDataRef.current = edgeData;

    const nodeMap: Record<string, NodeInfo> = {};
    data.nodes.forEach((n) => {
      const d = n.data as Record<string, unknown>;
      const nt = (d.node_type as string) || 'task';
      const rawName = (d.name as string) || n.id.split('/').pop() || n.id;
      const label = rawName.length > 40 ? rawName.slice(0, 38) + '\u2026' : rawName;
      const mod = (d.module as string) || '';
      const modLabel = mod.length > 35 ? mod.slice(0, 33) + '\u2026' : mod;
      const w = Math.max(textWidth(label, 11), modLabel ? textWidth(modLabel, 9) : 0, 70);
      const h = mod ? 38 : 26;
      nodeMap[n.id] = {
        id: n.id,
        type: nt,
        name: label,
        fullName: rawName,
        module: mod,
        modLabel,
        file: (d.file_path as string) || '',
        line: (d.line_start as number) || 0,
        scope: (d.scope as string) || 'owned',
        yaml: (d.yaml_lines as string) || '',
        w,
        h,
      };
      g.setNode(n.id, { width: w, height: h });
    });
    nodeMapRef.current = nodeMap;

    const execEdges = (data.execution_edges || []).filter(
      (e) => nodeSet.has(e.source) && nodeSet.has(e.target),
    );
    execEdges.forEach((e, i) => {
      g.setEdge(e.source, e.target, { minlen: 1 }, 'exec_' + i);
    });

    dagre.layout(g);

    const container = svg.append('g');
    const zoomBehavior = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.02, 4])
      .on('zoom', (ev) => container.attr('transform', ev.transform));
    svg.call(zoomBehavior);
    zoomBehaviorRef.current = zoomBehavior;

    const defs = svg.append('defs');
    Object.entries(MARKER_COLORS).forEach(([type, color]) => {
      defs
        .append('marker')
        .attr('id', 'arr-' + type)
        .attr('viewBox', '0 -4 8 8')
        .attr('refX', 8)
        .attr('refY', 0)
        .attr('markerWidth', 5)
        .attr('markerHeight', 5)
        .attr('orient', 'auto')
        .append('path')
        .attr('d', 'M0,-3L8,0L0,3')
        .attr('fill', color);
    });

    const groupLayer = container.append('g').attr('class', 'group-layer');
    groupLayerRef.current = groupLayer;

    function edgePoint(nodeId: string, toX: number, toY: number) {
      const dn = g.node(nodeId);
      const nm = nodeMap[nodeId];
      if (!dn || !nm) return null;
      const cx = dn.x, cy = dn.y, hw = nm.w / 2, hh = nm.h / 2;
      const dx = toX - cx, dy = toY - cy;
      if (dx === 0 && dy === 0) return { x: cx, y: cy + hh, nx: 0, ny: 1 };
      const sx = Math.abs(dx) > 0.001 ? hw / Math.abs(dx) : 1e6;
      const sy = Math.abs(dy) > 0.001 ? hh / Math.abs(dy) : 1e6;
      const s = Math.min(sx, sy);
      let nx = 0, ny = 0;
      if (s === sx) nx = dx > 0 ? 1 : -1;
      else ny = dy > 0 ? 1 : -1;
      return { x: cx + dx * s, y: cy + dy * s, nx, ny };
    }

    function drawEdge(
      group: d3.Selection<SVGGElement, unknown, null, undefined>,
      srcId: string,
      tgtId: string,
      cls: string,
    ) {
      const sn = g.node(srcId);
      const tn = g.node(tgtId);
      if (!sn || !tn || !nodeMap[srcId] || !nodeMap[tgtId]) return;
      const p1 = edgePoint(srcId, tn.x, tn.y);
      const p2 = edgePoint(tgtId, sn.x, sn.y);
      if (!p1 || !p2) return;
      const dist = Math.hypot(p2.x - p1.x, p2.y - p1.y);
      const cp = Math.min(dist * 0.4, 60);
      group
        .append('path')
        .attr('class', 'graph-edge ' + cls)
        .attr(
          'd',
          `M${p1.x},${p1.y} C${p1.x + p1.nx * cp},${p1.y + p1.ny * cp} ${p2.x + p2.nx * cp},${p2.y + p2.ny * cp} ${p2.x},${p2.y}`,
        )
        .attr('marker-end', 'url(#arr-' + cls.split(' ')[0] + ')');
    }

    const flowGroup = container.append('g');
    execEdges.forEach((e) => drawEdge(flowGroup, e.source, e.target, 'flow'));

    const xEdgeGroup = container.append('g');
    edgeData.forEach((e) => {
      if (e.type === 'contains' || e.type === 'include' || e.type === 'import') return;
      drawEdge(xEdgeGroup, e.source, e.target, e.type);
    });

    const tooltip = d3.select(containerRef.current).select('.graph-tooltip');
    const nodeGroup = container.append('g');

    Object.values(nodeMap).forEach((n) => {
      const dn = g.node(n.id);
      if (!dn) return;
      const x = dn.x - n.w / 2;
      const y = dn.y - n.h / 2;
      const color = NODE_COLORS[n.type] || '#484f58';
      const grp = nodeGroup.append('g').attr('transform', `translate(${x},${y})`);

      grp
        .append('rect')
        .attr('class', 'graph-node ' + n.scope)
        .attr('width', n.w)
        .attr('height', n.h)
        .attr('fill', color)
        .attr('stroke', color);

      if (n.module) {
        grp
          .append('text')
          .attr('class', 'graph-node-label')
          .attr('x', 8)
          .attr('y', 12)
          .text(n.name);
        grp
          .append('text')
          .attr('class', 'graph-node-badge')
          .attr('x', 8)
          .attr('y', 28)
          .attr('fill', color)
          .text(n.modLabel);
      } else {
        grp
          .append('text')
          .attr('class', 'graph-node-label')
          .attr('x', 8)
          .attr('y', n.h / 2)
          .text(n.name);
      }

      grp
        .on('mouseover', () => {
          let h = `<span class="f">type:</span> <span class="v">${escapeHtml(n.type)}</span>`;
          if (n.fullName)
            h += ` &middot; <span class="v">${escapeHtml(n.fullName)}</span>`;
          h += '<br>';
          if (n.module)
            h += `<span class="f">module:</span> <span class="v mod">${escapeHtml(n.module)}</span><br>`;
          if (n.file) h += `<span class="f">file:</span> <span class="v">${escapeHtml(n.file)}</span>`;
          if (n.line) h += `:<span class="v">${n.line}</span>`;
          if (n.file) h += '<br>';
          if (n.yaml) h += `<pre>${escapeHtml(n.yaml)}</pre>`;
          tooltip.html(h).style('display', 'block');
        })
        .on('mousemove', (ev) => {
          const rect = containerRef.current!.getBoundingClientRect();
          tooltip
            .style('left', ev.clientX - rect.left + 14 + 'px')
            .style('top', ev.clientY - rect.top - 14 + 'px');
        })
        .on('mouseout', () => tooltip.style('display', 'none'));
    });

    const gInfo = g.graph();
    const gw = gInfo.width || 800;
    const gh = gInfo.height || 600;
    const scale = Math.min(W / (gw + 80), H / (gh + 80), 1.5) * 0.9;
    const tx = (W - gw * scale) / 2;
    const ty = (H - gh * scale) / 2;
    svg
      .transition()
      .duration(500)
      .call(zoomBehavior.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
  }, [data]);

  useEffect(() => {
    buildGraph();
  }, [buildGraph]);

  const fitAll = useCallback(() => {
    if (!svgRef.current || !containerRef.current || !graphRef.current || !zoomBehaviorRef.current)
      return;
    const svg = d3.select(svgRef.current);
    const W = containerRef.current.clientWidth;
    const H = containerRef.current.clientHeight;
    const gInfo = graphRef.current.graph();
    const gw = gInfo.width || 800;
    const gh = gInfo.height || 600;
    const scale = Math.min(W / (gw + 80), H / (gh + 80), 1.5) * 0.9;
    const tx = (W - gw * scale) / 2;
    const ty = (H - gh * scale) / 2;
    svg
      .transition()
      .duration(500)
      .call(zoomBehaviorRef.current.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
  }, []);

  const zoomIn = useCallback(() => {
    if (!svgRef.current || !zoomBehaviorRef.current) return;
    d3.select(svgRef.current).transition().duration(300).call(zoomBehaviorRef.current.scaleBy, 1.4);
  }, []);

  const zoomOut = useCallback(() => {
    if (!svgRef.current || !zoomBehaviorRef.current) return;
    d3.select(svgRef.current).transition().duration(300).call(zoomBehaviorRef.current.scaleBy, 0.7);
  }, []);

  const renderGroups = useCallback(
    (type: string, visible: boolean) => {
      const groupLayer = groupLayerRef.current;
      const g = graphRef.current;
      const nodeMap = nodeMapRef.current;
      const containsChildren = containsChildrenRef.current;
      if (!groupLayer || !g) return;

      groupLayer.selectAll('.grp-' + type).remove();
      if (!visible) return;
      const color = GROUP_COLORS[type];
      if (!color) return;

      function descendants(nodeId: string): string[] {
        const result: string[] = [];
        const stack = [nodeId];
        const visited = new Set<string>();
        while (stack.length) {
          const id = stack.pop()!;
          if (visited.has(id)) continue;
          visited.add(id);
          const ch = containsChildren[id];
          if (ch) ch.forEach((c) => { result.push(c.target); stack.push(c.target); });
        }
        return result;
      }

      function boundingBox(ids: string[], pad: number) {
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        let valid = false;
        ids.forEach((id) => {
          const dn = g!.node(id);
          const nm = nodeMap[id];
          if (!dn || !nm) return;
          valid = true;
          x0 = Math.min(x0, dn.x - nm.w / 2);
          y0 = Math.min(y0, dn.y - nm.h / 2);
          x1 = Math.max(x1, dn.x + nm.w / 2);
          y1 = Math.max(y1, dn.y + nm.h / 2);
        });
        if (!valid) return null;
        return { x: x0 - pad, y: y0 - pad, w: x1 - x0 + 2 * pad, h: y1 - y0 + 2 * pad };
      }

      const grp = groupLayer.append('g').attr('class', 'grp-' + type);
      Object.values(nodeMap)
        .filter((n) => n.type === type)
        .forEach((n) => {
          const b = boundingBox([n.id, ...descendants(n.id)], 14);
          if (!b) return;
          grp
            .append('rect')
            .attr('class', 'graph-group-rect')
            .attr('x', b.x)
            .attr('y', b.y)
            .attr('width', b.w)
            .attr('height', b.h)
            .attr('fill', color)
            .attr('fill-opacity', 0.06)
            .attr('stroke', color)
            .attr('stroke-opacity', 0.35)
            .attr('stroke-width', 1.5);
          grp
            .append('text')
            .attr('class', 'graph-group-label')
            .attr('x', b.x + 6)
            .attr('y', b.y + 4)
            .attr('fill', color)
            .attr('fill-opacity', 0.7)
            .text(n.fullName);
        });
    },
    [],
  );

  const renderIncludes = useCallback(
    (visible: boolean) => {
      const groupLayer = groupLayerRef.current;
      const g = graphRef.current;
      const nodeMap = nodeMapRef.current;
      const edgeData = edgeDataRef.current;
      const containsChildren = containsChildrenRef.current;
      if (!groupLayer || !g) return;

      groupLayer.selectAll('.grp-include').remove();
      if (!visible) return;
      const color = '#d2a8ff';

      function descendants(nodeId: string): string[] {
        const result: string[] = [];
        const stack = [nodeId];
        const visited = new Set<string>();
        while (stack.length) {
          const id = stack.pop()!;
          if (visited.has(id)) continue;
          visited.add(id);
          const ch = containsChildren[id];
          if (ch) ch.forEach((c) => { result.push(c.target); stack.push(c.target); });
        }
        return result;
      }

      function boundingBox(ids: string[], pad: number) {
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        let valid = false;
        ids.forEach((id) => {
          const dn = g!.node(id);
          const nm = nodeMap[id];
          if (!dn || !nm) return;
          valid = true;
          x0 = Math.min(x0, dn.x - nm.w / 2);
          y0 = Math.min(y0, dn.y - nm.h / 2);
          x1 = Math.max(x1, dn.x + nm.w / 2);
          y1 = Math.max(y1, dn.y + nm.h / 2);
        });
        if (!valid) return null;
        return { x: x0 - pad, y: y0 - pad, w: x1 - x0 + 2 * pad, h: y1 - y0 + 2 * pad };
      }

      const grp = groupLayer.append('g').attr('class', 'grp-include');
      edgeData.forEach((e) => {
        if (e.type !== 'include' && e.type !== 'import') return;
        const b = boundingBox([e.source, e.target, ...descendants(e.target)], 14);
        if (!b) return;
        const srcNode = nodeMap[e.source];
        const srcName = srcNode ? srcNode.fullName : '';
        grp
          .append('rect')
          .attr('class', 'graph-group-rect')
          .attr('x', b.x)
          .attr('y', b.y)
          .attr('width', b.w)
          .attr('height', b.h)
          .attr('fill', color)
          .attr('fill-opacity', 0.06)
          .attr('stroke', color)
          .attr('stroke-opacity', 0.35)
          .attr('stroke-width', 1.5)
          .attr('stroke-dasharray', '4 4');
        grp
          .append('text')
          .attr('class', 'graph-group-label')
          .attr('x', b.x + 6)
          .attr('y', b.y + 4)
          .attr('fill', color)
          .attr('fill-opacity', 0.7)
          .text(e.type + ': ' + srcName);
      });
    },
    [],
  );

  useEffect(() => {
    if (!graphRef.current) return;
    Object.entries(groupState).forEach(([type, visible]) => renderGroups(type, visible));
    renderIncludes(incVisible);
  }, [buildGraph, groupState, incVisible, renderGroups, renderIncludes]);

  const toggleGroup = useCallback(
    (type: string) => {
      setGroupState((prev) => {
        const newVal = !prev[type];
        const next = { ...prev, [type]: newVal };
        renderGroups(type, newVal);
        return next;
      });
    },
    [renderGroups],
  );

  const toggleIncludes = useCallback(() => {
    setIncVisible((prev) => {
      renderIncludes(!prev);
      return !prev;
    });
  }, [renderIncludes]);

  const nodeCount = data.nodes.length;
  const edgeCount = data.edges.length;

  return (
    <div ref={containerRef} className="graph-container">
      <style>{`
        .graph-container { position: relative; width: 100%; height: 70vh; min-height: 400px; background: #0d1117; border-radius: 8px; overflow: hidden; }
        .graph-container svg { position: absolute; inset: 0; width: 100%; height: 100%; }

        .graph-edge { fill: none; stroke: #484f58; stroke-width: 1.2; }
        .graph-edge.flow { stroke: #8b949e; stroke-width: 1.6; }
        .graph-edge.contains { stroke: #30363d; stroke-opacity: 0.18; stroke-width: 0.6; }
        .graph-edge.import { stroke: #58a6ff; stroke-dasharray: 6 3; }
        .graph-edge.include { stroke: #d2a8ff; stroke-dasharray: 4 4; }
        .graph-edge.dependency { stroke: #f0883e; stroke-width: 1.8; }
        .graph-edge.notify { stroke: #3fb950; stroke-dasharray: 2 4; }
        .graph-edge.listen { stroke: #3fb950; stroke-dasharray: 2 4; }
        .graph-edge.data_flow { stroke: #f778ba; stroke-dasharray: 8 3; }
        .graph-edge.vars_include { stroke: #79c0ff; stroke-dasharray: 3 3; }
        .graph-edge.rescue { stroke: #f85149; }
        .graph-edge.always { stroke: #d29922; }
        .graph-edge.invokes { stroke: #56d364; stroke-dasharray: 5 2; }
        .graph-edge.py_imports { stroke: #a371f7; stroke-dasharray: 3 5; }

        .graph-group-rect { pointer-events: none; rx: 8; ry: 8; }
        .graph-group-label { pointer-events: none; font-size: 10px; font-weight: 600; letter-spacing: 0.3px; dominant-baseline: hanging; }

        .graph-node { rx: 4; ry: 4; stroke-width: 1.5; cursor: pointer; }
        .graph-node.owned { fill-opacity: 0.15; }
        .graph-node.referenced { fill-opacity: 0.05; stroke-dasharray: 4 2; }

        .graph-node-label { font-size: 11px; fill: #e6edf3; pointer-events: none; dominant-baseline: central; }
        .graph-node-badge { font-size: 9px; fill-opacity: 0.7; pointer-events: none; dominant-baseline: central; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; }

        .graph-tooltip { position: absolute; background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 10px 14px; font-size: 12px; pointer-events: none; display: none; max-width: 600px; max-height: 70vh; overflow-y: auto; z-index: 20; line-height: 1.6; box-shadow: 0 4px 12px rgba(0,0,0,0.4); color: #c9d1d9; }
        .graph-tooltip .f { color: #8b949e; }
        .graph-tooltip .v { color: #c9d1d9; font-family: ui-monospace, monospace; font-size: 11px; }
        .graph-tooltip .v.mod { color: #d2a8ff; }
        .graph-tooltip pre { margin-top: 6px; padding: 8px; background: #0d1117; border: 1px solid #21262d; border-radius: 4px; font-family: ui-monospace, monospace; font-size: 10px; color: #c9d1d9; white-space: pre; overflow-x: auto; max-height: 300px; line-height: 1.4; }

        .graph-controls { position: absolute; top: 12px; left: 12px; padding: 8px 12px; font-size: 12px; display: flex; gap: 8px; flex-wrap: wrap; background: #161b22; border: 1px solid #30363d; border-radius: 6px; z-index: 10; }
        .graph-controls button { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 11px; }
        .graph-controls button:hover { background: #30363d; }
        .graph-controls .sep { width: 1px; background: #30363d; margin: 0 2px; }
        .graph-controls button.tog-on { border-color: currentColor; background: #30363d; }

        .graph-legend { position: absolute; top: 12px; right: 12px; padding: 12px 16px; font-size: 11px; background: #161b22; border: 1px solid #30363d; border-radius: 6px; z-index: 10; color: #c9d1d9; user-select: none; }
        .graph-legend h3 { margin-bottom: 6px; font-size: 12px; color: #f0f6fc; }
        .graph-legend .li { display: flex; align-items: center; gap: 6px; margin: 3px 0; }
        .graph-legend .sw { width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; }

        .graph-stats { position: absolute; bottom: 12px; left: 12px; padding: 8px 14px; font-size: 12px; background: #161b22; border: 1px solid #30363d; border-radius: 6px; z-index: 10; color: #c9d1d9; }
      `}</style>

      <svg ref={svgRef} />
      <div className="graph-tooltip" />

      <div className="graph-controls">
        <button type="button" aria-label="Fit graph to view" onClick={fitAll}>Fit</button>
        <button type="button" aria-label="Zoom in" onClick={zoomIn}>+</button>
        <button type="button" aria-label="Zoom out" onClick={zoomOut}>&minus;</button>
        <div className="sep" />
        <button
          type="button"
          className={groupState.play ? 'tog-on' : ''}
          style={{ color: '#f0883e' }}
          onClick={() => toggleGroup('play')}
        >
          Plays
        </button>
        <button
          type="button"
          className={groupState.role ? 'tog-on' : ''}
          style={{ color: '#d2a8ff' }}
          onClick={() => toggleGroup('role')}
        >
          Roles
        </button>
        <button
          type="button"
          className={groupState.block ? 'tog-on' : ''}
          style={{ color: '#d29922' }}
          onClick={() => toggleGroup('block')}
        >
          Blocks
        </button>
        <button
          type="button"
          className={incVisible ? 'tog-on' : ''}
          style={{ color: '#d2a8ff' }}
          onClick={toggleIncludes}
        >
          Includes
        </button>
      </div>

      <div className="graph-legend">
        <h3>Nodes</h3>
        <div className="li"><div className="sw" style={{ background: '#f85149' }} />Playbook</div>
        <div className="li"><div className="sw" style={{ background: '#f0883e' }} />Play</div>
        <div className="li"><div className="sw" style={{ background: '#d2a8ff' }} />Role</div>
        <div className="li"><div className="sw" style={{ background: '#79c0ff' }} />TaskFile</div>
        <div className="li"><div className="sw" style={{ background: '#58a6ff' }} />Task</div>
        <div className="li"><div className="sw" style={{ background: '#3fb950' }} />Handler</div>
        <div className="li"><div className="sw" style={{ background: '#d29922' }} />Block</div>
        <div className="li"><div className="sw" style={{ background: '#8b949e' }} />VarsFile</div>
        <h3 style={{ marginTop: 8 }}>Edges</h3>
        <div className="li">
          <svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="#8b949e" strokeWidth="1.6" /></svg>
          flow
        </div>
        <div className="li">
          <svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="#f0883e" strokeWidth="1.8" /></svg>
          dependency
        </div>
        <div className="li">
          <svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="#f778ba" strokeWidth="1.2" strokeDasharray="8 3" /></svg>
          data_flow
        </div>
        <div className="li">
          <svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="#3fb950" strokeWidth="1.2" strokeDasharray="2 4" /></svg>
          notify
        </div>
      </div>

      <div className="graph-stats">
        {nodeCount} nodes &middot; {edgeCount} edges
      </div>
    </div>
  );
}
