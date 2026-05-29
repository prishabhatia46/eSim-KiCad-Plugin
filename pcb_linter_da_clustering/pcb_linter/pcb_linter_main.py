"""
PCB Linter Plugin — Main Module v4.5
By Prisha Bhatia, FOSSEE IIT Bombay
DA Clustering based on "Clustering of Power Networks", IIT Bombay
"""

import math
import os
import re
import json
import glob
import subprocess
import tkinter as tk
from tkinter import filedialog, simpledialog
import pcbnew
import networkx as nx
from pyvis.network import Network

HOME         = os.path.expanduser("~")
REPORT_DIR   = os.path.join(HOME, "eSim-Workspace", "pcb_linter_output")
SPICE_FILE   = os.path.join(REPORT_DIR, "pcblinter_sim.cir")
PLOT_FILE    = os.path.join(REPORT_DIR, "plot_data_v.txt")
CURRENT_FILE = os.path.join(REPORT_DIR, "plot_data_i.txt")
LIB_DIR      = os.path.join(HOME, "lib")
ESIM_LIB_DIR = os.path.join(HOME, "Downloads", "eSim-2.5", "library", "SubcircuitLibrary")

CLUSTER_COLORS = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#bc8cff"]


def _ask_mode():
    root = tk.Tk()
    root.withdraw()
    mode = simpledialog.askstring(
        "PCB Linter Mode",
        "Choose mode:\n\n"
        "1 — Run on KiCad PCB (full simulation)\n"
        "2 — Import existing voltage/data file\n\n"
        "Enter 1 or 2:",
        parent=root
    )
    root.destroy()
    return "import" if mode == "2" else "kicad"


def _ask_two_files():
    root = tk.Tk()
    root.withdraw()
    v_path = filedialog.askopenfilename(
        title="Select Voltage Data File (required)",
        initialdir=os.path.expanduser("~"),
        filetypes=[("Text files","*.txt"),("CSV files","*.csv"),("All files","*.*")]
    )
    i_path = ""
    if v_path:
        want_current = simpledialog.askstring(
            "Current File",
            "Do you also want to import a current file?\n\nEnter Y for Yes, N for No:",
            parent=root
        )
        if want_current and want_current.strip().upper() == "Y":
            i_path = filedialog.askopenfilename(
                title="Select Current Data File (optional)",
                initialdir=os.path.dirname(v_path),
                filetypes=[("Text files","*.txt"),("CSV files","*.csv"),("All files","*.*")]
            )
    root.destroy()
    return v_path, i_path


def _detect_format(content):
    lines = [l.strip() for l in content.split('\n') if l.strip()]
    for line in lines[:20]:
        if 'Index' in line and 'time'      in line: return 'transient'
        if 'Index' in line and 'frequency' in line: return 'ac'
    for line in lines:
        if ',' in line:
            parts = line.split(',')
            if len(parts) == 2:
                try:    float(parts[1].strip()); return 'csv'
                except: pass
        if '=' in line and ('v(' in line.lower() or 'i(' in line.lower()):
            return 'dc'
        if '=' in line:
            parts = line.replace('=', ' ').split()
            if len(parts) == 2:
                try:    float(parts[1]); return 'dc'
                except: pass
    return 'dc'


def _parse_transient(content):
    data, node_names, last_values = {}, [], []
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if 'Index' in line and 'time' in line:
            node_names = line.split()[2:]
            i += 1
            if i < len(lines) and '---' in lines[i]: i += 1
            continue
        if line and node_names:
            parts = line.split()
            if len(parts) >= len(node_names) + 2:
                try:
                    int(parts[0]); float(parts[1])
                    last_values = parts[2:2+len(node_names)]
                except: pass
        i += 1
    if node_names and last_values:
        for name, val in zip(node_names, last_values):
            try:
                clean = name.lstrip('/').lower().replace('-','_').replace('(','').replace(')','')
                data[clean] = [float(val)]
            except: continue
    return data


def _parse_ac(content):
    data, node_names, last_values = {}, [], []
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if 'Index' in line and 'frequency' in line:
            node_names = line.split()[2:]
            i += 1
            if i < len(lines) and '---' in lines[i]: i += 1
            continue
        if line and node_names:
            parts = line.split()
            if len(parts) >= len(node_names) + 2:
                try:
                    int(parts[0]); float(parts[1])
                    last_values = parts[2:2+len(node_names)]
                except: pass
        i += 1
    if node_names and last_values:
        for name, val in zip(node_names, last_values):
            try:
                clean = name.lstrip('/').lower().replace('-','_').replace('(','').replace(')','')
                if 'j' in val.lower():
                    magnitude = abs(complex(val.replace('i','j')))
                else:
                    magnitude = abs(float(val))
                data[clean] = [magnitude]
            except: continue
    return data


def _parse_dc(content, allow_branch=False):
    data = {}
    for line in content.split('\n'):
        line = line.strip()
        if not line or line.startswith('*') or line.startswith('-') or line.startswith('Node'):
            continue
        parts = line.replace('=', ' ').split()
        if len(parts) == 2:
            try:
                node_name = parts[0].lower().strip()
                if node_name == '0': continue
                if '#branch' in node_name and not allow_branch: continue
                if '#branch' in node_name:
                    node_name = node_name.replace('#branch','').strip('_')
                if node_name.startswith('@rload') and not allow_branch: continue
                data[node_name] = [float(parts[1])]
            except ValueError: continue
    return data


def _parse_csv_data(content):
    data = {}
    for line in content.split('\n'):
        line = line.strip()
        if not line: continue
        parts = line.split(',')
        if len(parts) >= 2:
            try:
                data[parts[0].strip().lower()] = [float(parts[1].strip())]
            except ValueError: continue
    return data


def _read_universal(path, is_current=False):
    try:
        with open(path) as f:
            content = f.read()
        fmt = _detect_format(content)
        print(f"[PCB Linter] Detected format: {fmt} — file: {os.path.basename(path)}")
        if   fmt == 'transient': data = _parse_transient(content)
        elif fmt == 'ac':        data = _parse_ac(content)
        elif fmt == 'csv':       data = _parse_csv_data(content)
        else:                    data = _parse_dc(content, allow_branch=is_current)
        print(f"[PCB Linter] Parsed {len(data)} nodes: {list(data.keys())}")
        return data, fmt
    except Exception as e:
        print(f"[PCB Linter] Could not read file: {e}")
        return {}, 'unknown'


def run():
    os.makedirs(REPORT_DIR, exist_ok=True)
    os.chdir(REPORT_DIR)

    print("=" * 50)
    print("  PCB Linter Plugin v4.5 — Starting...")
    print("=" * 50)

    mode = _ask_mode()
    print(f"[PCB Linter] Mode: {mode}")

    voltage_data      = {}
    current_data      = {}
    i_filename        = "Not provided"
    G                 = nx.Graph()
    net_to_components = {}
    findings          = []
    quality_score     = 100
    simulation_status = ""
    clustering_source = ""
    detected_format   = "dc"
    user_config       = {}

    if mode == "kicad":
        board      = pcbnew.GetBoard()
        footprints = board.GetFootprints()
        print(f"[PCB Linter] Board: {board.GetFileName()}")
        print(f"[PCB Linter] Components found: {len(list(footprints))}")

        for f in footprints:
            ref   = f.GetReference()
            value = f.GetValue()
            G.add_node(ref, value=value)
            for pad in f.Pads():
                net_name = pad.GetNetname()
                if net_name:
                    if net_name not in net_to_components:
                        net_to_components[net_name] = []
                    if ref not in net_to_components[net_name]:
                        net_to_components[net_name].append(ref)

        for net_name, comps in net_to_components.items():
            for i in range(len(comps)):
                for j in range(i+1, len(comps)):
                    G.add_edge(comps[i], comps[j], net=net_name)

        print(f"[PCB Linter] Nets found: {len(net_to_components)}")

        # Feature 3: Floating Net Detection — SPICE generate hone se pehle
        findings = _detect_floating_nets(net_to_components, findings)

        # Feature 1: Power Rail Dialog — user se voltages confirm karo
        detected_rails = []
        for net in net_to_components.keys():
            _, is_pwr = _infer_net_voltage(net)
            if is_pwr:
                detected_rails.append(net)
        detected_rails = sorted(set(detected_rails))

        user_config = {}
        if detected_rails:
            user_config = _get_power_rail_config(board.GetFileName(), detected_rails)
        else:
            print("[PCB Linter] No power rails detected — using defaults")

        dc_cent = nx.degree_centrality(G)
        bc_cent = nx.betweenness_centrality(G)

        if len(G.nodes()) >= 5:
            for node, score in dc_cent.items():
                if score > 0.8:
                    findings.append({
                        "type": "WARNING", "component": node,
                        "message": f"High fanout — degree centrality: {score:.2f}",
                        "fix": "Check if this net has too many components connected"
                    })
            for node, score in bc_cent.items():
                if score > 0.7:
                    findings.append({
                        "type": "ERROR", "component": node,
                        "message": f"Single Point of Failure — betweenness: {score:.2f}",
                        "fix": "Add redundant connection"
                    })

        def _calc_score(findings, cluster_quality_label="N/A"):
            penalty = 0
            for f in findings:
                if f["type"] == "ERROR":
                    penalty += 15   # SPOF, critical
                elif f["type"] == "WARNING":
                    if "0V" in f.get("message","") or "power net" in f.get("message","").lower():
                        penalty += 3
                    else:
                        penalty += 5   # signal floating, high fanout
                elif f["type"] == "INFO":
                    penalty += 0    # connector unconnected — no penalty
            # Clustering quality adjustment
            cq_bonus = {"Excellent": 5, "Good": 0, "Fair": -5, "Poor": -10}.get(cluster_quality_label, 0)
            return max(0, min(100, 100 - penalty + cq_bonus))
        quality_score = _calc_score(findings)

        graph_path = os.path.join(REPORT_DIR, "pcb_graph.html")
        net_vis = Network(height="500px", width="100%", bgcolor="#0d1117", font_color="white")
        net_vis.toggle_physics(True)
        net_vis.set_options('{"layout":{"randomSeed":42},"physics":{"enabled":true,"solver":"forceAtlas2Based","forceAtlas2Based":{"gravitationalConstant":-80,"centralGravity":0.005,"springLength":150,"springConstant":0.05,"damping":0.4}},"interaction":{"dragNodes":true,"zoomView":true,"hover":true,"tooltipDelay":100}}')

        node_net_map = {}
        for net, comps in net_to_components.items():
            _, is_pwr = _infer_net_voltage(net, user_config)
            for comp in comps:
                if comp not in node_net_map:
                    node_net_map[comp] = {"is_power": is_pwr, "nets": []}
                node_net_map[comp]["nets"].append(net)

        for node in G.nodes():
            score   = dc_cent.get(node, 0)
            val_str = G.nodes[node].get("value", "")
            info    = node_net_map.get(node, {})
            is_pwr  = info.get("is_power", False)
            nets    = info.get("nets", [])
            if score > 0.5:
                shape = "star"
                color = {"background":"#f85149","border":"#ff8080"}
            elif is_pwr:
                shape = "diamond"
                color = {"background":"#d29922","border":"#f0c040"}
            else:
                shape = "dot"
                color = {"background":"#58a6ff","border":"#80c0ff"}
            size = 12 + int(score * 40)
            nets_str = ", ".join(nets[:4])
            if len(nets) > 4: nets_str += f" +{len(nets)-4} more"
            tooltip = f"{node} [{val_str}] | Degree: {score:.2f} | Nets: {nets_str}"
            net_vis.add_node(node, label=node, color=color, shape=shape, size=size, title=tooltip,
                           font={"size":11,"color":"#c9d1d9"})

        for edge in G.edges(data=True):
            net_name = edge[2].get("net","")
            _, is_pwr_edge = _infer_net_voltage(net_name, user_config)
            net_vis.add_edge(edge[0], edge[1],
                           color="#d29922" if is_pwr_edge else "#30363d",
                           width=2 if is_pwr_edge else 1,
                           title=f"Net: {net_name}")
        net_vis.save_graph(graph_path)
        with open(graph_path) as gf:
            g_html = gf.read()
        legend = (
            "<div style=\"position:fixed;top:12px;left:12px;background:#161b22cc;"
            "border:1px solid #30363d;border-radius:6px;padding:10px 14px;"
            "font-family:monospace;font-size:11px;color:#c9d1d9;z-index:999;\">"
            "<div style=\"font-weight:700;margin-bottom:6px;color:#e6edf3;\">Legend</div>"
            "<div><span style=\"color:#58a6ff;\">&#9679;</span> Signal Component</div>"
            "<div><span style=\"color:#d29922;\">&#9670;</span> Power Rail</div>"
            "<div><span style=\"color:#f85149;\">&#10022;</span> High Fanout</div>"
            "<div style=\"margin-top:6px;border-top:1px solid #30363d;padding-top:6px;\">"
            "<span style=\"color:#d29922;\">&#9472;</span> Power Net &nbsp;"
            "<span style=\"color:#484f58;\">&#9472;</span> Signal Net</div></div>"
        )
        g_html = g_html.replace("<body>", "<body>" + legend, 1)
        with open(graph_path, "w") as gf:
            gf.write(g_html)

        spice_content = _generate_spice(footprints, net_to_components, PLOT_FILE, CURRENT_FILE, user_config)
        with open(SPICE_FILE, "w") as f:
            f.write(spice_content)
        print(f"[PCB Linter] SPICE file generated")

        print("[PCB Linter] Running NGSpice simulation...")
        try:
            result = subprocess.run(
                ["ngspice", "-b", SPICE_FILE],
                capture_output=True, text=True, timeout=60, cwd=REPORT_DIR
            )
            simulation_status = "Simulation successful" if result.returncode == 0 else "Simulation warnings"
            print(f"[PCB Linter] NGSpice stdout: {result.stdout[:500]}")
            if result.stderr:
                print(f"[PCB Linter] NGSpice stderr: {result.stderr[:300]}")
        except Exception as e:
            simulation_status = f"Error: {str(e)}"
            print(f"[PCB Linter] ERROR: {e}")

        voltage_data, detected_format = _read_universal(PLOT_FILE)
        if voltage_data:
            clustering_source = "Real NGSpice/eSim Simulation Data"
        else:
            clustering_source = "Simulation failed"
            print("[PCB Linter] Simulation failed — no voltage data")

        current_data_raw, _ = _read_universal(CURRENT_FILE, is_current=True)
        map_path = os.path.join(REPORT_DIR, "rload_node_map.json")
        rload_map = {}
        if os.path.exists(map_path):
            with open(map_path) as mf:
                rload_map = json.load(mf)
        rload_map_lower = {k.lower(): v.lower() for k, v in rload_map.items()}
        current_data = {}
        for k, v in current_data_raw.items():
            if abs(v[0]) > 1e-12:
                net_name = rload_map_lower.get(k.lower(), k)
                current_data[net_name] = v

        for node, val in voltage_data.items():
            if node == 'gnd': continue
            if node.startswith('unconnected_'): continue
            v, is_pwr = _infer_net_voltage(node, user_config)
            if is_pwr and abs(val[0]) < 0.01:
                findings.append({
                    "type": "WARNING",
                    "component": node,
                    "message": f"Power net reads 0V — possible floating net or missing model",
                    "fix": "Check if this net has a proper voltage source or add .lib model"
                })

        print(f"[PCB Linter] Voltage nodes: {len(voltage_data)}, Current nodes: {len(current_data)}")

    elif mode == "import":
        v_path, i_path = _ask_two_files()
        if not v_path:
            print("[PCB Linter] No file selected!")
            return

        voltage_data, detected_format = _read_universal(v_path)
        if not voltage_data:
            print("[PCB Linter] Could not read voltage data!")
            return

        if i_path:
            current_data_raw, _ = _read_universal(i_path, is_current=True)
            current_data = {k: v for k, v in current_data_raw.items() if abs(v[0]) > 1e-12}
        else:
            current_data = {}

        v_filename        = os.path.basename(v_path)
        i_filename        = os.path.basename(i_path) if i_path else "Not provided"
        simulation_status = f"Voltage: {v_filename} | Current: {i_filename} | Format: {detected_format.upper()}"
        clustering_source = v_filename

    # unconnected nodes alag karo
    unconnected_nodes  = {k: v for k, v in voltage_data.items() if k.startswith('unconnected_')}
    voltage_data_clean = {k: v for k, v in voltage_data.items() if not k.startswith('unconnected_')}

    # Unmodeled components ke nodes clustering se bahar karo
    if mode == "kicad":
        unmodeled_refs = set()
        for node in G.nodes():
            r = node.upper()
            if r.startswith('U'):
                val = G.nodes[node].get("value","").upper()
                known = ["LM741","LM358","LM7805","LM7812","LM317","LM311",
                         "NE555","LM555","LM393","LM386","LM324","LM301",
                         "LM317","LM321","LM386","LM833","LM124","LM1458"]
                if not any(k in val for k in known):
                    unmodeled_refs.add(node)
            elif any(r.startswith(x) for x in ['P','J']):
                unmodeled_refs.add(node)
        # Unmodeled nodes ke voltage nets bhi filter karo
        unmodeled_nets = set()
        for ref in unmodeled_refs:
            for net, comps in net_to_components.items():
                if ref in comps and len(comps) == 1:
                    net_lower = net.lower().replace("/","_").replace("-","_")
                    unmodeled_nets.add(net_lower)
        voltage_data_clean = {k: v for k, v in voltage_data_clean.items()
                              if k not in unmodeled_nets}
        print(f"[PCB Linter] Unmodeled nodes excluded from clustering: {len(unmodeled_nets)}")

    # clustering
    if voltage_data_clean:
        non_gnd      = [n for n in voltage_data_clean if abs(voltage_data_clean[n][0]) > 0.01]
        gnd_nodes    = [n for n in voltage_data_clean if abs(voltage_data_clean[n][0]) <= 0.01]
        unique_v     = set(round(voltage_data_clean[n][0], 0) for n in non_gnd)
        num_clusters = min(5, max(2, len(unique_v) + (1 if gnd_nodes else 0)))
        print(f"[PCB Linter] Auto num_clusters = {num_clusters} (unique V: {unique_v})")

        if non_gnd:
            active_vd = {n: voltage_data_clean[n] for n in non_gnd}
            a_nodes, a_matrix = _build_alpha(active_vd)
            a_clusters = _da_clustering(a_nodes, a_matrix, max(1, num_clusters-1))
            v_groups = {0: gnd_nodes}
            for node, cid in a_clusters.items():
                v_groups.setdefault(cid+1, []).append(node)
        else:
            v_nodes, v_matrix = _build_alpha(voltage_data_clean)
            v_clusters = _da_clustering(v_nodes, v_matrix, num_clusters)
            v_groups   = _make_groups(v_clusters)
    else:
        v_groups = {}

    v_nodes, v_matrix = _build_alpha(voltage_data_clean) if voltage_data_clean else ([], {})
    print(f"[PCB Linter] Voltage clusters: {len(v_groups)}")

    i_groups = {}
    current_warning_type = "none"
    if current_data and len(current_data) >= 2:
        i_nodes, i_matrix = _build_alpha(current_data)
        i_clusters = _da_clustering(i_nodes, i_matrix, num_clusters if voltage_data_clean else 3)
        i_groups   = _make_groups(i_clusters)
        print(f"[PCB Linter] Current clusters: {len(i_groups)}")
    elif mode == "kicad" and len(current_data) <= 1:
        current_warning_type = "series"
    elif mode == "import" and not current_data:
        current_warning_type = "no_file"
    elif mode == "import" and len(current_data) < 2:
        current_warning_type = "too_few"

    p_groups   = {}
    power_data = {}
    if i_groups:
        power_data = _build_power_data(voltage_data_clean, current_data)
        if power_data and len(power_data) >= 2:
            p_nodes, p_matrix = _build_alpha(power_data)
            p_clusters = _da_clustering(p_nodes, p_matrix, num_clusters if voltage_data_clean else 3)
            p_groups   = _make_groups(p_clusters)
            print(f"[PCB Linter] Power clusters: {len(p_groups)}")

    # Feature 5: Cluster Quality Metric
    cluster_quality_score, cluster_quality_label = _compute_cluster_quality(v_groups, voltage_data_clean)
    # Recalculate score with clustering quality bonus
    if mode == "kicad":
        def _calc_score_final(findings, cql):
            penalty = 0
            for f in findings:
                if f["type"] == "ERROR":   penalty += 15
                elif f["type"] == "WARNING":
                    if "0V" in f.get("message","") or "power net" in f.get("message","").lower():
                        penalty += 3
                    else: penalty += 5
            cq_bonus = {"Excellent":5,"Good":0,"Fair":-5,"Poor":-10}.get(cql, 0)
            return max(0, min(100, 100 - penalty + cq_bonus))
        quality_score = _calc_score_final(findings, cluster_quality_label)

    report_path = os.path.join(REPORT_DIR, "pcb_linter_report.html")
    html = _make_report(
        quality_score, findings, G, net_to_components,
        simulation_status, clustering_source,
        v_nodes, v_matrix, voltage_data_clean,
        v_groups, i_groups, p_groups,
        current_data, power_data, mode,
        detected_format, current_warning_type, i_filename,
        unconnected_nodes, cluster_quality_score, cluster_quality_label
    )
    with open(report_path, "w") as f:
        f.write(html)

    print("=" * 50)
    print(f"  Voltage Clusters : {len(v_groups)}")
    print(f"  Current Clusters : {len(i_groups)}")
    print(f"  Power Clusters   : {len(p_groups)}")
    print(f"  Report Saved     : {report_path}")
    print("=" * 50)

    try:
        win_path = 'file:///\\\\wsl$\\Ubuntu' + report_path
        subprocess.run(['cmd.exe', '/c', 'start', win_path], capture_output=True, timeout=10)
    except Exception:
        try:
            subprocess.run(['xdg-open', report_path], timeout=10)
        except Exception:
            print(f'[PCB Linter] Open manually: {report_path}')


def _make_groups(cluster_result):
    groups = {}
    for node, cid in cluster_result.items():
        groups.setdefault(cid, []).append(node)
    return groups


def _get_power_rail_config(board_path, detected_rails):
    """
    Feature 1: Power Rail Dialog + JSON save
    Pehli baar user se voltages confirm karta hai, phir JSON mein save karta hai.
    Agli baar automatically load hota hai — no repeated asking.
    """
    config_path = os.path.join(REPORT_DIR, "pcb_voltages.json")

    # Agar config file exist karti hai toh load karo
    if os.path.exists(config_path):
        with open(config_path) as f:
            saved = json.load(f)
        # Sirf naye rails add karo jo pehle nahi the
        new_rails = [r for r in detected_rails if r not in saved]
        if not new_rails:
            print(f"[PCB Linter] Power rail config loaded from {config_path}")
            return saved

    # Dialog banao
    root = tk.Tk()
    root.title("PCB Linter — Power Rail Voltages")
    root.resizable(False, False)

    tk.Label(root, text="Power Rails Detected in PCB", font=("Arial", 12, "bold"),
             pady=8).pack()
    tk.Label(root, text="Confirm or change voltage values.\nWill be saved automatically for this PCB.",
             font=("Arial", 9), fg="gray").pack()

    frame = tk.Frame(root, pady=10)
    frame.pack(padx=20)

    entries = {}
    existing = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            existing = json.load(f)

    # Default guesses
    DEFAULT_GUESSES = {
        'VCC': 5.0, 'VDD': 3.3, 'VPP': 13.0, 'VIN': 12.0,
        'VBAT': 3.7, 'VBUS': 5.0, 'VMOT': 12.0, 'VSS': -5.0,
        'VEE': -12.0, 'VNEG': -5.0
    }

    for i, rail in enumerate(detected_rails):
        tk.Label(frame, text=f"{rail}", width=12, anchor='w',
                 font=("Courier", 10)).grid(row=i, column=0, padx=5, pady=3)
        tk.Label(frame, text="=", font=("Arial", 10)).grid(row=i, column=1)

        # Value: saved > default guess > 5.0
        default_val = existing.get(rail,
                      DEFAULT_GUESSES.get(rail.upper().split('_')[0],
                      DEFAULT_GUESSES.get(rail.upper(), 5.0)))
        var = tk.StringVar(value=str(default_val))
        entry = tk.Entry(frame, textvariable=var, width=8, font=("Courier", 10))
        entry.grid(row=i, column=2, padx=5)
        tk.Label(frame, text="V", font=("Arial", 10)).grid(row=i, column=3)
        entries[rail] = var

    result = {}

    def on_ok():
        for rail, var in entries.items():
            try:
                result[rail] = float(var.get())
            except ValueError:
                result[rail] = 5.0
        root.destroy()

    def on_cancel():
        for rail, var in entries.items():
            try:
                result[rail] = float(var.get())
            except ValueError:
                result[rail] = 5.0
        root.destroy()

    btn_frame = tk.Frame(root, pady=10)
    btn_frame.pack()
    tk.Button(btn_frame, text="OK — Save & Continue", command=on_ok,
              bg="#2ea043", fg="white", font=("Arial", 10, "bold"),
              padx=10).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_frame, text="Use Defaults", command=on_cancel,
              font=("Arial", 9), padx=10).pack(side=tk.LEFT, padx=5)

    root.mainloop()

    # Merge with existing and save
    existing.update(result)
    with open(config_path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"[PCB Linter] Power rail config saved to {config_path}")
    return existing


def _detect_floating_nets(net_to_components, findings):
    """
    Feature 3: Floating Net Detection — grouped version
    Same-component floating nets ek row mein group kiye jaate hain.
    Connectors (P,J) aur ICs (U) ke unconnected pins skip — by design hain.
    """
    CONNECTOR_PREFIXES = ('P', 'J', 'U')
    GND_NETS = ('gnd', 'ground', '0', 'agnd', 'dgnd', 'pgnd', 'egnd')

    # Group: comp → list of floating nets
    connector_unconn = {}   # P/J/U components
    signal_floating  = {}   # actual signal nets — real warning

    for net, comps in net_to_components.items():
        if net.lower() in GND_NETS:
            continue
        if len(comps) == 1:
            comp = comps[0]
            if net.lower().startswith('unconnected-') or any(comp.upper().startswith(p) for p in CONNECTOR_PREFIXES):
                connector_unconn.setdefault(comp, []).append(net)
            else:
                signal_floating.setdefault(comp, []).append(net)

    # Connector/IC unconnected — ek grouped INFO row
    if connector_unconn:
        all_comps  = sorted(connector_unconn.keys())
        total_pins = sum(len(v) for v in connector_unconn.values())
        comp_list  = ", ".join(all_comps[:6])
        if len(all_comps) > 6: comp_list += f" +{len(all_comps)-6} more"
        findings.append({
            "type": "INFO",
            "component": comp_list,
            "message": f"{total_pins} unconnected pins on connectors/ICs — intentional (by design).",
            "fix": "No action needed. These are expected unconnected pins."
        })

    # Real signal floating nets — actual warnings, grouped per component
    for comp, nets in signal_floating.items():
        net_list = ", ".join(nets[:4])
        if len(nets) > 4: net_list += f" +{len(nets)-4} more"
        findings.append({
            "type": "WARNING",
            "component": comp,
            "message": f"{len(nets)} floating signal net(s): {net_list}",
            "fix": "Connect these nets or add pull-up/pull-down resistors."
        })

    return findings


def _get_ic_subcircuit(ref, value, pad_nets):
    """
    Feature 6: Subcircuit IC Support
    eSim SubcircuitLibrary se models inject karta hai.
    """
    val = value.upper().strip()
    esim = ESIM_LIB_DIR

    # Folder name → (subfolder, .sub filename, num_pins)
    OPAMP_MODELS = {
        'LM741':  ('lm_741',     'lm_741.sub',     5),
        'MC1741': ('MC1741_Sub', 'MC1741_Sub.sub',  5),
        'LM301':  ('LM301_SUB',  'LM301_SUB.sub',   5),
        'LM302':  ('LM302_SUB',  'LM302_SUB.sub',   5),
        'LM310':  ('LM310_SUB',  'LM310_SUB.sub',   5),
        'LM321':  ('LM321',      'LM321.sub',        5),
        'LM358':  ('LM358_Sub',  'LM358_Sub.sub',    5),
        'LM393':  ('LM393',      'LM393.sub',        5),
        'LM311':  ('LM311',      'LM311.sub',        5),
        'LM386':  ('LM386',      'LM386.sub',        5),
        'LM833':  ('LM833',      'LM833.sub',        5),
        'LM124':  ('LM124_SUBCIRCUIT', 'LM124_SUBCIRCUIT.sub', 5),
        'LM1458': ('lm1458',     'lm1458.sub',       5),
    }

    VREG_MODELS = {
        'LM7805': ('lm7805',      'lm7805.sub',      3, 5.0),
        'LM7812': ('LM7812',      'LM7812.sub',       3, 12.0),
        'LM7809': ('LM_7809',     'LM_7809.sub',      3, 9.0),
        'LM7905': ('LM7905_SUB',  'LM7905_SUB.sub',   3, -5.0),
        'LM7912': ('LM7912_SUB',  'LM7912_SUB.sub',   3, -12.0),
        'LM7915': ('LM7915_SUB',  'LM7915_SUB.sub',   3, -15.0),
        'LM317':  ('LM317_sub',   'LM317_sub.sub',    3, None),
        'LM340_5':  ('LM340_5V_SUB',  'LM340_5V_SUB.sub',  3, 5.0),
        'LM340_12': ('LM340_12V_SUB', 'LM340_12V_SUB.sub', 3, 12.0),
        'LM340_15': ('LM340_15V_SUB', 'LM340_15V_SUB.sub', 3, 15.0),
        '78L05':  ('LM78L_Sub',   'LM78L_Sub.sub',    3, 5.0),
        '78M05':  ('LM78M05_sub', 'LM78M05_sub.sub',  3, 5.0),
    }

    # NE555 / LM555
    if any(x in val for x in ['NE555', 'LM555', 'NA555', '555', 'LM555N']):
        sub_path = os.path.join(esim, 'lm555n', 'lm555n.sub')
        if os.path.exists(sub_path):
            pins = pad_nets[:8] if len(pad_nets) >= 8 else pad_nets + ['0']*(8-len(pad_nets))
            return (f"X{ref} {' '.join(pins)} lm555n",
                    f".include {sub_path}", True)
        return f"* INFO: {ref} (555) — model not found", "", False

    # Check op-amps
    for name, (folder, subfile, npins) in OPAMP_MODELS.items():
        if name in val:
            sub_path = os.path.join(esim, folder, subfile)
            pins = pad_nets[:npins] if len(pad_nets) >= npins else pad_nets + ['0']*(npins-len(pad_nets))
            if os.path.exists(sub_path):
                return (f"X{ref} {' '.join(pins)} {name}",
                        f".include {sub_path}", True)
            else:
                # Generic ideal op-amp fallback
                return (_generic_opamp_model(ref, pins),
                        f"* {ref} ({value}) — eSim model not found, using ideal", True)

    # Check voltage regulators
    for name, (folder, subfile, npins, vout) in VREG_MODELS.items():
        if name in val:
            sub_path = os.path.join(esim, folder, subfile)
            pins = pad_nets[:npins] if len(pad_nets) >= npins else pad_nets + ['0']*(npins-len(pad_nets))
            if os.path.exists(sub_path):
                return (f"X{ref} {' '.join(pins)} {name}",
                        f".include {sub_path}", True)
            elif vout is not None:
                out_pin = pins[2] if len(pins) > 2 else pins[0]
                return (f"Vreg_{ref} {out_pin} 0 DC {vout}",
                        f"* {ref} modeled as ideal {vout}V source", True)

    return None, None, False


def _generic_opamp_model(ref, pins):
    """Generic ideal op-amp subcircuit when library model not found"""
    out = pins[0] if pins else 'out'
    inp = pins[1] if len(pins) > 1 else 'inp'
    inn = pins[2] if len(pins) > 2 else 'inn'
    vcc = pins[3] if len(pins) > 3 else 'vcc'
    vee = pins[4] if len(pins) > 4 else '0'
    return (f"* Generic ideal op-amp for {ref}\n"
            f"Eopamp_{ref} {out} 0 {inp} {inn} 100k")


def _get_cluster_domain_name(centroid_v):
    """
    Feature 2: Automatic Cluster Naming
    Centroid voltage se meaningful domain name deta hai.
    """
    v = centroid_v
    if abs(v) < 0.1:
        return "GND Domain"
    elif -0.5 < v < 0.5:
        return "Near-GND Domain"
    elif 0.5 <= v < 2.0:
        return "Low-V Domain (~1V)"
    elif 2.0 <= v < 4.0:
        return "Signal Domain (~3.3V)"
    elif 4.0 <= v < 6.0:
        return "5V Domain"
    elif 6.0 <= v < 10.0:
        return "Mid-V Domain (~9V)"
    elif 10.0 <= v < 14.0:
        return "12V Domain"
    elif 14.0 <= v < 20.0:
        return "15V Domain"
    elif v >= 20.0:
        return f"High-V Domain (~{round(v)}V)"
    elif v < -0.5:
        return f"Neg Supply ({round(v,1)}V)"
    return f"Domain (~{round(v,1)}V)"


def _compute_cluster_quality(v_groups, voltage_data):
    """
    Feature 5: Cluster Quality Metric
    Intra-cluster vs inter-cluster voltage ratio compute karta hai.
    Good clustering = intra spread kam, inter spread zyada.
    Returns: score (0-100), label (Excellent/Good/Fair/Poor)
    """
    if not v_groups or len(v_groups) < 2:
        return None, "N/A"

    group_voltages = {}
    for cid, nodes in v_groups.items():
        vals = [voltage_data[n][0] for n in nodes if n in voltage_data]
        if vals:
            group_voltages[cid] = vals

    if len(group_voltages) < 2:
        return None, "N/A"

    # Intra-cluster spread (lower is better)
    intra_spreads = []
    for cid, vals in group_voltages.items():
        if len(vals) > 1:
            spread = max(vals) - min(vals)
            intra_spreads.append(spread)
    avg_intra = sum(intra_spreads) / len(intra_spreads) if intra_spreads else 0

    # Inter-cluster separation (higher is better)
    centroids = [sum(v)/len(v) for v in group_voltages.values()]
    inter_diffs = []
    for i in range(len(centroids)):
        for j in range(i+1, len(centroids)):
            inter_diffs.append(abs(centroids[i] - centroids[j]))
    avg_inter = sum(inter_diffs) / len(inter_diffs) if inter_diffs else 1

    # Quality ratio
    ratio = avg_inter / (avg_intra + 0.01)
    if ratio > 10:   score, label = 95, "Excellent"
    elif ratio > 5:  score, label = 80, "Good"
    elif ratio > 2:  score, label = 60, "Fair"
    else:            score, label = 35, "Poor"

    return score, label


def _infer_net_voltage(net_name, user_config=None):
    """
    Infer voltage from net name. If user_config provided, use that first.
    Feature 4: Negative supply support added.
    """
    # Feature 1: User config takes priority
    if user_config:
        n_upper = net_name.upper().strip()
        for rail, volt in user_config.items():
            if rail.upper() in n_upper or n_upper in rail.upper():
                return (float(volt), True)

    n = net_name.upper().strip()
    negative = n.startswith('-')
    n_clean  = n.lstrip('+-').strip()

    if n in ('GND','GROUND','AGND','DGND','PGND','EGND','VSS','VSSA','VSSD','0','COM','COMMON','GND_PWR','POWER_GND','EARTH'):
        return (0.0, False)

    # Feature 4: Negative supply support
    if negative:
        if any(x in n_clean for x in ('12V','V12')): return (-12.0, True)
        if any(x in n_clean for x in ('5V','V5')):   return (-5.0,  True)
        if any(x in n_clean for x in ('3V3','3.3V')): return (-3.3, True)
        if any(x in n_clean for x in ('15V','V15')): return (-15.0, True)
        return (-5.0, True)

    # Negative supply names
    if any(x in n for x in ('VEE','VNEG','VM12','VMINUS','NEG_RAIL','V_NEG','-12V','-5V')):
        if any(x in n for x in ('12','VM12')): return (-12.0, True)
        return (-5.0, True)
    if 'VSS' in n and 'VSSA' not in n and 'VSSD' not in n:
        return (-5.0, True)

    if any(x in n for x in ('1V8','V1V8','1.8V','VDD18','VDDIO','VIO','VLOGIC','DVDD')): return (1.8, True)
    if any(x in n for x in ('2V5','V2V5','2.5V','VDD25','VREF25')): return (2.5, True)
    if any(x in n for x in ('3V3','V3V3','3.3V','VDD33','VCC33','AVDD','AVCC','DVDD33','DVCC33',
                             '+3.3V','PWR3V3','IOVDD','P33V','P3V3','VDD_MCU','VDDA','VMCU')): return (3.3, True)
    if any(x in n for x in ('VBAT','VBATT','VBATTERY','VCELL','LIPO')): return (3.7, True)
    if any(x in n for x in ('VCC','VDD','V5','VCC5','5V','+5V','VSUP','VBUS','VUSB','PWR','POWER',
                             'VREG','VIN5','VCC_5V','VDD5','AVCC5','DVCC5','VCC_MAIN','+5','V_5V','VSYS')): return (5.0, True)
    if any(x in n for x in ('9V','V9','VCC9','VIN9')): return (9.0, True)
    if any(x in n for x in ('VIN','V12','VCC12','12V','+12V','VMOT','VMOTOR','VM','VIN12','VCC_12V','PWR12','V_12V')): return (12.0, True)
    if any(x in n for x in ('VPP','VHIGH','VPROG','VBOOST','VPP_','_VPP','VMCLR')): return (13.0, True)
    if any(x in n for x in ('15V','V15','VCC15','+15V')): return (15.0, True)
    if any(x in n for x in ('24V','V24','VCC24','+24V')): return (24.0, True)
    if any(x in n for x in ('48V','V48','VCC48')): return (48.0, True)

    return (0.0, False)


def _clean_spice_value(value, component_type="R"):
    val = str(value).strip()
    val = re.sub(r'/\d+[Vv].*', '', val)
    val = re.sub(r'([0-9])([munpkKMG])[FfHhΩ].*', r'\1\2', val)
    val = val.replace('µ','u').replace('μ','u')
    val = re.sub(r'(\d),(\d)', r'\1.\2', val)
    val = re.sub(r'(\d)K$', r'\1k', val)
    val = re.sub(r'(\d)M$', r'\1meg', val)
    val = val.strip()
    if not val or not re.search(r'\d', val):
        return {"R":"1k","C":"1n","L":"1u","V":"5"}.get(component_type, "1k")
    return val


def _generate_spice(footprints, net_to_components, plot_file, current_file, user_config=None):
    lines = ["* PCB Linter — Auto SPICE v4.5", "* Prisha Bhatia, FOSSEE IIT Bombay", ""]

    lib_files = glob.glob(os.path.join(LIB_DIR, "*.lib"))
    if lib_files:
        for lf in lib_files:
            lines.append(f".include {lf}")
        lines.append("")

    net_map     = {}
    net_counter = 1
    for net_name in net_to_components.keys():
        if net_name.lower() in ['gnd','ground','/gnd']:
            net_map[net_name] = '0'
        else:
            tmp   = net_name.replace("+","P").replace("-","N")
            clean = ''.join(c for c in tmp.replace("/","_") if c.isalnum() or c == '_')
            if not clean or clean[0].isdigit():
                clean = f"net_{net_counter}"
            net_map[net_name] = clean
            net_counter += 1

    NON_ELECTRICAL = ['LOGO', 'MOUNT', 'FID', 'TP', 'SH']

    has_v            = False
    all_nodes        = set()
    comp_lines       = []
    need_bjt_model   = False
    need_pnp_model   = False
    need_diode_model = False
    need_nmos_model  = False
    need_pmos_model  = False
    need_jfet_model  = False

    for f in footprints:
        ref      = f.GetReference()
        value    = f.GetValue() or "1k"
        pad_nets = [net_map.get(p.GetNetname(), '0') for p in f.Pads()]
        while len(pad_nets) < 2: pad_nets.append('0')
        n1, n2 = pad_nets[0], pad_nets[1]
        r = ref.upper()

        if any(r.startswith(x) for x in NON_ELECTRICAL):
            comp_lines.append(f"* SKIPPED: {ref} — non-electrical")
            continue

        if r.startswith('R'):
            comp_lines.append(f"{ref} {n1} {n2} {_clean_spice_value(value,'R')}")
            for n in [n1,n2]:
                if n != '0': all_nodes.add(n)
        elif r.startswith('C'):
            comp_lines.append(f"{ref} {n1} {n2} {_clean_spice_value(value,'C')}")
            for n in [n1,n2]:
                if n != '0': all_nodes.add(n)
        elif r.startswith('L'):
            comp_lines.append(f"{ref} {n1} {n2} {_clean_spice_value(value,'L')}")
            for n in [n1,n2]:
                if n != '0': all_nodes.add(n)
        elif r.startswith('V'):
            v_val = re.search(r'[\d.]+', _clean_spice_value(value,'V'))
            comp_lines.append(f"{ref} {n1} 0 DC {v_val.group() if v_val else '5'}")
            has_v = True
            if n1 != '0': all_nodes.add(n1)
        elif r.startswith('I'):
            comp_lines.append(f"{ref} {n1} {n2} DC 1m")
            for n in [n1,n2]:
                if n != '0': all_nodes.add(n)
        elif r.startswith('Q'):
            if len(pad_nets) >= 3:
                nc, nb, ne = pad_nets[0], pad_nets[1], pad_nets[2]
                if any(x in value.upper() for x in ['PNP','BC557','BC558','2N3906','BC327']):
                    comp_lines.append(f"{ref} {nc} {nb} {ne} Qpnp"); need_pnp_model = True
                else:
                    comp_lines.append(f"{ref} {nc} {nb} {ne} Qnpn"); need_bjt_model = True
                for n in [nc,nb,ne]:
                    if n != '0': all_nodes.add(n)
            else:
                comp_lines.append(f"* WARNING: {ref} skipped — BJT needs 3 pads")
        elif r.startswith('M'):
            if len(pad_nets) >= 3:
                nd, ng, ns = pad_nets[0], pad_nets[1], pad_nets[2]
                if 'PMOS' in value.upper() or value.upper().startswith('P'):
                    comp_lines.append(f"{ref} {nd} {ng} {ns} {ns} Mpmos W=10u L=1u"); need_pmos_model = True
                else:
                    comp_lines.append(f"{ref} {nd} {ng} {ns} {ns} Mnmos W=10u L=1u"); need_nmos_model = True
                for n in [nd,ng,ns]:
                    if n != '0': all_nodes.add(n)
            else:
                comp_lines.append(f"* WARNING: {ref} skipped — MOSFET needs 3 pads")
        elif r.startswith('D'):
            comp_lines.append(f"{ref} {n1} {n2} Dmodel"); need_diode_model = True
            for n in [n1,n2]:
                if n != '0': all_nodes.add(n)
        elif r.startswith('J'):
            if len(pad_nets) >= 3:
                nd, ng, ns = pad_nets[0], pad_nets[1], pad_nets[2]
                comp_lines.append(f"{ref} {nd} {ng} {ns} Jmodel"); need_jfet_model = True
                for n in [nd,ng,ns]:
                    if n != '0': all_nodes.add(n)
            else:
                comp_lines.append(f"* WARNING: {ref} skipped — JFET needs 3 pads")
        elif r.startswith('U'):
            # Feature 6: Subcircuit IC Support
            spice_line, model_hint, found = _get_ic_subcircuit(ref, value, pad_nets)
            if found:
                comp_lines.append(spice_line)
                if model_hint:
                    if model_hint.startswith('.include'):
                        lines.insert(3, model_hint)  # add include at top
                    else:
                        comp_lines.append(model_hint)
            else:
                comp_lines.append(f"* INFO: {ref} ({value}) — IC not in library, pads added as nodes")
            for n in pad_nets:
                if n != '0': all_nodes.add(n)
        else:
            for n in pad_nets:
                if n != '0': all_nodes.add(n)
            comp_lines.append(f"* INFO: {ref} — unknown type")

    lines += comp_lines

    vsrc_idx       = 1
    rload_idx      = 1
    found_any_vsrc = has_v
    rload_node_map = {}

    for node in sorted(all_nodes):
        voltage, is_power = _infer_net_voltage(node, user_config)
        if is_power:
            lines.append(f"Vsrc_{vsrc_idx} {node} 0 DC {voltage}")
            print(f"[PCB Linter] Power source: Vsrc_{vsrc_idx} → {node} = {voltage}V")
            vsrc_idx += 1
            found_any_vsrc = True
        else:
            lines.append(f"Rload_{rload_idx} {node} 0 100k")
            rload_node_map[f"@rload_{rload_idx}[i]"] = node
            rload_idx += 1

    if not found_any_vsrc and all_nodes:
        first = sorted(all_nodes)[0]
        lines.append(f"Vdefault {first} 0 DC 5")

    if need_bjt_model:   lines.append(".model Qnpn NPN(BF=100 IS=1e-14 VAF=100)")
    if need_pnp_model:   lines.append(".model Qpnp PNP(BF=100 IS=1e-14 VAF=100)")
    if need_diode_model: lines.append(".model Dmodel D(IS=1e-14 N=1)")
    if need_nmos_model:  lines.append(".model Mnmos NMOS(KP=100u VTO=1)")
    if need_pmos_model:  lines.append(".model Mpmos PMOS(KP=100u VTO=-1)")
    if need_jfet_model:  lines.append(".model Jmodel NJF(Beta=1m Vto=-2)")

    rload_print_lines = [f"print @rload_{ri}[i] >> {current_file}" for ri in range(1, rload_idx)]
    lines += [
        "", ".op", "", ".control", "run",
        f"echo 'GND = 0.0' > {plot_file}",
        f"print allv >> {plot_file}",
        f"echo '' > {current_file}",
    ] + rload_print_lines + [".endc", ".end"]

    map_path = os.path.join(REPORT_DIR, "rload_node_map.json")
    with open(map_path, "w") as mf:
        json.dump(rload_node_map, mf)

    return "\n".join(lines)


def _build_alpha(vd):
    nodes = list(vd.keys())
    alpha = {}
    MAX_RATIO = 10.0
    all_v = [abs(vd[n][0]) for n in nodes if vd[n]]
    v_max = max(all_v) if all_v else 1.0
    for ni in nodes:
        alpha[ni] = {}
        vi = vd[ni][0] if vd[ni] else 1.0
        for nj in nodes:
            vj = vd[nj][0] if vd[nj] else 1.0
            if ni == nj:
                alpha[ni][nj] = 1.0
            elif vj == 0 and vi == 0:
                alpha[ni][nj] = 1.0
            elif vj == 0:
                alpha[ni][nj] = MAX_RATIO
            else:
                ratio        = min(abs(vi/vj), MAX_RATIO)
                diff_penalty = abs(vi-vj) / (v_max if v_max > 0 else 1.0)
                alpha[ni][nj] = ratio * (1.0 - 0.5 * diff_penalty)
    return nodes, alpha


def _da_clustering(nodes, alpha, num_clusters=3, T_start=2.0, T_end=0.0001, cooling=0.92):
    n = len(nodes)
    if n == 0: return {}
    k = min(num_clusters, n)
    sorted_nodes = sorted(range(n), key=lambda x: alpha[nodes[x]][nodes[x]])
    node_rank    = {nodes[i]: r for r, i in enumerate(sorted_nodes)}
    probs = []
    for i in range(n):
        p    = [0.01] * k
        cidx = min(int(node_rank[nodes[i]] * k / n), k-1)
        p[cidx] = 0.97
        total = sum(p)
        probs.append([x/total for x in p])

    T = T_start; iters = 0
    while T > T_end:
        centers = []
        for c in range(k):
            tw = sum(probs[i][c] for i in range(n))
            centers.append({
                nj: sum(probs[i][c] * alpha[nodes[i]].get(nj,0) for i in range(n)) / (tw or 1)
                for nj in nodes
            })
        for i, ni in enumerate(nodes):
            dists = [sum((alpha[ni].get(nj,0)-centers[c].get(nj,0))**2 for nj in nodes) for c in range(k)]
            min_d = min(dists)
            exps  = []
            for d in dists:
                try:    exps.append(math.exp(-(d-min_d)/T))
                except: exps.append(float('inf'))
            te       = sum(exps)
            probs[i] = [e/te for e in exps] if te > 0 else [1.0/k]*k
        T *= cooling; iters += 1

    print(f"[PCB Linter] DA done in {iters} iterations")
    return {nodes[i]: probs[i].index(max(probs[i])) for i in range(n)}


def _build_power_data(voltage_data, current_data):
    v_lower = {k.lower(): v for k, v in voltage_data.items()}
    i_lower = {k.lower(): v for k, v in current_data.items()}
    common  = set(v_lower.keys()) & set(i_lower.keys())
    if not common: return {}
    power = {node: [abs(v_lower[node][0]) * abs(i_lower[node][0])] for node in common}
    print(f"[PCB Linter] Power data: {len(power)} nodes")
    return power


def _make_cluster_cards(groups, voltage_data, current_data):
    if not groups:
        return '<p style="color:var(--muted);font-size:0.85em;">Not available.</p>'
    cards = ""
    for cid, nodes in groups.items():
        color = CLUSTER_COLORS[cid % len(CLUSTER_COLORS)]

        # Feature 2: Automatic Cluster Naming
        node_voltages = [voltage_data[n][0] for n in nodes if voltage_data.get(n)]
        centroid = sum(node_voltages) / len(node_voltages) if node_voltages else 0
        domain_name = _get_cluster_domain_name(centroid)

        nodes_html = ""
        for n in nodes:
            v_str = f"{voltage_data.get(n,[0])[0]:.2f}V" if voltage_data.get(n) else ""
            i_str = f"{current_data.get(n,[0])[0]*1000:.3f}mA" if current_data.get(n) else ""
            extra = (f" {v_str}" if v_str else "") + (f" {i_str}" if i_str else "")
            nodes_html += (
                f'<span class="node-tag">{n}'
                f'<span class="node-v">{extra}</span></span>'
            )
        cards += (
            f'<div class="cluster-card" style="border-top:3px solid {color};">'
            f'<div class="cluster-title" style="color:{color};">Cluster {cid+1} — {domain_name}</div>'
            f'<div class="cluster-count">{len(nodes)} node(s)</div>'
            f'<div class="node-list">{nodes_html}</div></div>'
        )
    return cards


def _get_current_warning_html(warning_type, mode):
    msgs = {
        "series":  ('d29922','&#9888; Series circuit — single total current only.'),
        "no_file": ('8b949e','&#8505; No current file imported.'),
        "too_few": ('d29922','&#9888; Current values near-zero — series or open circuit.'),
    }
    if warning_type in msgs:
        color, msg = msgs[warning_type]
        return f'<p style="color:#{color};font-size:0.8em;margin-bottom:12px;">{msg}</p>'
    return ""


def _make_net_component_table(net_to_components):
    if not net_to_components: return ""
    rows = ""
    for net, comps in sorted(net_to_components.items()):
        comps_html = " ".join(f'<code>{c}</code>' for c in comps)
        rows += f"<tr><td>{net}</td><td>{len(comps)}</td><td>{comps_html}</td></tr>"
    return f"""
  <div class="section">
    <div class="section-header"><div class="section-dot" style="background:#bc8cff;"></div><div class="section-title">Net → Component Map</div></div>
    <div class="section-body" style="padding:0;"><div class="matrix-wrap">
      <table><thead><tr><th>Net Name</th><th>Components</th><th>Connected To</th></tr></thead><tbody>{rows}</tbody></table>
    </div></div>
  </div>"""


def _make_unconnected_section(unconnected_nodes):
    if not unconnected_nodes: return ""
    pins_html = "".join(
        f'<span class="node-tag">{n}<span class="node-v"> 0V (floating)</span></span>'
        for n in sorted(unconnected_nodes.keys())
    )
    return f"""
  <div class="section">
    <div class="section-header"><div class="section-dot" style="background:#f85149;"></div><div class="section-title">Unconnected Pins ({len(unconnected_nodes)})</div></div>
    <div class="section-body">
      <p style="color:var(--muted);font-size:0.8em;margin-bottom:12px;">These pads have no net connection. Excluded from clustering.</p>
      <div class="node-list">{pins_html}</div>
    </div>
  </div>"""


def _make_heatmap(voltage_data, current_data, power_data):
    if not voltage_data:
        return '<p style="color:var(--muted);">No voltage data available.</p>'

    def get_domain(v):
        av = abs(v)
        if av < 0.01:   return ("GND / Signal",     "#484f58", "#8b949e", "⚪")
        elif av < 2.5:  return ("Low Voltage",       "#1a2a3a", "#58a6ff", "🔵")
        elif av < 4.0:  return ("Logic (3.3V)",      "#1a3a2a", "#3fb950", "🟢")
        elif av < 6.0:  return ("5V Supply",         "#2d2416", "#d29922", "🟡")
        elif av < 14.0: return ("High Voltage",      "#3d1a1a", "#f85149", "🔴")
        else:           return ("Very High Voltage",  "#3d1020", "#bc8cff", "🟣")

    sorted_nets = sorted(voltage_data.items(), key=lambda x: abs(x[1][0]), reverse=True)
    max_v = max(abs(x[1][0]) for x in sorted_nets) or 1
    rows = ""
    for net, val in sorted_nets:
        v = val[0]
        domain_label, bg, fg, icon = get_domain(v)
        bar_w = min(int(abs(v) / max_v * 100), 100)
        warning = ""
        inferred_v, is_pwr = _infer_net_voltage(net)
        if is_pwr and abs(v) < 0.01:
            warning = '<span style="color:#f85149;font-size:0.75em;margin-left:6px;">⚠ Expected non-zero</span>'
        rows += f"""<tr>
          <td style="color:var(--text);font-weight:500;">{net}{warning}</td>
          <td style="text-align:right;color:{fg};font-weight:600;">{v:.3f}V</td>
          <td style="padding:8px 12px;">
            <div style="background:var(--surface3);border-radius:3px;height:8px;overflow:hidden;">
              <div style="width:{bar_w}%;height:100%;background:{fg};border-radius:3px;"></div>
            </div>
          </td>
          <td><span style="display:inline-block;padding:2px 8px;border-radius:3px;font-size:0.75em;font-weight:600;background:{bg};color:{fg};border:1px solid {fg}44;">{icon} {domain_label}</span></td>
        </tr>"""

    return f"""<div style="overflow-x:auto;">
      <table><thead><tr><th>Net</th><th style="text-align:right;">Voltage</th><th>Level</th><th>Domain</th></tr></thead>
      <tbody>{rows}</tbody></table>
      <div style="margin-top:14px;display:flex;gap:16px;flex-wrap:wrap;font-size:0.78em;">
        <span style="color:#8b949e;">⚪ GND/Signal</span>
        <span style="color:#58a6ff;">🔵 Low Voltage</span>
        <span style="color:#3fb950;">🟢 Logic 3.3V</span>
        <span style="color:#d29922;">🟡 5V Supply</span>
        <span style="color:#f85149;">🔴 High Voltage</span>
        <span style="color:#bc8cff;">🟣 Very High</span>
      </div></div>"""



def _make_cluster_bar_chart(v_groups, voltage_data):
    """Bar chart showing voltage range per cluster"""
    if not v_groups or not voltage_data:
        return '<p style="color:var(--muted);">No cluster data available.</p>'

    CLUSTER_COLORS = ["#58a6ff","#3fb950","#d29922","#f85149","#bc8cff"]
    bars = ""
    max_v = max((abs(voltage_data[n][0]) for nodes in v_groups.values() for n in nodes if voltage_data.get(n)), default=1)
    if max_v == 0: max_v = 1

    for cid, nodes in sorted(v_groups.items()):
        vals = [voltage_data[n][0] for n in nodes if voltage_data.get(n)]
        if not vals: continue
        color = CLUSTER_COLORS[cid % len(CLUSTER_COLORS)]
        centroid = sum(vals)/len(vals)
        domain = _get_cluster_domain_name(centroid)
        min_v = min(vals)
        max_cv = max(vals)
        bar_pct = min(100, int(abs(centroid)/max_v*100))
        range_str = f"{min_v:.2f}V – {max_cv:.2f}V" if min_v != max_cv else f"{centroid:.2f}V"
        bars += f"""
        <div style="margin-bottom:18px;">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
            <span style="color:{color};font-weight:600;font-size:0.85em;">Cluster {cid+1} — {domain}</span>
            <span style="color:var(--muted);font-size:0.82em;">{len(nodes)} nodes | {range_str}</span>
          </div>
          <div style="background:var(--surface2);border-radius:6px;height:22px;overflow:hidden;">
            <div style="width:{bar_pct}%;height:100%;background:{color};border-radius:6px;
                        display:flex;align-items:center;padding-left:8px;
                        font-size:0.75em;color:#000;font-weight:600;min-width:40px;">
              {centroid:.2f}V
            </div>
          </div>
        </div>"""

    return f'<div style="padding:8px 0;">{bars}</div>'


def _make_cluster_bar_chart(v_groups, voltage_data):
    if not v_groups or not voltage_data:
        return '<p style="color:var(--muted);">No cluster data.</p>'
    COLORS = ["#58a6ff","#3fb950","#d29922","#f85149","#bc8cff"]
    all_vals = [abs(voltage_data[n][0]) for nodes in v_groups.values() for n in nodes if voltage_data.get(n)]
    max_v = max(all_vals) if all_vals else 1
    if max_v == 0: max_v = 1
    bars = ""
    for cid, nodes in sorted(v_groups.items()):
        vals = [voltage_data[n][0] for n in nodes if voltage_data.get(n)]
        if not vals: continue
        color = COLORS[cid % len(COLORS)]
        centroid = sum(vals)/len(vals)
        domain = _get_cluster_domain_name(centroid)
        min_v = min(vals)
        max_cv = max(vals)
        bar_pct = min(100, int(abs(centroid)/max_v*100))
        range_str = (str(round(min_v,2))+"V - "+str(round(max_cv,2))+"V") if min_v != max_cv else str(round(centroid,2))+"V"
        bars += (
            '<div style="margin-bottom:18px;">'
            '<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
            '<span style="color:'+color+';font-weight:600;font-size:0.85em;">Cluster '+str(cid+1)+' - '+domain+'</span>'
            '<span style="color:var(--muted);font-size:0.82em;">'+str(len(nodes))+' nodes | '+range_str+'</span>'
            '</div>'
            '<div style="background:var(--surface2);border-radius:6px;height:22px;overflow:hidden;">'
            '<div style="width:'+str(bar_pct)+'%;height:100%;background:'+color+';border-radius:6px;'
            'display:flex;align-items:center;padding-left:8px;font-size:0.75em;color:#000;font-weight:600;min-width:40px;">'
            +str(round(centroid,2))+'V'
            '</div></div></div>'
        )
    return '<div style="padding:8px 0;">'+bars+'</div>'

def _make_report(quality_score, findings, G, net_to_components,
                 simulation_status, clustering_source,
                 cluster_nodes, alpha_matrix, voltage_data,
                 v_groups, i_groups, p_groups,
                 current_data, power_data, mode,
                 detected_format="dc", current_warning_type="none",
                 i_filename="Not provided", unconnected_nodes=None,
                 cluster_quality_score=None, cluster_quality_label="N/A"):

    if unconnected_nodes is None:
        unconnected_nodes = {}

    clean_status = simulation_status.replace("✅","").replace("⚠","").replace("❌","").strip()
    clean_source = clustering_source.replace("✅","").replace("⚠","").strip()

    findings_rows = ""
    for f in findings:
        if f["type"] == "ERROR":
            badge_class = "badge-error"
        elif f["type"] == "INFO":
            badge_class = "badge-info"
        else:
            badge_class = "badge-warn"
        findings_rows += (
            f'<tr><td><span class="badge {badge_class}">{f["type"]}</span></td>'
            f'<td><code>{f["component"]}</code></td>'
            f'<td>{f["message"]}</td><td>{f["fix"]}</td></tr>'
        )
    if not findings_rows:
        if mode == "kicad" and len(G.nodes()) < 5:
            findings_rows = f'<tr><td colspan="4" class="no-issues" style="color:var(--muted);">Circuit has only {len(G.nodes())} component(s).</td></tr>'
        else:
            findings_rows = '<tr><td colspan="4" class="no-issues">No issues found.</td></tr>'

    # Simulation Coverage
    sim_coverage_html = ""
    if mode == "kicad" and net_to_components:
        simulated, partial, not_simulated = [], [], []
        for node in G.nodes():
            r = node.upper()
            if any(r.startswith(x) for x in ["R","C","L","D","Q","M","J"]):
                simulated.append(node)
            elif r.startswith("V") or r.startswith("I"):
                simulated.append(node)
            elif r.startswith("U"):
                val = G.nodes[node].get("value","").upper()
                known = ["LM741","LM358","LM7805","LM7812","LM317","LM311","NE555","LM555","LM393","LM386","LM324","LM301"]
                if any(k in val for k in known):
                    simulated.append(node)
                else:
                    not_simulated.append(node)
            else:
                not_simulated.append(node)

        def _chip_row(nodes, color, icon, label):
            if not nodes: return ""
            uid = label.replace(" ","_").replace("/","_").replace("(","").replace(")","").lower()
            chips_all = " ".join(f'<code>{n}</code>' for n in nodes)
            chips_preview = " ".join(f'<code>{n}</code>' for n in nodes[:12])
            more_count = len(nodes) - 12
            if more_count > 0:
                toggle = (
                    f'<span id="more_{uid}" style="display:none;">{chips_all}</span>' +
                    f'<span id="preview_{uid}">{chips_preview} ' +
                    f'<span onclick="toggleMore(\'{uid}\')" ' +
                    f'style="color:#58a6ff;cursor:pointer;text-decoration:underline;font-size:0.85em;">+{more_count} more</span></span>'
                )
                chips_html = toggle
            else:
                chips_html = chips_preview
            return (f'<div style="margin-bottom:10px;">' +
                    f'<span style="color:{color};font-weight:600;font-size:0.82em;">{icon} {label} ({len(nodes)})</span>' +
                    f'<div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px;">{chips_html}</div></div>')

        sim_coverage_html = (
            _chip_row(simulated,    "#3fb950", "✅", "Fully Simulated") +
            _chip_row(partial,      "#d29922", "⚠️", "Partially Simulated (ideal model)") +
            _chip_row(not_simulated,"#f85149", "❌", "Unmodeled — Excluded from Clustering")
        )

        # Net Count Summary
        power_nets  = [n for n in net_to_components if _infer_net_voltage(n)[1]]
        signal_nets = [n for n in net_to_components if not _infer_net_voltage(n)[1]
                       and not n.lower().startswith("unconnected-")]
        uncomn_nets = [n for n in net_to_components if n.lower().startswith("unconnected-")]
        net_summary = (
            f'<div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:16px;">' +
            f'<div class="stat">Power Nets&nbsp;<span>{len(power_nets)}</span></div>' +
            f'<div class="stat">Signal Nets&nbsp;<span>{len(signal_nets)}</span></div>' +
            f'<div class="stat">Unconnected&nbsp;<span>{len(uncomn_nets)}</span></div>' +
            f'<div class="stat">Total Nets&nbsp;<span>{len(net_to_components)}</span></div>' +
            f'</div>'
        )
        sim_coverage_html = net_summary + sim_coverage_html

    matrix_headers = "".join(f"<th>{n}</th>" for n in cluster_nodes)
    matrix_rows = ""
    for ni in cluster_nodes:
        row = f"<tr><td class='row-label'>{ni}</td>"
        for nj in cluster_nodes:
            val = alpha_matrix.get(ni,{}).get(nj,0)
            cs  = ('background:var(--surface2);color:var(--muted);' if ni==nj
                   else 'background:#1a3a2a;color:var(--green);' if val>0.8
                   else 'background:#2d2a1a;color:#d29922;' if val>0.4 else '')
            row += f'<td style="{cs}">{val:.3f}</td>'
        matrix_rows += row + "</tr>"

    score_color = '#3fb950' if quality_score >= 80 else '#58a6ff' if quality_score >= 60 else '#d29922' if quality_score >= 40 else '#f85149'
    score_label = 'PASS' if quality_score >= 80 else 'REVIEW' if quality_score >= 60 else 'CAUTION' if quality_score >= 40 else 'FAIL'

    v_cards = _make_cluster_cards(v_groups, voltage_data, {})
    i_cards = _make_cluster_cards(i_groups, {}, current_data)
    p_cards = _make_cluster_cards(p_groups, voltage_data, current_data)
    current_warning = _get_current_warning_html(current_warning_type, mode)

    fmt_colors = {
        'transient':('#bc8cff','#2d1a4a'),'ac':('#58a6ff','#1a2a3a'),
        'csv':('#3fb950','#1a3a2a'),'dc':('#d29922','#2d2416'),
    }
    fmt_color, fmt_bg = fmt_colors.get(detected_format, ('#8b949e','#21262d'))
    format_badge = f'<span style="display:inline-block;padding:2px 10px;border-radius:3px;font-size:0.72em;font-weight:600;text-transform:uppercase;background:{fmt_bg};color:{fmt_color};border:1px solid {fmt_color}55;margin-left:8px;">{detected_format.upper()}</span>'

    if mode == "kicad":
        cq_color = '#3fb950' if cluster_quality_label == 'Excellent' else '#58a6ff' if cluster_quality_label == 'Good' else '#d29922' if cluster_quality_label == 'Fair' else '#f85149'
        cq_html = f'<span style="background:{cq_color}22;color:{cq_color};border:1px solid {cq_color}44;border-radius:4px;padding:2px 8px;font-size:0.78em;font-weight:600;margin-left:8px;">Clustering Quality: {cluster_quality_label}</span>' if cluster_quality_score else ''
        clustering_desc = f'<p style="color:var(--muted);font-size:0.82em;margin-bottom:16px;">DA clustering applied to voltage, current, and power dissipation (P=VxI). Includes current clustering (Ii/Ij) and power dissipation clustering (Pi/Pj). {cq_html}</p>'
        if i_groups:
            tabs_html = f"""<div class="tabs">
        <button class="tab-btn active" onclick="showTab('tab-v',this)">Voltage</button>
        <button class="tab-btn" onclick="showTab('tab-i',this)">Current</button>
        <button class="tab-btn" onclick="showTab('tab-p',this)">Power Dissipation</button>
      </div>
      <div id="tab-v" class="tab-content active"><p style="color:var(--muted);font-size:0.8em;margin-bottom:12px;">Voltage similarity: alpha(i,j) = Vi/Vj.</p><div class="cluster-grid">{v_cards}</div></div>
      <div id="tab-i" class="tab-content"><p style="color:var(--muted);font-size:0.8em;margin-bottom:12px;">Current similarity: alpha(i,j) = Ii/Ij.</p>{current_warning}<div class="cluster-grid">{i_cards}</div></div>
      <div id="tab-p" class="tab-content"><p style="color:var(--muted);font-size:0.8em;margin-bottom:12px;">Power Dissipation: Pi/Pj where P=VxI.</p><div class="cluster-grid">{p_cards}</div></div>"""
        else:
            tabs_html = f"""<div class="tabs">
        <button class="tab-btn active" onclick="showTab('tab-v',this)">Voltage</button>
        <button class="tab-btn" onclick="showTab('tab-i',this)">Current</button>
      </div>
      <div id="tab-v" class="tab-content active"><p style="color:var(--muted);font-size:0.8em;margin-bottom:12px;">Voltage similarity: alpha(i,j) = Vi/Vj.</p><div class="cluster-grid">{v_cards}</div></div>
      <div id="tab-i" class="tab-content"><p style="color:var(--muted);font-size:0.8em;margin-bottom:12px;">Current similarity: alpha(i,j) = Ii/Ij.</p>{current_warning}<div class="cluster-grid">{i_cards}</div></div>"""
    else:
        clustering_desc = '<p style="color:var(--muted);font-size:0.82em;margin-bottom:16px;">DA clustering applied to imported data.</p>'
        _p_btn   = '<button class="tab-btn" onclick="showTab(\'tab-p\',this)">Power Dissipation</button>' if p_groups else ''
        _p_block = f'<div id="tab-p" class="tab-content"><div class="cluster-grid">{p_cards}</div></div>' if p_groups else ''
        tabs_html = f"""<div class="tabs">
        <button class="tab-btn active" onclick="showTab('tab-v',this)">Voltage</button>
        <button class="tab-btn" onclick="showTab('tab-i',this)">Current</button>
        {_p_btn}
      </div>
      <div id="tab-v" class="tab-content active"><div class="cluster-grid">{v_cards}</div></div>
      <div id="tab-i" class="tab-content">{current_warning}<div class="cluster-grid">{i_cards}</div></div>
      {_p_block}"""

    graph_section  = ""
    issues_section = ""
    if mode == "kicad":
        graph_section = """<div class="section">
    <div class="section-header"><div class="section-dot"></div><div class="section-title">PCB Connectivity Graph</div></div>
    <div class="section-body" style="padding:0;"><iframe src="pcb_graph.html"></iframe></div>
  </div>"""
        issues_section = f"""<div class="section">
    <div class="section-header"><div class="section-dot" style="background:#d29922;"></div><div class="section-title">Issues &amp; Recommendations</div></div>
    <div class="section-body" style="padding:0;">
      <table><thead><tr><th>Type</th><th>Component</th><th>Issue</th><th>Fix</th></tr></thead><tbody>{findings_rows}</tbody></table>
    </div></div>"""

    current_matrix_section = ""
    if current_data and len(current_data) >= 2:
        i_nodes_list = list(current_data.keys())
        _, i_alpha   = _build_alpha(current_data)
        i_hdrs = "".join(f"<th>{n}</th>" for n in i_nodes_list)
        i_rows = ""
        for ni in i_nodes_list:
            row = f"<tr><td class='row-label'>{ni}</td>"
            for nj in i_nodes_list:
                val = i_alpha.get(ni,{}).get(nj,0)
                cs  = ('background:var(--surface2);color:var(--muted);' if ni==nj
                       else 'background:#1a3a2a;color:var(--green);' if val>0.8
                       else 'background:#2d2a1a;color:#d29922;' if val>0.4 else '')
                row += f'<td style="{cs}">{val:.3f}</td>'
            i_rows += row + "</tr>"
        current_matrix_section = f"""<div class="section">
    <div class="section-header" style="cursor:pointer;" onclick="toggleSection('curr-matrix')">
      <div class="section-dot"></div>
      <div class="section-title">Current Attenuation Matrix <span style="font-size:0.75em;color:var(--muted);">(click to expand)</span></div>
    </div>
    <div id="curr-matrix" style="display:none;"><div class="section-body"><div class="matrix-wrap">
      <table><thead><tr><th>Node</th>{i_hdrs}</tr></thead><tbody>{i_rows}</tbody></table>
    </div></div></div></div>"""

    power_matrix_section = ""
    if power_data and len(power_data) >= 2:
        p_nodes_list = list(power_data.keys())
        _, p_alpha   = _build_alpha(power_data)
        p_hdrs = "".join(f"<th>{n}</th>" for n in p_nodes_list)
        p_rows = ""
        for ni in p_nodes_list:
            row = f"<tr><td class='row-label'>{ni}</td>"
            for nj in p_nodes_list:
                val = p_alpha.get(ni,{}).get(nj,0)
                cs  = ('background:var(--surface2);color:var(--muted);' if ni==nj
                       else 'background:#1a3a2a;color:var(--green);' if val>0.8
                       else 'background:#2d2a1a;color:#d29922;' if val>0.4 else '')
                row += f'<td style="{cs}">{val:.3f}</td>'
            p_rows += row + "</tr>"
        power_matrix_section = f"""<div class="section">
    <div class="section-header" style="cursor:pointer;" onclick="toggleSection('pow-matrix')">
      <div class="section-dot"></div>
      <div class="section-title">Power Dissipation Matrix <span style="font-size:0.75em;color:var(--muted);">(click to expand)</span></div>
    </div>
    <div id="pow-matrix" style="display:none;"><div class="section-body"><div class="matrix-wrap">
      <table><thead><tr><th>Node</th>{p_hdrs}</tr></thead><tbody>{p_rows}</tbody></table>
    </div></div></div></div>"""

    net_comp_section    = _make_net_component_table(net_to_components) if mode == "kicad" else ""
    unconnected_section = _make_unconnected_section(unconnected_nodes)
    # Bar chart — cluster voltage ranges
    bar_chart_html = _make_cluster_bar_chart(v_groups, voltage_data)
    heatmap_section = f"""<div class="section">
    <div class="section-header"><div class="section-dot" style="background:#58a6ff;"></div><div class="section-title">Cluster Voltage Distribution</div></div>
    <div class="section-body">{bar_chart_html}</div>
  </div>"""

    mode_label = "KiCad PCB Mode" if mode == "kicad" else "File Import Mode"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>PCB Linter Report v4.5</title>
<style>
:root{{--bg:#0d1117;--surface:#161b22;--surface2:#1c2128;--surface3:#21262d;--border:#30363d;--border2:#21262d;--text:#c9d1d9;--text2:#e6edf3;--muted:#8b949e;--muted2:#484f58;--blue:#58a6ff;--green:#3fb950;--hover:#1c2128;}}
body.light-mode{{--bg:#ffffff;--surface:#f6f8fa;--surface2:#eaeef2;--surface3:#d0d7de;--border:#d0d7de;--border2:#eaeef2;--text:#24292f;--text2:#1f2328;--muted:#656d76;--blue:#0969da;--green:#1a7f37;--hover:#eaeef2;}}
*,*::before,*::after{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:'Consolas','Courier New',monospace;background:var(--bg);color:var(--text);font-size:13px;line-height:1.6;padding:32px 24px;}}
.page{{max-width:1100px;margin:0 auto;}}
.header{{border-left:3px solid #58a6ff;padding:20px 24px;margin-bottom:32px;background:var(--surface);border-radius:0 6px 6px 0;}}
.header h1{{font-size:1.25em;font-weight:600;color:var(--text2);}}
.header-sub{{color:var(--muted);font-size:0.85em;margin-top:4px;}}
.mode-badge{{display:inline-block;padding:3px 10px;border-radius:3px;font-size:0.72em;font-weight:600;text-transform:uppercase;background:#1a3a2a;color:var(--green);border:1px solid #3fb95055;margin-left:12px;}}
.score-row{{display:flex;align-items:center;gap:20px;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:20px 24px;margin-bottom:24px;}}
.score-num{{font-size:2.8em;font-weight:700;color:{score_color};line-height:1;min-width:80px;}}
.score-label{{font-size:0.75em;color:{score_color};letter-spacing:0.1em;margin-top:4px;}}
.score-bar-wrap{{flex:1;}}
.score-bar-track{{height:6px;background:var(--surface3);border-radius:3px;overflow:hidden;margin-bottom:8px;}}
.score-bar-fill{{height:100%;width:{quality_score}%;background:{score_color};border-radius:3px;}}
.score-stats{{display:flex;gap:24px;flex-wrap:wrap;}}
.stat{{color:var(--muted);}}.stat span{{color:var(--text);font-weight:600;}}
.section{{background:var(--surface);border:1px solid var(--border);border-radius:6px;margin-bottom:20px;overflow:hidden;}}
.section-header{{padding:12px 20px;border-bottom:1px solid var(--border);background:var(--surface2);display:flex;align-items:center;gap:10px;}}
.section-title{{font-size:0.8em;font-weight:600;letter-spacing:0.08em;color:var(--muted);text-transform:uppercase;}}
.section-dot{{width:6px;height:6px;border-radius:50%;background:#58a6ff;flex-shrink:0;}}
.section-body{{padding:20px;}}
.sim-block{{font-size:0.9em;color:var(--text);background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:12px 16px;margin-bottom:10px;}}
.sim-source{{font-size:0.8em;color:var(--muted);}}.sim-source span{{color:var(--text);}}
.tabs{{display:flex;gap:4px;margin-bottom:16px;flex-wrap:wrap;}}
.tab-btn{{padding:6px 14px;border-radius:4px;cursor:pointer;font-size:0.78em;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;border:1px solid var(--border);background:var(--surface2);color:var(--muted);font-family:inherit;}}
.tab-btn.active{{background:#58a6ff22;color:var(--blue);border-color:#58a6ff55;}}
.tab-content{{display:none;}}.tab-content.active{{display:block;}}
.cluster-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;}}
.cluster-card{{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:14px 16px;}}
.cluster-title{{font-size:0.8em;font-weight:600;letter-spacing:0.05em;margin-bottom:4px;text-transform:uppercase;}}
.cluster-count{{font-size:0.78em;color:var(--muted);margin-bottom:10px;}}
.node-list{{display:flex;flex-wrap:wrap;gap:6px;}}
.node-tag{{display:inline-flex;align-items:center;gap:6px;background:var(--surface3);border:1px solid var(--border);border-radius:3px;padding:3px 8px;font-size:0.8em;color:var(--text);}}
.node-v{{color:var(--muted);font-size:0.9em;}}
table{{width:100%;border-collapse:collapse;font-size:0.85em;}}
th{{background:var(--surface2);color:var(--muted);padding:9px 12px;text-align:left;border-bottom:1px solid var(--border);font-weight:500;font-size:0.8em;text-transform:uppercase;}}
td{{padding:9px 12px;border-bottom:1px solid var(--surface2);color:var(--text);vertical-align:middle;}}
tr:last-child td{{border-bottom:none;}}tr:hover td{{background:var(--surface2);}}
.row-label{{color:var(--muted);font-weight:600;}}
td code{{background:var(--surface3);border-radius:3px;padding:2px 6px;font-size:0.9em;color:#79c0ff;}}
.no-issues{{text-align:center;color:var(--green);padding:20px;font-size:0.9em;}}
.badge{{display:inline-block;padding:2px 8px;border-radius:3px;font-size:0.75em;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;}}
.badge-error{{background:#3d1a1a;color:#f85149;border:1px solid #6b2020;}}
.badge-warn{{background:#2d2416;color:#d29922;border:1px solid #5c4a1a;}}
.badge-info{{background:#1a2a3a;color:#58a6ff;border:1px solid #1f4070;}}
iframe{{width:100%;height:420px;border:none;border-radius:4px;display:block;background:var(--bg);}}
.matrix-wrap{{overflow-x:auto;}}.matrix-wrap table{{min-width:max-content;}}
.footer{{margin-top:32px;padding-top:16px;border-top:1px solid var(--border2);color:var(--muted2);font-size:0.78em;display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px;}}
#mode-toggle{{position:fixed;top:16px;right:20px;z-index:999;padding:7px 16px;border-radius:6px;cursor:pointer;font-family:'Consolas','Courier New',monospace;font-size:0.78em;font-weight:600;border:1px solid var(--border);background:var(--surface2);color:var(--muted);}}
</style>
<script>
function toggleSection(id){{var el=document.getElementById(id);if(el){{el.style.display=el.style.display==='none'?'block':'none';}}}}
function toggleMore(uid){{var m=document.getElementById("more_"+uid);var p=document.getElementById("preview_"+uid);if(m&&p){{m.style.display=m.style.display==='none'?'inline':'none';p.style.display=p.style.display==='none'?'inline':'none';}}}}
function showTab(id,btn){{document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));document.getElementById(id).classList.add('active');btn.classList.add('active');}}
function toggleMode(){{var b=document.body,btn=document.getElementById('mode-toggle');if(b.classList.contains('light-mode')){{b.classList.remove('light-mode');btn.textContent='☀ Light Mode';localStorage.setItem('pcblinter-mode','dark');}}else{{b.classList.add('light-mode');btn.textContent='🌙 Dark Mode';localStorage.setItem('pcblinter-mode','light');}}}}
window.onload=function(){{if(localStorage.getItem('pcblinter-mode')==='light'){{document.body.classList.add('light-mode');document.getElementById('mode-toggle').textContent='🌙 Dark Mode';}}}};
</script>
</head>
<body>
<button id="mode-toggle" onclick="toggleMode()">☀ Light Mode</button>
<div class="page">
  <div class="header">
    <div style="display:flex;align-items:center;flex-wrap:wrap;gap:8px;">
      <h1>{'PCB Linter Report' if mode=='kicad' else 'DA Clustering Report'}</h1>
      <span class="mode-badge">{mode_label}</span>{format_badge}
    </div>
    <div class="header-sub">DA Clustering — Voltage · Current · Power Dissipation</div>
  </div>

  {f'''<div class="score-row">
    <div><div class="score-num">{quality_score}</div><div class="score-label">{score_label}</div></div>
    <div class="score-bar-wrap">
      <div class="score-bar-track"><div class="score-bar-fill"></div></div>
      <div class="score-stats">
        <div class="stat">Issues&nbsp;<span>{len(findings)}</span></div>
        <div class="stat">V-Clusters&nbsp;<span>{len(v_groups)}</span></div>
        <div class="stat">I-Clusters&nbsp;<span>{len(i_groups)}</span></div>
        <div class="stat">P-Clusters&nbsp;<span>{len(p_groups)}</span></div>
        <div class="stat">Unconnected&nbsp;<span>{len(unconnected_nodes)}</span></div>
      </div>
    </div>
  </div>''' if mode=="kicad" else ''}

  <div class="section">
    <div class="section-header"><div class="section-dot"></div><div class="section-title">Status</div></div>
    <div class="section-body">
      <div class="sim-block">{clean_status}</div>
      <div class="sim-source">Source:&nbsp;<span>{clean_source}</span></div>
    </div>
  </div>

  {graph_section}

  <div class="section">
    <div class="section-header"><div class="section-dot"></div><div class="section-title">DA Clustering Results</div></div>
    <div class="section-body">{clustering_desc}{tabs_html}</div>
  </div>

  <div class="section">
    <div class="section-header"><div class="section-dot"></div><div class="section-title">Voltage Attenuation Matrix (alpha_ij = Vi/Vj)</div></div>
    <div class="section-body">
      <p style="color:var(--muted);font-size:0.8em;margin-bottom:10px;">
        Alpha(i,j) = Vi/Vj — input similarity matrix for DA clustering. 
        DA uses these ratios to compute soft cluster assignments via temperature-based annealing, converging to final hard clusters.
        <br>
        <span style="display:inline-flex;gap:16px;margin-top:6px;flex-wrap:wrap;">
          <span><span style="background:#1a3a2a;color:#3fb950;padding:1px 6px;border-radius:3px;">green</span> alpha &gt; 0.8 — high similarity</span>
          <span><span style="background:#2d2a1a;color:#d29922;padding:1px 6px;border-radius:3px;">yellow</span> 0.4 – 0.8 — moderate</span>
          <span><span style="background:var(--surface2);color:var(--muted);padding:1px 6px;border-radius:3px;">grey</span> diagonal — self reference</span>
          <span><span style="background:var(--surface);color:var(--text);padding:1px 6px;border-radius:3px;">dark</span> &lt; 0.4 — low similarity</span>
        </span>
      </p>
      <div class="matrix-wrap">
      <table><thead><tr><th>Node</th>{matrix_headers}</tr></thead><tbody>{matrix_rows}</tbody></table>
    </div></div>
  </div>

  {current_matrix_section}
  {power_matrix_section}
  {unconnected_section}
  {net_comp_section}
  {issues_section}
  {heatmap_section}
  {"" if not sim_coverage_html else f'''<div class="section">
    <div class="section-header"><div class="section-dot" style="background:#3fb950;"></div><div class="section-title">Simulation Coverage &amp; Net Summary</div></div>
    <div class="section-body">{sim_coverage_html}</div>
  </div>'''}

  <div class="footer">
    <span>PCB Linter Plugin v4.5</span>
    <span>DA Clustering — Deterministic Annealing | FOSSEE IIT Bombay</span>
  </div>
</div>
</body>
</html>"""