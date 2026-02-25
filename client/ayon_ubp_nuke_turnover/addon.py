from ayon_core.addon import AYONAddon

from .api import show_timeline_file_chooser
from .version import __version__

_MENU_LABEL = "UBP Turnover"


class UBPNukeTurnoverAddon(AYONAddon):
    name = "ubp_nuke_turnover"
    version = __version__
    host_name = "nuke"

    def on_host_install(self, host, host_name, project_name):
        import nuke
        
        from ayon_nuke.api.lib import get_main_window

        main_window = get_main_window()
        menubar = nuke.menu("Nuke")
        menu = menubar.addMenu(_MENU_LABEL)
        menu.addCommand("Read Timeline File...", lambda: show_timeline_file_chooser(main_window))