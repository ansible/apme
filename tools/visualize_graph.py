#!/usr/bin/env python3
"""ContentGraph visualizer — generates a standalone HTML file.

Scans an Ansible project, builds a ContentGraph, and renders it as an
interactive HTML page using dagre (layout) and D3.js (rendering).

The output shows execution flow (top-to-bottom), with toggle-able
bounding boxes for plays, roles, blocks, and includes.  Hovering
over a node displays its YAML source.

Examples:
    # Scan a playbook (auto-detects project root as parent directory)
    python tools/visualize_graph.py site.yml

    # Explicit project root
    python tools/visualize_graph.py playbooks/deploy.yml /path/to/project

    # Use built-in test fixture
    python tools/visualize_graph.py
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import cast

# Ensure the local src/ tree is importable (not a stale pip install).
_src = str(Path(__file__).resolve().parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ContentGraph — {title}</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #0d1117; color: #c9d1d9; }}
  #app {{ position: relative; width: 100vw; height: 100vh; overflow: hidden; }}
  svg {{ position: absolute; inset: 0; width: 100%; height: 100%; }}

  .edge-path {{ fill: none; stroke-width: 1.2; }}
  .edge-path.flow {{ stroke: #8b949e; stroke-width: 1.6; }}
  .edge-path.contains {{ stroke: #30363d; stroke-opacity: 0.18; stroke-width: 0.6; }}
  .edge-path.import {{ stroke: #58a6ff; stroke-dasharray: 6 3; }}
  .edge-path.include {{ stroke: #d2a8ff; stroke-dasharray: 4 4; }}
  .edge-path.dependency {{ stroke: #f0883e; stroke-width: 1.8; }}
  .edge-path.notify {{ stroke: #3fb950; stroke-dasharray: 2 4; }}
  .edge-path.listen {{ stroke: #3fb950; stroke-dasharray: 2 4; }}
  .edge-path.data_flow {{ stroke: #f778ba; stroke-dasharray: 8 3; }}
  .edge-path.vars_include {{ stroke: #79c0ff; stroke-dasharray: 3 3; }}
  .edge-path.rescue {{ stroke: #f85149; }}
  .edge-path.always {{ stroke: #d29922; }}
  .edge-path.invokes {{ stroke: #56d364; stroke-dasharray: 5 2; }}

  .group-rect {{ pointer-events: none; rx: 8; ry: 8; }}
  .group-label {{ pointer-events: none; font-size: 10px; font-weight: 600;
    letter-spacing: 0.3px; dominant-baseline: hanging; }}

  .node-rect {{ rx: 4; ry: 4; stroke-width: 1.5; cursor: pointer; }}
  .node-rect.owned {{ fill-opacity: 0.15; }}
  .node-rect.referenced {{ fill-opacity: 0.05; stroke-dasharray: 4 2; }}

  .node-label {{ font-size: 11px; fill: #e6edf3; pointer-events: none; dominant-baseline: central; }}
  .node-type-badge {{ font-size: 9px; fill-opacity: 0.7; pointer-events: none; dominant-baseline: central;
    font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; }}

  #tooltip {{ position: absolute; background: #161b22; border: 1px solid #30363d; border-radius: 6px;
    padding: 10px 14px; font-size: 12px; pointer-events: none; display: none;
    max-width: 600px; max-height: 70vh; overflow-y: auto; z-index: 20; line-height: 1.6;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4); }}
  #tooltip .f {{ color: #8b949e; }}
  #tooltip .v {{ color: #c9d1d9; font-family: ui-monospace, monospace; font-size: 11px; }}
  #tooltip .v.mod {{ color: #d2a8ff; }}
  #tooltip pre {{ margin-top: 6px; padding: 8px; background: #0d1117; border: 1px solid #21262d;
    border-radius: 4px; font-family: ui-monospace, monospace; font-size: 10px; color: #c9d1d9;
    white-space: pre; overflow-x: auto; max-height: 300px; line-height: 1.4; }}

  .overlay {{ position: absolute; background: #161b22; border: 1px solid #30363d;
    border-radius: 6px; z-index: 10; }}

  #legend {{ top: 12px; right: 12px; padding: 12px 16px; font-size: 11px; user-select: none; }}
  #legend h3 {{ margin-bottom: 6px; font-size: 12px; color: #f0f6fc; }}
  .li {{ display: flex; align-items: center; gap: 6px; margin: 3px 0; }}
  .sw {{ width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; }}

  #stats {{ bottom: 12px; left: 12px; padding: 8px 14px; font-size: 12px; }}

  #controls {{ top: 12px; left: 12px; padding: 8px 12px; font-size: 12px; display: flex; gap: 8px;
    flex-wrap: wrap; }}
  #controls button {{ background: #21262d; border: 1px solid #30363d; color: #c9d1d9; border-radius: 4px;
    padding: 4px 10px; cursor: pointer; font-size: 11px; }}
  #controls button:hover {{ background: #30363d; }}
  #controls .sep {{ width: 1px; background: #30363d; margin: 0 2px; }}
  #controls button.tog-on {{ border-color: currentColor; background: #30363d; }}
</style>
</head>
<body>
<div id="app">
  <svg></svg>
  <div id="tooltip"></div>
  <div id="controls" class="overlay">
    <button onclick="fitAll()">Fit</button>
    <button onclick="zoomIn()">+</button>
    <button onclick="zoomOut()">&minus;</button>
    <div class="sep"></div>
    <button id="tog-play" onclick="toggleGroup('play')" style="color:#f0883e">Plays</button>
    <button id="tog-role" onclick="toggleGroup('role')" style="color:#d2a8ff">Roles</button>
    <button id="tog-block" onclick="toggleGroup('block')" style="color:#d29922">Blocks</button>
    <button id="tog-inc" onclick="toggleIncludes()" style="color:#d2a8ff">Includes</button>
  </div>
  <div id="legend" class="overlay">
    <h3>Nodes</h3>
    <div class="li"><div class="sw" style="background:#f85149"></div>Playbook</div>
    <div class="li"><div class="sw" style="background:#f0883e"></div>Play</div>
    <div class="li"><div class="sw" style="background:#d2a8ff"></div>Role</div>
    <div class="li"><div class="sw" style="background:#79c0ff"></div>TaskFile</div>
    <div class="li"><div class="sw" style="background:#58a6ff"></div>Task</div>
    <div class="li"><div class="sw" style="background:#3fb950"></div>Handler</div>
    <div class="li"><div class="sw" style="background:#d29922"></div>Block</div>
    <div class="li"><div class="sw" style="background:#8b949e"></div>VarsFile</div>
    <h3 style="margin-top:8px">Edges</h3>
    <div class="li"><svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="#8b949e" stroke-width="1.6"/></svg>flow (exec order)</div>
    <div class="li"><svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="#f0883e" stroke-width="1.8"/></svg>dependency</div>
    <div class="li"><svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="#f778ba" stroke-width="1.2" stroke-dasharray="8 3"/></svg>data_flow</div>
    <div class="li"><svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="#3fb950" stroke-width="1.2" stroke-dasharray="2 4"/></svg>notify</div>
    <h3 style="margin-top:8px">Groups <span style="font-weight:normal;color:#8b949e">(toggle)</span></h3>
    <div class="li"><div class="sw" style="background:#f0883e;opacity:0.3;border:1px solid #f0883e"></div>Play</div>
    <div class="li"><div class="sw" style="background:#d2a8ff;opacity:0.3;border:1px solid #d2a8ff"></div>Role</div>
    <div class="li"><div class="sw" style="background:#d29922;opacity:0.3;border:1px solid #d29922"></div>Block</div>
    <div class="li"><svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke="#d2a8ff" stroke-width="1.2" stroke-dasharray="4 4"/></svg>Include/Import</div>
  </div>
  <div id="stats" class="overlay">{node_count} nodes &middot; {edge_count} edges &middot; DAG: {is_dag}</div>
</div>
<script>
const graphData = {graph_json};

const nodeColors = {{
  playbook: "#f85149", play: "#f0883e", role: "#d2a8ff",
  taskfile: "#79c0ff", task: "#58a6ff", handler: "#3fb950",
  block: "#d29922", vars_file: "#8b949e", module: "#56d364",
  collection: "#a371f7"
}};

// ── Build dagre graph (TB = top-to-bottom execution flow) ───────────
const g = new dagre.graphlib.Graph({{ multigraph: true, compound: false }});
g.setGraph({{
  rankdir: "TB",
  nodesep: 20,
  ranksep: 40,
  edgesep: 6,
  marginx: 40,
  marginy: 40
}});
g.setDefaultEdgeLabel(() => ({{}}));

function textWidth(str, fontSize) {{
  return str.length * fontSize * 0.58 + 16;
}}

// ── Collect edges for cross-cutting overlay and group boxes ──────
const containsChildren = {{}};
const edgeData = [];
const nodeSet = new Set(graphData.nodes.map(n => n.id));

graphData.edges.forEach((e, i) => {{
  if (!nodeSet.has(e.source) || !nodeSet.has(e.target)) return;
  const type = e.edge_type || "contains";
  const pos = e.position || 0;
  edgeData.push({{ source: e.source, target: e.target, type, pos, idx: i }});
  if (type === "contains") {{
    if (!containsChildren[e.source]) containsChildren[e.source] = [];
    containsChildren[e.source].push({{ target: e.target, pos }});
  }}
}});

Object.values(containsChildren).forEach(arr => arr.sort((a, b) => a.pos - b.pos));

// ── Add nodes ──────────────────────────────────────────────────────
const nodeMap = {{}};
graphData.nodes.forEach(n => {{
  const d = n.data;
  const nt = d.node_type || "task";
  const name = d.name || n.id.split("/").pop() || n.id;
  const label = name.length > 40 ? name.slice(0, 38) + "\u2026" : name;
  const modLabel = (d.module || "").length > 35 ? d.module.slice(0, 33) + "\u2026" : (d.module || "");
  const w = Math.max(textWidth(label, 11), modLabel ? textWidth(modLabel, 9) : 0, 70);
  const h = d.module ? 38 : 26;
  nodeMap[n.id] = {{
    id: n.id,
    type: nt,
    name: label,
    fullName: name,
    module: d.module || "",
    modLabel,
    file: d.file_path || "",
    line: d.line_start || 0,
    scope: d.scope || "owned",
    yaml: d.yaml_lines || "",
    w, h
  }};
  g.setNode(n.id, {{ width: w, height: h }});
}});

// ── Execution chain from pre-computed edges ──────────────────────
const execEdges = (graphData.execution_edges || []).filter(
  e => nodeSet.has(e.source) && nodeSet.has(e.target)
);
execEdges.forEach((e, i) => {{
  g.setEdge(e.source, e.target, {{ minlen: 1 }}, "exec_" + i);
}});

dagre.layout(g);

// ── Render with D3 ────────────────────────────────────────────────
const svg = d3.select("svg");
const app = document.getElementById("app");
const W = app.clientWidth, H = app.clientHeight;
svg.attr("viewBox", [0, 0, W, H]);

const container = svg.append("g");
const zoomBehavior = d3.zoom().scaleExtent([0.02, 4])
  .on("zoom", ev => container.attr("transform", ev.transform));
svg.call(zoomBehavior);

// Arrow markers
const markerColors = {{
  flow: "#8b949e", contains: "#30363d", import: "#58a6ff", include: "#d2a8ff",
  dependency: "#f0883e", data_flow: "#f778ba", notify: "#3fb950",
  listen: "#3fb950", vars_include: "#79c0ff", rescue: "#f85149",
  always: "#d29922", invokes: "#56d364"
}};
const defs = svg.append("defs");
Object.entries(markerColors).forEach(([type, color]) => {{
  defs.append("marker").attr("id", "arr-" + type)
    .attr("viewBox", "0 -4 8 8").attr("refX", 8).attr("refY", 0)
    .attr("markerWidth", 5).attr("markerHeight", 5).attr("orient", "auto")
    .append("path").attr("d", "M0,-3L8,0L0,3").attr("fill", color);
}});

const groupLayer = container.append("g").attr("class", "group-layer");

// ── Nearest-edge routing ─────────────────────────────────────────
const flowGroup = container.append("g");

function edgePoint(nodeId, toX, toY) {{
  const dn = g.node(nodeId), nm = nodeMap[nodeId];
  if (!dn || !nm) return null;
  const cx = dn.x, cy = dn.y, hw = nm.w / 2, hh = nm.h / 2;
  const dx = toX - cx, dy = toY - cy;
  if (dx === 0 && dy === 0) return {{ x: cx, y: cy + hh, nx: 0, ny: 1 }};
  const sx = Math.abs(dx) > 0.001 ? hw / Math.abs(dx) : 1e6;
  const sy = Math.abs(dy) > 0.001 ? hh / Math.abs(dy) : 1e6;
  const s = Math.min(sx, sy);
  let nx = 0, ny = 0;
  if (s === sx) nx = dx > 0 ? 1 : -1; else ny = dy > 0 ? 1 : -1;
  return {{ x: cx + dx * s, y: cy + dy * s, nx, ny }};
}}

function drawEdge(group, srcId, tgtId, cls) {{
  const sn = g.node(srcId), tn = g.node(tgtId);
  if (!sn || !tn || !nodeMap[srcId] || !nodeMap[tgtId]) return;
  const p1 = edgePoint(srcId, tn.x, tn.y);
  const p2 = edgePoint(tgtId, sn.x, sn.y);
  if (!p1 || !p2) return;
  const dist = Math.hypot(p2.x - p1.x, p2.y - p1.y);
  const cp = Math.min(dist * 0.4, 60);
  group.append("path")
    .attr("class", "edge-path " + cls)
    .attr("d", `M${{p1.x}},${{p1.y}} C${{p1.x + p1.nx * cp}},${{p1.y + p1.ny * cp}} ${{p2.x + p2.nx * cp}},${{p2.y + p2.ny * cp}} ${{p2.x}},${{p2.y}}`)
    .attr("marker-end", "url(#arr-" + cls.split(" ")[0] + ")");
}}

// ── Flow edges (from pre-computed execution_edges) ───────────────
execEdges.forEach(e => drawEdge(flowGroup, e.source, e.target, "flow"));

// ── Cross-cutting edges (dependency, notify, etc.) ───────────────
const xEdgeGroup = container.append("g");
edgeData.forEach(e => {{
  if (e.type === "contains" || e.type === "include" || e.type === "import") return;
  drawEdge(xEdgeGroup, e.source, e.target, e.type);
}});

// ── Nodes ────────────────────────────────────────────────────────
const tooltip = d3.select("#tooltip");
const nodeGroup = container.append("g");

Object.values(nodeMap).forEach(n => {{
  const dn = g.node(n.id);
  if (!dn) return;
  const x = dn.x - n.w / 2;
  const y = dn.y - n.h / 2;
  const color = nodeColors[n.type] || "#484f58";
  const grp = nodeGroup.append("g").attr("transform", `translate(${{x}},${{y}})`);

  grp.append("rect")
    .attr("class", "node-rect " + n.scope)
    .attr("width", n.w).attr("height", n.h)
    .attr("fill", color).attr("stroke", color);

  if (n.module) {{
    grp.append("text").attr("class", "node-label")
      .attr("x", 8).attr("y", 12).text(n.name);
    grp.append("text").attr("class", "node-type-badge")
      .attr("x", 8).attr("y", 28)
      .attr("fill", color)
      .text(n.modLabel);
  }} else {{
    grp.append("text").attr("class", "node-label")
      .attr("x", 8).attr("y", n.h / 2).text(n.name);
  }}

  grp.on("mouseover", ev => {{
    const esc = s => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    let h = `<span class="f">type:</span> <span class="v">${{esc(n.type)}}</span>`;
    if (n.fullName) h += ` &middot; <span class="v">${{esc(n.fullName)}}</span>`;
    h += `<br>`;
    if (n.module) h += `<span class="f">module:</span> <span class="v mod">${{esc(n.module)}}</span><br>`;
    if (n.file) h += `<span class="f">file:</span> <span class="v">${{esc(n.file)}}</span>`;
    if (n.line) h += `:<span class="v">${{n.line}}</span>`;
    if (n.file) h += `<br>`;
    if (n.yaml) h += `<pre>${{esc(n.yaml)}}</pre>`;
    tooltip.html(h).style("display", "block");
  }}).on("mousemove", ev => {{
    tooltip.style("left", (ev.clientX + 14) + "px").style("top", (ev.clientY - 14) + "px");
  }}).on("mouseout", () => tooltip.style("display", "none"));
}});

// ── Fit / zoom ───────────────────────────────────────────────────
function fitAll() {{
  const gInfo = g.graph();
  const gw = gInfo.width || 800, gh = gInfo.height || 600;
  const scale = Math.min(W / (gw + 80), H / (gh + 80), 1.5) * 0.9;
  const tx = (W - gw * scale) / 2;
  const ty = (H - gh * scale) / 2;
  svg.transition().duration(500).call(zoomBehavior.transform,
    d3.zoomIdentity.translate(tx, ty).scale(scale));
}}
function zoomIn() {{ svg.transition().duration(300).call(zoomBehavior.scaleBy, 1.4); }}
function zoomOut() {{ svg.transition().duration(300).call(zoomBehavior.scaleBy, 0.7); }}

fitAll();

// ── Group bounding-box computation ───────────────────────────────
const groupColors = {{ play: "#f0883e", role: "#d2a8ff", block: "#d29922" }};
const groupState = {{ play: false, role: false, block: false }};

function descendants(nodeId) {{
  const result = [];
  const stack = [nodeId];
  const visited = new Set();
  while (stack.length) {{
    const id = stack.pop();
    if (visited.has(id)) continue;
    visited.add(id);
    const ch = containsChildren[id];
    if (ch) ch.forEach(c => {{ result.push(c.target); stack.push(c.target); }});
  }}
  return result;
}}

function boundingBox(ids, pad) {{
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  let valid = false;
  ids.forEach(id => {{
    const dn = g.node(id), nm = nodeMap[id];
    if (!dn || !nm) return;
    valid = true;
    x0 = Math.min(x0, dn.x - nm.w / 2);
    y0 = Math.min(y0, dn.y - nm.h / 2);
    x1 = Math.max(x1, dn.x + nm.w / 2);
    y1 = Math.max(y1, dn.y + nm.h / 2);
  }});
  if (!valid) return null;
  return {{ x: x0 - pad, y: y0 - pad, w: (x1 - x0) + 2 * pad, h: (y1 - y0) + 2 * pad }};
}}

function renderGroups(type) {{
  groupLayer.selectAll(".grp-" + type).remove();
  if (!groupState[type]) return;
  const color = groupColors[type];
  const grp = groupLayer.append("g").attr("class", "grp-" + type);
  Object.values(nodeMap).filter(n => n.type === type).forEach(n => {{
    const b = boundingBox([n.id, ...descendants(n.id)], 14);
    if (!b) return;
    grp.append("rect").attr("class", "group-rect")
      .attr("x", b.x).attr("y", b.y).attr("width", b.w).attr("height", b.h)
      .attr("fill", color).attr("fill-opacity", 0.06)
      .attr("stroke", color).attr("stroke-opacity", 0.35).attr("stroke-width", 1.5);
    grp.append("text").attr("class", "group-label")
      .attr("x", b.x + 6).attr("y", b.y + 4)
      .attr("fill", color).attr("fill-opacity", 0.7).text(n.fullName);
  }});
}}

function toggleGroup(type) {{
  groupState[type] = !groupState[type];
  document.getElementById("tog-" + type).classList.toggle("tog-on", groupState[type]);
  renderGroups(type);
}}

// ── Include/Import bounding-box toggle ───────────────────────────
let incVisible = false;
function toggleIncludes() {{
  incVisible = !incVisible;
  document.getElementById("tog-inc").classList.toggle("tog-on", incVisible);
  groupLayer.selectAll(".grp-include").remove();
  if (!incVisible) return;
  const color = "#d2a8ff";
  const grp = groupLayer.append("g").attr("class", "grp-include");
  edgeData.forEach(e => {{
    if (e.type !== "include" && e.type !== "import") return;
    const b = boundingBox([e.source, e.target, ...descendants(e.target)], 14);
    if (!b) return;
    const srcName = nodeMap[e.source] ? nodeMap[e.source].fullName : "";
    grp.append("rect").attr("class", "group-rect")
      .attr("x", b.x).attr("y", b.y).attr("width", b.w).attr("height", b.h)
      .attr("fill", color).attr("fill-opacity", 0.06)
      .attr("stroke", color).attr("stroke-opacity", 0.35)
      .attr("stroke-width", 1.5).attr("stroke-dasharray", "4 4");
    grp.append("text").attr("class", "group-label")
      .attr("x", b.x + 6).attr("y", b.y + 4)
      .attr("fill", color).attr("fill-opacity", 0.7)
      .text(e.type + ": " + srcName);
  }});
}}
</script>
</body>
</html>
"""


def main() -> None:
    """Scan an Ansible project and write an interactive graph.html."""
    from apme_engine.engine.graph_builder import GraphBuilder
    from apme_engine.runner import run_scan

    parser = argparse.ArgumentParser(
        description="Visualize an Ansible project's ContentGraph as interactive HTML.",
    )
    parser.add_argument(
        "playbook",
        nargs="?",
        help="Path to the playbook YAML file (defaults to built-in test fixture).",
    )
    parser.add_argument(
        "project_root",
        nargs="?",
        help="Project root directory (defaults to playbook's parent directory).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="graph.html",
        help="Output HTML file path (default: graph.html).",
    )
    args = parser.parse_args()

    fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "terrible-playbook"

    if args.playbook:
        pb = Path(args.playbook)
        if pb.is_dir():
            project_root = args.project_root or str(pb)
            site = pb / "site.yml"
            playbook_path = str(site) if site.exists() else str(pb)
        else:
            playbook_path = str(pb)
            project_root = args.project_root or str(pb.parent)
    else:
        playbook_path = str(fixture / "site.yml")
        project_root = str(fixture)

    print(f"Scanning: {playbook_path}")
    print(f"Root:     {project_root}")

    context = run_scan(playbook_path, project_root, include_scandata=True)
    sd = context.scandata
    if sd is None:
        print("ERROR: run_scan produced no scandata", file=sys.stderr)
        sys.exit(1)

    builder = GraphBuilder(
        cast(dict[str, object], sd.root_definitions),
        cast(dict[str, object], sd.ext_definitions),
    )
    graph = builder.build()

    graph_dict = graph.to_dict()
    # Prevent </script> in YAML content from breaking out of the script tag.
    graph_json = json.dumps(graph_dict, default=str).replace("</", "<\\/")

    title = html.escape(Path(playbook_path).name)
    html_out = HTML_TEMPLATE.format(
        title=title,
        graph_json=graph_json,
        node_count=graph.node_count(),
        edge_count=graph.edge_count(),
        is_dag="yes" if graph.is_acyclic() else "no",
    )

    out = Path(args.output)
    out.write_text(html_out, encoding="utf-8")
    print(f"\nGraph: {graph.node_count()} nodes, {graph.edge_count()} edges, DAG: {graph.is_acyclic()}")
    print(f"Written to: {out.resolve()}")
    print(f"Open:       xdg-open {out}")


if __name__ == "__main__":
    main()
