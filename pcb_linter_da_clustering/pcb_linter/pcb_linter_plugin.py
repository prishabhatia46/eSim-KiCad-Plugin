import math
import os
import subprocess
import webbrowser
"""
PCB Linter Plugin — KiCad Action Plugin
By Prisha Bhatia, FOSSEE IIT Bombay

Flow:
1. KiCad PCB se components + nets read karo
2. SPICE netlist auto-generate karo (DC .op analysis)
3. ngspice (eSim engine) se real simulation karo
4. Real voltage values se DA Clustering karo
5. HTML Report generate karo
"""

import pcbnew
import networkx as nx
from pyvis.network import Network
import os, math, subprocess, webbrowser

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
HOME       = os.path.expanduser("~")
REPORT_DIR = os.path.join(HOME, "eSim-Workspace", "pcb_linter_output")
SPICE_FILE = os.path.join(REPORT_DIR, "pcblinter_sim.cir")
PLOT_FILE  = os.path.join(REPORT_DIR, "plot_data_v.txt")
os.makedirs(REPORT_DIR, exist_ok=True)
os.chdir(REPORT_DIR)

print("=" * 50)
print("  PCB Linter Plugin — Starting...")
print("=" * 50)

# ─────────────────────────────────────────────
# STEP 1: KiCad PCB se components + nets read
# ─────────────────────────────────────────────
board      = pcbnew.GetBoard()
footprints = board.GetFootprints()

print(f"[PCB Linter] Board: {board.GetFileName()}")
print(f"[PCB Linter] Components found: {len(list(footprints))}")

G                 = nx.Graph()
net_to_components = {}

for f in footprints:
    ref   = f.GetReference()
    value = f.GetValue()
    G.add_node(ref, value=value)
    for pad in f.Pads():
        net_name = pad.GetNetname()
        if net_name and net_name != "":
            if net_name not in net_to_components:
                net_to_components[net_name] = []
            if ref not in net_to_components[net_name]:
                net_to_components[net_name].append(ref)

for net_name, comps in net_to_components.items():
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            G.add_edge(comps[i], comps[j], net=net_name)

print(f"[PCB Linter] Nets found: {len(net_to_components)}")
print(f"[PCB Linter] Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

# ─────────────────────────────────────────────
# STEP 2: Centrality Analysis
# ─────────────────────────────────────────────
dc       = nx.degree_centrality(G)
bc       = nx.betweenness_centrality(G)
findings = []

for node, score in dc.items():
    if score > 0.3:
        findings.append({
            "type"      : "WARNING",
            "component" : node,
            "message"   : f"High fanout — degree centrality: {score:.2f}",
            "fix"       : "Check if this net has too many components connected"
        })

for node, score in bc.items():
    if score > 0.0:
        findings.append({
            "type"      : "ERROR",
            "component" : node,
            "message"   : f"Single Point of Failure — betweenness: {score:.2f}",
            "fix"       : "Add redundant connection"
        })

quality_score = max(0, 100 - (len(findings) * 15))

# ─────────────────────────────────────────────
# STEP 3: Pyvis Interactive Graph
# ─────────────────────────────────────────────
graph_path = os.path.join(REPORT_DIR, "pcb_graph.html")
net_vis    = Network(height="400px", width="100%", bgcolor="#0d1117", font_color="white")

for node in G.nodes():
    score = dc.get(node, 0)
    color = "#f85149" if score > 0.5 else "#d29922" if score > 0.3 else "#58a6ff"
    net_vis.add_node(node, label=node, color=color, title=f"Degree: {score:.2f}")

for edge in G.edges(data=True):
    net_vis.add_edge(edge[0], edge[1], color="#30363d")

net_vis.save_graph(graph_path)
print(f"[PCB Linter] Graph saved: {graph_path}")

# ─────────────────────────────────────────────
# STEP 4: SPICE Netlist Generate (DC .op)
# ─────────────────────────────────────────────
def generate_spice_netlist(footprints, net_to_components, plot_file):
    spice_lines = [
        "* PCB Linter — Auto SPICE Netlist",
        "* By Prisha Bhatia, FOSSEE IIT Bombay",
        ""
    ]

    # Net name map banana
    net_map     = {}
    net_counter = 1
    for net_name in net_to_components.keys():
        if net_name.lower() in ['gnd', 'ground', '/gnd', 'net-(gnd-pad1)', 'pwr_flag']:
            net_map[net_name] = '0'
        else:
            clean = ''.join(
                c for c in net_name.replace("/", "_").replace("-", "_")
                if c.isalnum() or c == '_'
            )
            if not clean or clean[0].isdigit():
                clean = f"net_{net_counter}"
            net_map[net_name] = clean
            net_counter += 1

    has_voltage_source = False
    all_non_gnd_nodes  = set()
    component_lines    = []

    for f in footprints:
        ref      = f.GetReference()
        value    = f.GetValue() or "1k"
        pad_nets = [net_map.get(p.GetNetname(), '0') for p in f.Pads()]
        while len(pad_nets) < 2:
            pad_nets.append('0')
        n1, n2 = pad_nets[0], pad_nets[1]
        r      = ref.upper()

        # non-gnd nodes track karo
        for n in [n1, n2]:
            if n != '0':
                all_non_gnd_nodes.add(n)

        if r.startswith('R'):
            component_lines.append(f"{ref} {n1} {n2} {value}")
        elif r.startswith('C'):
            component_lines.append(f"{ref} {n1} {n2} {value}")
        elif r.startswith('L'):
            component_lines.append(f"{ref} {n1} {n2} {value}")
        elif r.startswith('V'):
            component_lines.append(f"{ref} {n1} 0 DC 5")
            has_voltage_source = True
        elif r.startswith('I'):
            component_lines.append(f"{ref} {n1} {n2} DC 1m")

    spice_lines += component_lines

    # Voltage source nahi hai toh default add karo
    if not has_voltage_source:
        first_node = list(all_non_gnd_nodes)[0] if all_non_gnd_nodes else 'net_1'
        spice_lines.append(f"Vdefault {first_node} 0 DC 5")

    # Floating nodes ko GND se connect karo via load resistor
    # Taaki current flow ho aur voltage divider bane
    floating_counter = 1
    for node in all_non_gnd_nodes:
        spice_lines.append(f"Rload_{floating_counter} {node} 0 100k")
        floating_counter += 1

    spice_lines += [
        "",
        ".op",
        "",
        ".control",
        "run",
        f"print allv > {plot_file}",
        ".endc",
        ".end"
    ]

    return "\n".join(spice_lines)


spice_content = generate_spice_netlist(footprints, net_to_components, PLOT_FILE)

with open(SPICE_FILE, "w") as f:
    f.write(spice_content)

print(f"[PCB Linter] SPICE file generated: {SPICE_FILE}")

# ─────────────────────────────────────────────
# STEP 5: ngspice (eSim engine) se Simulation
# ─────────────────────────────────────────────
print("[PCB Linter] Running simulation (eSim/ngspice engine)...")

try:
    result = subprocess.run(
        ["ngspice", "-b", SPICE_FILE],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPORT_DIR
    )
    if result.returncode == 0:
        simulation_status = "✅ Simulation successful (eSim/ngspice)"
        print("[PCB Linter] Simulation successful!")
    else:
        simulation_status = f"⚠ Simulation warnings"
        print(f"[PCB Linter] Simulation warning: {result.stderr[:200]}")
except FileNotFoundError:
    simulation_status = "❌ ngspice not found"
    print("[PCB Linter] ERROR: ngspice not found!")
except Exception as e:
    simulation_status = f"❌ Error: {str(e)}"
    print(f"[PCB Linter] ERROR: {e}")

# ─────────────────────────────────────────────
# STEP 6: Real Voltage Data Read karo
# ─────────────────────────────────────────────
def read_voltage_data(path):
    try:
        with open(path) as f:
            content = f.read()

        voltage_data = {}
        for line in content.split('\n'):
            line = line.strip()
            # Format: "node_name   value"
            if line and not line.startswith('*') and not line.startswith('-') and not line.startswith('Node'):
                parts = line.replace("=", " ").split()
                if len(parts) == 2:
                    try:
                        node_name = parts[0].lower()
                        # Skip GND, branch currents
                        if node_name == '0' or '#branch' in node_name:
                            continue
                        value = float(parts[1])
                        voltage_data[node_name] = [value]
                    except ValueError:
                        continue

        return voltage_data
    except Exception as e:
        print(f"[PCB Linter] Could not read voltage data: {e}")
        return {}


voltage_data = read_voltage_data(PLOT_FILE)

if voltage_data:
    print(f"[PCB Linter] Real voltage data loaded: {len(voltage_data)} nodes")
    print(f"[PCB Linter] Nodes: {list(voltage_data.keys())}")
    print(f"[PCB Linter] Voltages: {[round(v[0],3) for v in voltage_data.values()]}")
    clustering_source = "✅ Real ngspice/eSim Simulation Data"
else:
    print("[PCB Linter] No voltage data — using component-based clustering")
    component_refs = [f.GetReference() for f in board.GetFootprints()]
    voltage_data   = {}
    for i, ref in enumerate(component_refs):
        voltage_data[ref] = [float(i + 1) * 1.5]
    clustering_source = "⚠ Component-based fallback"

# ─────────────────────────────────────────────
# STEP 7: Attenuation Matrix (αij)
# ─────────────────────────────────────────────
def build_attenuation_matrix(vd):
    nodes = list(vd.keys())
    alpha = {}
    for ni in nodes:
        alpha[ni] = {}
        vi = vd[ni][0] if vd[ni] else 1.0
        for nj in nodes:
            vj = vd[nj][0] if vd[nj] else 1.0
            if ni == nj:
                alpha[ni][nj] = 1.0
            else:
                alpha[ni][nj] = abs(vi / vj) if vj != 0 else 0.0
    return nodes, alpha

# ─────────────────────────────────────────────
# STEP 8: DA Clustering
# ─────────────────────────────────────────────
def da_clustering(nodes, alpha, num_clusters=3, T_start=2.0, T_end=0.0001, cooling=0.92):
    n = len(nodes)
    if n == 0:
        return {}

    k = min(num_clusters, n)

    # Smart initialization — voltage ke basis pe
    import random
    random.seed(42)
    probs = []
    for i in range(n):
        p = [0.01] * k
        # Assign highest prob to cluster based on voltage rank
        vi    = alpha[nodes[i]][nodes[i]]
        rank  = sorted(range(n), key=lambda x: alpha[nodes[x]][nodes[x]])
        idx   = rank.index(i)
        cidx  = min(int(idx * k / n), k - 1)
        p[cidx] = 0.97
        total = sum(p)
        probs.append([x / total for x in p])

    T     = T_start
    iters = 0

    while T > T_end:
        centers = []
        for c in range(k):
            total_weight = sum(probs[i][c] for i in range(n))
            center       = {}
            for nj in nodes:
                center[nj] = sum(
                    probs[i][c] * alpha[nodes[i]].get(nj, 0)
                    for i in range(n)
                ) / (total_weight or 1)
            centers.append(center)

        for i, ni in enumerate(nodes):
            dists = []
            for c in range(k):
                d = sum(
                    (alpha[ni].get(nj, 0) - centers[c].get(nj, 0)) ** 2
                    for nj in nodes
                )
                dists.append(d)

            exps      = []
            min_d     = min(dists)
            for d in dists:
                try:
                    exps.append(math.exp(-(d - min_d) / T))
                except OverflowError:
                    exps.append(float('inf'))

            total_exp = sum(exps)
            if total_exp > 0:
                probs[i] = [e / total_exp for e in exps]
            else:
                probs[i] = [1.0 / k] * k

        T     *= cooling
        iters += 1

    print(f"[PCB Linter] DA Clustering done in {iters} iterations")
    return {nodes[i]: probs[i].index(max(probs[i])) for i in range(n)}


cluster_nodes, alpha_matrix = build_attenuation_matrix(voltage_data)
num_clusters                = min(3, len(cluster_nodes))
cluster_result              = da_clustering(cluster_nodes, alpha_matrix, num_clusters=num_clusters)

cluster_groups = {}
for node, cid in cluster_result.items():
    cluster_groups.setdefault(cid, []).append(node)

# ─────────────────────────────────────────────
# STEP 9: HTML Report Generate
# ─────────────────────────────────────────────
CLUSTER_COLORS = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#bc8cff"]

def make_html_report():
    findings_rows = ""
    for f in findings:
        icon = "🔴" if f["type"] == "ERROR" else "🟡"
        findings_rows += f"""
        <tr>
            <td>{icon} {f['type']}</td>
            <td><code>{f['component']}</code></td>
            <td>{f['message']}</td>
            <td>{f['fix']}</td>
        </tr>"""

    if not findings_rows:
        findings_rows = '<tr><td colspan="4" style="text-align:center; color:#3fb950;">✅ No issues found!</td></tr>'

    cluster_cards = ""
    for cid, nodes in cluster_groups.items():
        color      = CLUSTER_COLORS[cid % len(CLUSTER_COLORS)]
        nodes_html = ""
        for n in nodes:
            v = voltage_data.get(n, [0])[0]
            nodes_html += f'<span class="node-tag">{n} <small>({v:.2f}V)</small></span>'
        cluster_cards += f"""
        <div class="cluster-card" style="border-left: 4px solid {color};">
            <h3 style="color:{color};">Cluster {cid + 1}</h3>
            <p>{len(nodes)} node(s)</p>
            <div>{nodes_html}</div>
        </div>"""

    matrix_headers = "".join(f"<th>{n}</th>" for n in cluster_nodes)
    matrix_rows    = ""
    for ni in cluster_nodes:
        row = f"<tr><td><strong>{ni}</strong></td>"
        for nj in cluster_nodes:
            val        = alpha_matrix.get(ni, {}).get(nj, 0)
            cell_color = "#3fb95033" if val > 0.8 else "#d2992233" if val > 0.4 else ""
            row       += f'<td style="background:{cell_color}">{val:.3f}</td>'
        row        += "</tr>"
        matrix_rows += row

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>PCB Linter Report — FOSSEE IIT Bombay</title>
<style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ font-family:'Segoe UI',sans-serif; background:#0d1117; color:#c9d1d9; padding:20px; }}
    .header {{ background:linear-gradient(135deg,#161b22,#1f2937); border:1px solid #30363d; border-radius:12px; padding:30px; margin-bottom:24px; text-align:center; }}
    .header h1 {{ color:#58a6ff; font-size:2em; margin-bottom:8px; }}
    .header p  {{ color:#8b949e; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:16px; margin-bottom:24px; }}
    .card {{ background:#161b22; border:1px solid #30363d; border-radius:10px; padding:20px; text-align:center; }}
    .card .value {{ font-size:2.5em; font-weight:bold; color:#58a6ff; }}
    .card .label {{ color:#8b949e; margin-top:4px; }}
    .section {{ background:#161b22; border:1px solid #30363d; border-radius:10px; padding:24px; margin-bottom:24px; }}
    .section h2 {{ color:#58a6ff; margin-bottom:16px; font-size:1.3em; }}
    table {{ width:100%; border-collapse:collapse; font-size:0.9em; }}
    th {{ background:#21262d; color:#8b949e; padding:10px; text-align:left; border-bottom:1px solid #30363d; }}
    td {{ padding:10px; border-bottom:1px solid #21262d; }}
    tr:hover td {{ background:#1c2128; }}
    .cluster-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; }}
    .cluster-card {{ background:#0d1117; border-radius:8px; padding:16px; }}
    .node-tag {{ display:inline-block; background:#21262d; border-radius:4px; padding:4px 10px; margin:3px; font-size:0.85em; color:#58a6ff; }}
    .sim-status {{ background:#1c2128; border-radius:8px; padding:12px 16px; margin-bottom:16px; font-family:monospace; }}
    iframe {{ width:100%; height:420px; border:none; border-radius:8px; background:#0d1117; }}
    .footer {{ text-align:center; color:#8b949e; font-size:0.85em; margin-top:24px; }}
</style>
</head>
<body>

<div class="header">
    <h1>🔍 PCB Linter Report</h1>
    <p>Prisha Bhatia — FOSSEE IIT Bombay</p>
    <p style="margin-top:8px;font-size:0.85em;">DA Clustering — Research Paper: "Clustering of Power Networks", IIT Bombay</p>
</div>

<div class="grid">
    <div class="card">
        <div class="value" style="color:{'#3fb950' if quality_score>=70 else '#d29922' if quality_score>=40 else '#f85149'}">{quality_score}</div>
        <div class="label">Quality Score / 100</div>
    </div>
    <div class="card"><div class="value">{len(findings)}</div><div class="label">Issues Found</div></div>
    <div class="card"><div class="value">{len(cluster_groups)}</div><div class="label">DA Clusters</div></div>
    <div class="card"><div class="value">{G.number_of_nodes()}</div><div class="label">Components</div></div>
    <div class="card"><div class="value">{len(net_to_components)}</div><div class="label">Nets</div></div>
</div>

<div class="section">
    <h2>⚡ Simulation Status</h2>
    <div class="sim-status">{simulation_status}</div>
    <p style="color:#8b949e;font-size:0.85em;">Clustering source: {clustering_source}</p>
</div>

<div class="section">
    <h2>🕸️ PCB Connectivity Graph</h2>
    <iframe src="pcb_graph.html"></iframe>
</div>

<div class="section">
    <h2>🔵 DA Clustering Results</h2>
    <p style="color:#8b949e;margin-bottom:16px;">Deterministic Annealing — attenuation matrix (αij) from real simulation voltages</p>
    <div class="cluster-grid">{cluster_cards}</div>
</div>

<div class="section">
    <h2>📊 Attenuation Matrix (αij)</h2>
    <div style="overflow-x:auto;">
        <table>
            <thead><tr><th>Node</th>{matrix_headers}</tr></thead>
            <tbody>{matrix_rows}</tbody>
        </table>
    </div>
</div>

<div class="section">
    <h2>⚠️ Issues & Recommendations</h2>
    <table>
        <thead><tr><th>Type</th><th>Component</th><th>Issue</th><th>Fix</th></tr></thead>
        <tbody>{findings_rows}</tbody>
    </table>
</div>

<div class="footer">
    <p>PCB Linter Plugin v3.0 — FOSSEE IIT Bombay</p>
    <p>DA Clustering — "Clustering of Power Networks", IIT Bombay</p>
</div>
</body>
</html>"""
    return html


report_path = os.path.join(REPORT_DIR, "pcb_linter_report.html")
with open(report_path, "w") as f:
    f.write(make_html_report())

print("=" * 50)
print("  PCB Linter Report Generated!")
print("=" * 50)
print(f"  Quality Score  : {quality_score}/100")
print(f"  Issues Found   : {len(findings)}")
print(f"  Clusters       : {len(cluster_groups)} (DA Clustering)")
print(f"  Nodes Clustered: {len(cluster_nodes)}")
print(f"  Voltages       : {[round(v[0],3) for v in voltage_data.values()]}")
print(f"  Simulation     : {simulation_status}")
print(f"  Report Saved   : {report_path}")
print("=" * 50)

try:
    webbrowser.open(f"file://{report_path}")
    print("[PCB Linter] Report opened in browser!")
except:
    print(f"[PCB Linter] Open manually: {report_path}")
