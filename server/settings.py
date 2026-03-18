"""Settings for the addon."""

from ayon_server.settings import (
    BaseSettingsModel,
    MultiplatformPathModel,
    SettingsField,
)


class StillLifeExposureNameToLayerTypeMappingItemModel(BaseSettingsModel):
    still_life_exposure_name_regex: str = SettingsField(
        "",
        title="Still Life Exposure Name RegEx",
    )
    layer_type: str = SettingsField("", title="Layer Type")


class UBPNukeTurnoverSettings(BaseSettingsModel):
    reels_search_root_folder_path: MultiplatformPathModel = SettingsField(
        default_factory=MultiplatformPathModel,
        title="Reels Search Root Folder Path",
    )
    still_life_bond_uri: str = SettingsField(
        "",
        title="Still Life Bond URI",
    )
    still_life_root_folder_path: MultiplatformPathModel = SettingsField(
        default_factory=MultiplatformPathModel,
        title="Still Life Root Folder Path",
    )
    still_life_bond_production_name: str = SettingsField(
        "",
        title="Still Life Bond Production Name",
    )
    still_life_take_version_full_name_regex: str = SettingsField(
        "",
        title="Still Life Take Version Full Name RegEx",
    )
    still_life_image_render_type_name: str = SettingsField(
        "",
        title="Still Life Image Render Type Name",
    )
    still_life_exposure_name_to_layer_type_mapping: list[
        StillLifeExposureNameToLayerTypeMappingItemModel
    ] = SettingsField(
        title="Still Life Exposure Name to Layer Type Mapping",
        default_factory=list,
    )


DEFAULT_VALUES = dict(
    reels_search_root_folder_path=dict(
        windows="",
        darwin="",
        linux="",
    ),
    still_life_bond_uri="",
    still_life_root_folder_path=dict(
        windows="",
        darwin="",
        linux="",
    ),
    still_life_bond_production_name="",
    still_life_take_version_full_name_regex="",
    still_life_image_render_type_name="",
    still_life_exposure_name_to_layer_type_mapping=[],
)
