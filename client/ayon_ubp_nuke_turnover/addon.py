import re
import sys

from typing import Any

from ayon_core.addon import AYONAddon

from .api import show_timeline_file_chooser, show_still_life_take_version_chooser
from .version import __version__

_MENU_LABEL = "UBP Turnover"


class UBPNukeTurnoverAddon(AYONAddon):
    name = "ubp_nuke_turnover"
    version = __version__

    def initialize(self, settings: dict[str, Any]) -> None:
        self.log.debug("Initializing UBP Nuke Turnover Addon")
        # Ensure AYON dependencies
        # override any modules packaged
        # with Nuke. (e.g. opentimelineio)
        for include_path in sys.path:
            # TODO: this I know isn't a great way to do this.
            # quite hacky. I asked for advice on Discord, but
            # haven't seen a reply yet, but thought I'd push this
            # so it's there to be seen.
            if not re.search("/dependency_packages/.*/dependencies/?", include_path):
                continue

            sys.path.remove(include_path)
            sys.path.insert(0, include_path)
            break

    def on_host_install(self, host, host_name, project_name):
        if host_name != "nuke":
            return

        import nuke

        from ayon_nuke.api.lib import get_main_window

        main_window = get_main_window()
        menubar = nuke.menu("Nuke")
        menu = menubar.addMenu(_MENU_LABEL)
        menu.addCommand(
            "Read Timeline File...", lambda: show_timeline_file_chooser(main_window)
        )
        menu.addCommand(
            "Select Still Life Take Version...", lambda: show_still_life_take_version_chooser(main_window)
        )
