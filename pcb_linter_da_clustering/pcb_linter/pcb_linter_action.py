"""
PCB Linter Action Plugin — Toolbar Button
By Prisha Bhatia, FOSSEE IIT Bombay
"""

import pcbnew
import os
import sys
import importlib

class PCBLinterAction(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "PCB Linter — DA Clustering"
        self.category = "PCB Analysis"
        self.description = "PCB Linter with DA Clustering by Prisha Bhatia, FOSSEE IIT Bombay"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "icon.png"
        )

    def Run(self):
        try:
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

            import pcb_linter.pcb_linter_main as linter
            importlib.reload(linter)
            linter.run()

        except Exception as e:
            import traceback
            print(f"[PCB Linter] ERROR: {e}")
            traceback.print_exc()

PCBLinterAction().register()
