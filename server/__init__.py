"""Server package."""
from typing import Type

from ayon_server.addons import BaseServerAddon

from .settings import DEFAULT_VALUES, MySettings


class MyAddon(BaseServerAddon):
    """Add-on class for the server."""
    settings_model: Type[MySettings] = MySettings

    async def get_default_settings(self) -> MySettings:
        """Return default settings."""
        settings_model_cls = self.get_settings_model()
        return settings_model_cls(**DEFAULT_VALUES)
