"""Settings for the addon."""
from ayon_server.settings import BaseSettingsModel, MultiplatformPathModel, SettingsField

DEFAULT_VALUES = dict(
    reels_search_root_folder_path=dict(
        windows="",
        darwin="",
        linux="",
    ),
)


class UBPNukeTurnoverSettings(BaseSettingsModel):
    reels_search_root_folder_path: MultiplatformPathModel = SettingsField(
        default_factory=MultiplatformPathModel,
        title="Reels Search Root Folder Path",
        description="Top level folder under which to search for reels referenced in editorial timelines.",
    )
