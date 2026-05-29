import pcbnew
import os


class PCBLinterPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "PCB Linter"
        self.category = "Analysis"
        self.description = "DA Clustering — Voltage, Current & Power Dissipation Analysis"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(
            os.path.dirname(__file__), "icon.png"
        )

    def Run(self):
        from . import pcb_linter_main
        pcb_linter_main.run()


PCBLinterPlugin().register()