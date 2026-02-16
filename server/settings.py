"""Settings for the addon."""
from typing import Any

from ayon_server.settings import BaseSettingsModel

DEFAULT_VALUES: dict[str, Any] = {}


class MySettings(BaseSettingsModel):
    """Settings for the addon."""
