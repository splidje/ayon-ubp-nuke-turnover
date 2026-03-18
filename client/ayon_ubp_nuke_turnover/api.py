import json
import platform
import re

from collections import defaultdict
from pathlib import Path
from typing import Iterable
from urllib.request import urlopen

import opentimelineio as otio

import ayon_core

from ayon_core.settings import get_current_project_settings


def show_timeline_file_chooser(parent) -> None:
    from qtpy.QtWidgets import QFileDialog

    timeline_file_path, _ = QFileDialog.getOpenFileName(parent)
    if not timeline_file_path:
        return

    process_timeline_file(Path(timeline_file_path))


def process_timeline_file(timeline_file_path: Path) -> None:
    shot_name_regex_pattern = r"(?P<shot_name>(?P<sequence_name>\d{3}_[A-Z]{2})_\d{4})"
    shot_name_match = re.search(shot_name_regex_pattern, timeline_file_path.stem)
    if not shot_name_match:
        value_error_message = (
            f"File name {timeline_file_path.stem}"
            f" doesn't match regex {shot_name_regex_pattern}"
        )
        raise ValueError(value_error_message)

    plate_number_regex_pattern = r"Plate(\d+)"
    plate_number_match = re.search(plate_number_regex_pattern, timeline_file_path.stem)
    if not plate_number_match:
        value_error_message = (
            f"File name {timeline_file_path.stem}"
            f" doesn't match regex {plate_number_regex_pattern}"
        )
        raise ValueError(value_error_message)

    process_plate_timeline(
        shot_name_match.group("sequence_name"),
        shot_name_match.group("shot_name"),
        int(plate_number_match.group(1)),
        otio.adapters.read_from_file(str(timeline_file_path)),
    )


def process_plate_timeline(
    sequence_name: str,
    shot_name: str,
    plate_number: int,
    timeline: otio.schema.Timeline,
) -> None:
    from qtpy.QtCore import QTimer

    import nuke

    root_node = nuke.root()
    timeline_duration = timeline.duration()
    project_frames_per_second = root_node["fps"].value()
    if timeline_duration.rate != project_frames_per_second:
        value_error_message = (
            f"Timeline {timeline.name} frames per second:"
            f" {timeline_duration.rate}"
            f" != project: {project_frames_per_second}"
        )
        raise ValueError(value_error_message)

    # TODO @splidje: create sequence + shot.
    print(sequence_name, shot_name, plate_number)
    # Then switch context and refresh frame range
    # (then no need for below)
    first_handle_frame_number = 1001
    last_handle_frame_number = round(
        first_handle_frame_number + timeline_duration.value - 1
    )
    frame_range_string = (
        f"{first_handle_frame_number + 8}-{last_handle_frame_number - 8}"
    )
    root_node["lock_range"].setValue(False)
    root_node["first_frame"].setValue(first_handle_frame_number)
    root_node["last_frame"].setValue(last_handle_frame_number)
    root_node["lock_range"].setValue(True)
    QTimer.singleShot(0, lambda: set_viewer_frame_ranges(frame_range_string))

    source_name = get_source_name_from_timeline_clips(timeline)

    source_range, first_frame_number, frame_number_map = (
        get_source_range_first_frame_number_and_frame_number_map_from_timeline_clips(
            timeline, first_handle_frame_number
        )
    )

    for (
        plate_name_prefix,
        layer_name,
        source_file_path,
        scale,
    ) in get_plate_name_prefix_layer_name_source_file_path_scale_quadruplets(
        source_name
    ):
        create_nodes_for_layer(
            f"{plate_name_prefix}{plate_number:02}_{layer_name}",
            source_file_path,
            source_range,
            first_frame_number,
            scale,
            frame_number_map,
        )


def set_viewer_frame_ranges(frame_range_string: str) -> None:
    import nuke

    for viewer_node in nuke.allNodes(filter="Viewer"):
        viewer_node["frame_range"].setValue(frame_range_string)
        viewer_node["frame_range_lock"].setValue(True)


def get_source_range_first_frame_number_and_frame_number_map_from_timeline_clips(
    timeline: otio.schema.Timeline, timeline_first_frame_number: int
) -> tuple[otio.opentime.TimeRange, int, dict[int, int]]:
    first_frame_number = None
    source_start_time = None
    previous_clip_timeline_end_time_exclusive = None
    frame_number_map = None
    accumulated_offset = 0
    for clip in timeline.clip_if():
        range_in_timeline = timeline.range_of_child(clip)
        if first_frame_number is None:
            first_frame_number = (
                timeline_first_frame_number + range_in_timeline.start_time.value
            )
            source_start_time = clip.source_range.start_time
            previous_clip_timeline_end_time_exclusive = range_in_timeline.start_time
        if (
            previous_clip_timeline_end_time_exclusive is not None
            and range_in_timeline.start_time
            != previous_clip_timeline_end_time_exclusive
        ):
            value_error_message = (
                f"Clip {clip.name} starts:"
                f" {range_in_timeline.start_time.to_timecode()}"
                " which isn't immediately following the previous clip:"
                f" {previous_clip_timeline_end_time_exclusive.to_timecode()}"
            )
            raise ValueError(value_error_message)

        keyframe_values = None
        for effect in clip.effects:
            if not isinstance(effect, otio.schema.TimeEffect):
                continue

            if isinstance(effect, otio.schema.FreezeFrame):
                keyframe_values = (
                    (0, 0),
                    (
                        range_in_timeline.duration.value - 1,
                        0,
                    ),
                )
                break

            keyframe_values = (
                effect.metadata.get("AAF", {})
                .get("Parameters", {})
                .get("PARAM_SPEED_OFFSET_MAP_U", {})
                .get("keyframe_values", ())
            )
            if keyframe_values:
                break

        if keyframe_values:
            if not frame_number_map:
                # fill frames so far
                frame_number_map = {
                    frame_number: frame_number
                    for frame_number in range(
                        timeline_first_frame_number,
                        round(
                            timeline_first_frame_number
                            + range_in_timeline.start_time.value
                        ),
                    )
                }
            for (
                output_relative_frame_number,
                input_relative_frame_number,
            ) in keyframe_values:
                frame_number = (
                    timeline_first_frame_number
                    + previous_clip_timeline_end_time_exclusive.value
                    + output_relative_frame_number
                )
                offset = input_relative_frame_number - output_relative_frame_number
                frame_number_map[frame_number] = (
                    frame_number + accumulated_offset + offset
                )
            accumulated_offset += offset
        elif frame_number_map:
            frame_number_map.update(
                {
                    frame_number: frame_number + accumulated_offset
                    for frame_number in range(
                        round(
                            timeline_first_frame_number
                            + previous_clip_timeline_end_time_exclusive.value
                        ),
                        round(
                            timeline_first_frame_number
                            + range_in_timeline.end_time_exclusive().value
                        ),
                    )
                }
            )

        previous_clip_timeline_end_time_exclusive = (
            range_in_timeline.end_time_exclusive()
        )

    return (
        otio.opentime.TimeRange.range_from_start_end_time(
            source_start_time,
            source_start_time
            + otio.opentime.RationalTime(
                (
                    range_in_timeline.end_time_exclusive().value
                    - (first_frame_number - timeline_first_frame_number)
                    + accumulated_offset
                ),
                source_start_time.rate,
            ),
        ),
        first_frame_number,
        frame_number_map,
    )


def get_source_name_from_timeline_clips(
    timeline: otio.schema.Timeline,
) -> Path:
    source_name = None
    for clip in timeline.clip_if():
        clip_source_name = clip.metadata.get("cmx_3600", {}).get("reel") or next(
            iter(clip.media_references().keys())
        )
        if clip_source_name == "DEFAULT_MEDIA":
            clip_source_name = re.sub(
                r"-[^-]+$",
                "",
                Path(clip.media_references()[clip_source_name]["target_url"]).stem,
            )
        if source_name and source_name != clip_source_name:
            value_error_message = (
                "All clips must reference the same reel."
                " Found at least two different ones:"
                f" {source_name} and {clip_source_name}"
            )
            raise ValueError(value_error_message)

        source_name = clip_source_name

    if not source_name:
        value_error_message = "Timeline contains no clips."
        raise ValueError(value_error_message)

    return source_name


def get_plate_name_prefix_layer_name_source_file_path_scale_quadruplets(source_name):
    still_life_take_version_full_name_regex = get_current_project_settings()[
        "ubp_nuke_turnover"
    ]["still_life_take_version_full_name_regex"]
    match_ = re.search(still_life_take_version_full_name_regex, source_name)
    if match_:
        return get_still_life_plate_name_prefix_layer_name_source_file_path_scale_quadruplets(
            match_.group(1)
        )

    return get_reel_plate_name_prefix_layer_name_source_file_path_scale_quadruplets(
        source_name
    )


def get_still_life_plate_name_prefix_layer_name_source_file_path_scale_quadruplets(
    take_version_full_name,
) -> Iterable[tuple[str, Path, float]]:
    settings = get_current_project_settings()["ubp_nuke_turnover"]
    bond_uri = settings["still_life_bond_uri"]
    root_folder_path = Path(
        settings["still_life_root_folder_path"][platform.system().lower()]
    )
    production_name = settings["still_life_bond_production_name"]
    image_render_type_name = settings["still_life_image_render_type_name"]
    exposure_name_to_layer_type_mapping_items = settings[
        "still_life_exposure_name_to_layer_type_mapping"
    ]
    response = urlopen(
        f"{bond_uri}/take_version_frame_relative_nuke_user_text_path"
        f"/{production_name}/{take_version_full_name}/{image_render_type_name}"
    )
    content_type = response.headers["Content-Type"]
    if content_type != "application/json":
        raise ValueError(f"Content Type isn't JSON: {content_type}")

    layer_name_by_layer_type = defaultdict(list)
    for exposure_name, frame_hashed_relative_path in json.load(response.fp).items():
        for (
            exposure_name_to_layer_name_mapping_item
        ) in exposure_name_to_layer_type_mapping_items:
            if not re.search(
                exposure_name_to_layer_name_mapping_item[
                    "still_life_exposure_name_regex"
                ],
                exposure_name,
            ):
                continue

            layer_type = exposure_name_to_layer_name_mapping_item["layer_type"]
            break

        else:
            raise ValueError(
                f"Couldn't find mapping for exposure name: {exposure_name}"
            )

        number = len(layer_name_by_layer_type[layer_type]) + 1
        layer_name = f"{layer_type}{number:02d}"
        layer_name_by_layer_type[layer_type].append(layer_name)
        yield "SM", layer_name, root_folder_path / frame_hashed_relative_path, 1


def get_reel_plate_name_prefix_layer_name_source_file_path_scale_quadruplets(
    source_name: str,
) -> Iterable[tuple[str, Path, float]]:
    reels_search_root_folder_path = Path(
        get_current_project_settings()["ubp_nuke_turnover"][
            "reels_search_root_folder_path"
        ][platform.system().lower()]
    )
    reel_file_path_string = next(
        reels_search_root_folder_path.glob(f"**/{source_name}.mxf"),
        None,
    )
    if not reel_file_path_string:
        file_not_found_message = (
            f"Failed to find reel {source_name}"
            f" under {reels_search_root_folder_path}"
        )
        raise FileNotFoundError(file_not_found_message)

    return (("LA", "BTY01", Path(reel_file_path_string), 0.5),)


def create_nodes_for_layer(
    variant_name,
    source_file_path,
    source_range,
    first_frame_number,
    scale,
    frame_number_map,
):
    import nuke

    read_node = nuke.nodes.Read(
        colorspace="ACES2065-1",
        before="black",
        after="black",
    )
    read_node["file"].fromUserText(str(source_file_path))
    if "sonyGamut" in read_node.knobs():
        read_node["sonyGamut"].setValue("ACES")

    project_frames_per_second = nuke.root()["fps"].value()
    source_frames_per_second = (
        read_node.metadata("input/frame_rate") or project_frames_per_second
    )
    if source_frames_per_second != project_frames_per_second:
        value_error_message = (
            f"Reel {source_file_path} frames per second:"
            f" {source_frames_per_second}"
            f" != project: {project_frames_per_second}"
        )
        raise ValueError(value_error_message)

    source_timecode = read_node.metadata("input/timecode", 1)
    source_start_time = (
        otio.opentime.RationalTime.from_timecode(
            source_timecode,
            source_frames_per_second,
        )
        if source_timecode
        # falling back implies it's stop motion,
        # so 1 to ignore the slate frame
        else otio.opentime.RationalTime(1, source_frames_per_second)
    )
    plate_first_frame = round((source_range.start_time - source_start_time).value) + 1
    plate_last_frame = (
        round((source_range.end_time_inclusive() - source_start_time).value) + 1
    )
    read_node["first"].setValue(plate_first_frame)
    read_node["last"].setValue(plate_last_frame)
    current_node = nuke.nodes.TimeOffset(
        inputs=(
            nuke.nodes.FrameRange(
                inputs=(read_node,),
                first_frame=plate_first_frame,
                last_frame=plate_last_frame,
            ),
        ),
        time_offset=first_frame_number - plate_first_frame,
    )
    if scale != 1:
        current_node = nuke.nodes.Reformat(
            inputs=(current_node,),
            type="scale",
            scale=0.5,
        )

    current_node.selectOnly()
    created_instance = ayon_core.pipeline.create.CreateContext(
        ayon_core.pipeline.registered_host()
    ).create("create_write_plate", variant_name)
    current_node = created_instance.transient_data["node"]

    if frame_number_map:
        current_node = nuke.nodes.TimeWarp(
            inputs=(current_node,),
        )
        lookup_knob = current_node["lookup"]
        lookup_knob.setAnimated()
        for (
            output_frame_number,
            input_frame_number,
        ) in frame_number_map.items():
            lookup_knob.setValueAt(input_frame_number, output_frame_number)
