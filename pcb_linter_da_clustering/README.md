# PCB Linter Plugin with Deterministic Annealing Clustering
**eSim Semester Long Internship — Spring 2026**
FOSSEE, IIT Bombay

## Author
**Prisha Bhatia**
B.Tech CSE (AI and ML), 3rd Year — VIT Bhopal University

- **Mentor:** Mr. Sumanto Kar, FOSSEE IIT Bombay
- **Principal Investigator:** Prof. Prabhu Ramachandran, Department of Aerospace Engineering, IIT Bombay

## Overview
This plugin is a KiCad PCB analysis tool (v4.5) that automatically extracts nets from any `.kicad_pcb` file, runs NGSpice simulation, and applies the **Deterministic Annealing (DA) clustering algorithm** to classify PCB nets into Voltage, Current, and Power Dissipation domains.

The DA algorithm is adapted from the work of Baranwal and Salapaka (2015) on clustering of electrical power networks, extended here to the PCB domain as a novel contribution.

The plugin generates a fully self-contained interactive HTML report with quality scores, attenuation matrices, a PCB connectivity graph, and a net-to-component map.

## Features
- Automatic net extraction from `.kicad_pcb` using the KiCad `pcbnew` Python API
- SPICE netlist generation with smart voltage source assignment based on net naming conventions
- NGSpice simulation in headless batch mode
- DA Clustering on Voltage, Current, and Power Dissipation domains
- eSim SubcircuitLibrary integration — automatically scans 647+ models and injects `.include` directives for ICs like LM741, LM7805, NE555, and more
- Interactive HTML report with dark/light mode toggle, quality score (0–100), attenuation matrices, PCB connectivity graph, and net map
- **Mode 1:** Full simulation pipeline from a KiCad PCB file
- **Mode 2:** Universal import mode — cluster any existing DC, AC, Transient, or CSV data file without needing a PCB file

## Novel Contributions

| Contribution | Description |
|---|---|
| DA Clustering on PCB Nets | Adapted from power grid analysis; uses αij = Vi/Vj as similarity metric |
| Current Clustering | Per-net current via Rload technique; uses αij = Ii/Ij |
| Power Dissipation Clustering | Per-node power Pi = Vi × Ii; uses αij = Pi/Pj |
| eSim SubcircuitLibrary Integration | Auto-scans all 647 models and injects correct `.include` |
| Universal Parser (Mode 2) | Supports DC, AC, Transient, and CSV formats for import |

## Plugin Architecture

**Mode 1 — KiCad PCB Workflow**
**Mode 2 — Universal Import Workflow**
## Folder Structure
## Installation

### Requirements
- KiCad 8.0 (with Python scripting enabled)
- eSim 2.5 (for SubcircuitLibrary integration)
- NGSpice (included with eSim)
- Python packages: `numpy`, `networkx`, `pyvis`

### Install Python dependencies:
```bash
pip install numpy networkx pyvis
```

### Steps
1. Copy the `pcb_linter` folder to your KiCad scripting plugins directory:

   **Linux / WSL:**
```bash
   cp -r pcb_linter ~/.local/share/kicad/8.0/scripting/plugins/
```
   **Windows:**
2. Open KiCad → Tools → Scripting Console
3. Run:
```python
   import pcbnew
   pcbnew.GetWizardsBackTrace()
```
4. The plugin will appear under **Tools → External Plugins → PCB Linter**

## Usage

### Mode 1 — KiCad PCB
1. Open your `.kicad_pcb` file in KiCad
2. Go to **Tools → External Plugins → PCB Linter**
3. Select **Mode 1** in the dialog
4. Confirm or adjust the detected power rail voltages
5. The plugin runs NGSpice and opens the HTML report automatically

### Mode 2 — Universal Import
1. Launch the plugin and select **Mode 2**
2. Browse and select a voltage data file (`.txt`, `.csv`, DC/AC/Transient format)
3. Optionally import a current data file
4. The DA clustering runs on the imported data and generates the HTML report

## Key Functions

| Function | Role |
|---|---|
| `run()` | Main entry point; orchestrates the full pipeline |
| `_generate_spice()` | Builds SPICE netlist from KiCad footprints |
| `_infer_net_voltage()` | Infers supply voltage from net name |
| `_build_alpha()` | Constructs the DA similarity matrix |
| `_da_clustering()` | Runs the Deterministic Annealing algorithm |
| `_make_report()` | Renders the interactive HTML report |
| `_detect_floating_nets()` | Identifies unconnected signal nets |
| `_get_power_rail_config()` | Dialog for user voltage confirmation |
| `_compute_cluster_quality()` | Computes intra and inter cluster quality |

## Test Results Summary

| Circuit | Mode | Score | V-Clusters | I-Clusters | P-Clusters | Quality |
|---|---|---|---|---|---|---|
| PIC Programmer V03 | 1 | 100 / PASS | 5 | 5 | 5 | Excellent |
| Stickhub USB Hub | 1 | 85 / PASS | 5 | 3 | 3 | Poor* |
| LM741 Subcircuit Test | 1 | 100 / PASS | 3 | 1 | 0 | Good |
| Precision Rectifier | 2 | N/A | 4 | 0 | 0 | N/A |

*Poor quality due to closely spaced USB differential pair voltages (1.65 V and 1.86 V) — known limitation for such circuits.

## HTML Report Sections

| Section | Description |
|---|---|
| Quality Score Bar | Score 0–100 with PASS / REVIEW / FAIL |
| DA Clustering Tabs | Voltage, Current, Power Dissipation clusters |
| Voltage Attenuation Matrix | Colour-coded αij = Vi/Vj |
| PCB Connectivity Graph | Interactive PyVis graph (Mode 1 only) |
| Net to Component Map | All nets with connected components |
| Issues & Recommendations | ERROR, WARNING, INFO with fix suggestions |
| Cluster Voltage Distribution | Bar chart per cluster |
| Simulation Coverage | Fully simulated and unmodeled component lists |
| Dark / Light Mode Toggle | Persistent across browser sessions |

## Limitations
- Per-net current clustering is not meaningful for purely series circuits
- ICs not present in the eSim SubcircuitLibrary are excluded from simulation
- Mode 2 transient import uses only the last time step value
- Clustering quality may show Poor for circuits with closely spaced voltage rails (e.g. USB differential pairs)

## References
1. M. Baranwal and S. M. Salapaka, "Clustering of Power Networks: An Information-Theoretic Perspective," IEEE CDC, 2015.
2. K. Rose, "Deterministic Annealing for Clustering," Proceedings of the IEEE, vol. 86, no. 11, 1998.
3. FOSSEE Project, IIT Bombay. eSim: Free and Open Source EDA Tool. https://esim.fossee.in
4. NGSpice Development Team. NGSpice User Manual, Version 42. https://ngspice.sourceforge.io
5. KiCad EDA. KiCad 8.0 Scripting Reference. https://docs.kicad.org/8.0/en/scripting/

## License
This project is developed under the FOSSEE Internship Program, IIT Bombay, and is distributed under the **GPL-3.0 License**.
