import json
import os
import platform
import re

from collections import defaultdict
from pathlib import Path
from typing import Iterable
from urllib.request import urlopen

import ayon_api
import ayon_core

from ayon_core.lib import Logger
from ayon_core.settings import get_current_project_settings

log = Logger.get_logger(__name__)


def show_timeline_file_chooser(parent) -> None:
    from qtpy.QtWidgets import QFileDialog

    timeline_file_path, _ = QFileDialog.getOpenFileName(parent)
    if not timeline_file_path:
        return

    process_timeline_file(Path(timeline_file_path))


def process_timeline_file(timeline_file_path: Path) -> None:
    import opentimelineio as otio

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

    settings = get_current_project_settings()["ubp_nuke_turnover"]
    default_handle_length = settings["default_handle_length"]

    handle_start_match = re.search(r"[_\d]H(\d+)", timeline_file_path.stem)
    handle_start = (
        int(handle_start_match.group(1))
        if handle_start_match
        else default_handle_length
    )

    handle_end_match = re.search(r"[_\d]T(\d+)", timeline_file_path.stem)
    handle_end = (
        int(handle_end_match.group(1)) if handle_end_match else default_handle_length
    )

    process_plate_timeline(
        shot_name_match.group("sequence_name"),
        shot_name_match.group("shot_name"),
        int(plate_number_match.group(1)),
        handle_start,
        handle_end,
        otio.adapters.read_from_file(str(timeline_file_path)),
    )


def process_plate_timeline(
    sequence_name: str,
    shot_name: str,
    plate_number: int,
    handle_start: int,
    handle_end: int,
    timeline: "otio.schema.Timeline",
) -> None:
    from ayon_nuke.api.lib import WorkfileSettings
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

    settings = get_current_project_settings()["ubp_nuke_turnover"]
    first_handle_frame_number = settings["first_handle_frame_number"]

    last_handle_frame_number = round(
        first_handle_frame_number + timeline_duration.value - 1
    )

    shot_entity_dict = ensure_sequence_and_shot_exist(
        sequence_name,
        shot_name,
        first_handle_frame_number,
        last_handle_frame_number,
        handle_start,
        handle_end,
    )
    turnover_task_entity_dict = ensure_turnover_task_exists(shot_entity_dict)

    ayon_core.pipeline.context_tools.change_current_context(
        shot_entity_dict,
        turnover_task_entity_dict,
    )
    WorkfileSettings().set_context_settings()

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
        source_name, settings
    ):
        create_nodes_for_layer(
            f"{plate_name_prefix}{plate_number:02}_{layer_name}",
            source_file_path,
            source_range,
            first_frame_number,
            scale,
            frame_number_map,
        )

    # HACK: And once for luck!
    QTimer.singleShot(0, lambda: WorkfileSettings().set_context_settings())


def ensure_sequence_and_shot_exist(
    sequence_name,
    shot_name,
    first_handle_frame_number,
    last_handle_frame_number,
    handle_start,
    handle_end,
):
    project_name = os.environ["AYON_PROJECT_NAME"]
    shot_entity_dicts = tuple(
        ayon_api.get_folders(
            project_name,
            folder_names=[shot_name],
            folder_types=["Shot"],
        )
    )
    if len(shot_entity_dicts) > 1:
        raise ValueError(
            f"More than one Shot exists called {shot_name}: {shot_entity_dicts}"
        )

    first_frame_number = first_handle_frame_number + handle_start
    last_frame_number = last_handle_frame_number - handle_end

    if shot_entity_dicts:
        shot_entity_dict = shot_entity_dicts[0]
        shot_attributes_dict = shot_entity_dict["attrib"]
        if (
            shot_attributes_dict["frameStart"] != first_frame_number
            or shot_attributes_dict["frameEnd"] < last_frame_number
            or shot_attributes_dict["handleStart"] != handle_start
            or shot_attributes_dict["handleEnd"] != handle_end
        ):
            raise ValueError(
                "Existing Shot doesn't have correct"
                f" frameStart: {first_frame_number}"
                f", frameEnd: {last_frame_number}"
                f", handleStart: {handle_start}"
                f", handleEnd: {handle_end}"
                f": {shot_entity_dict}"
            )

        return shot_entity_dict

    sequence_entity_dict = ensure_sequence_exists(sequence_name)

    shot_id = ayon_api.create_folder(
        project_name,
        name=shot_name,
        folder_type="Shot",
        parent_id=sequence_entity_dict["id"],
        attrib=dict(
            frameStart=first_frame_number,
            frameEnd=last_frame_number,
            handleStart=handle_start,
            handleEnd=handle_end,
        ),
    )
    return next(ayon_api.get_folders(project_name, folder_ids=[shot_id]))


def ensure_sequence_exists(sequence_name):
    project_name = os.environ["AYON_PROJECT_NAME"]
    sequence_entity_dicts = tuple(
        ayon_api.get_folders(
            project_name,
            folder_names=[sequence_name],
            folder_types=["Sequence"],
        )
    )
    if len(sequence_entity_dicts) > 1:
        raise ValueError(
            f"More than one Sequence exists called {sequence_name}: {sequence_entity_dicts}"
        )

    if sequence_entity_dicts:
        return sequence_entity_dicts[0]

    sequence_id = ayon_api.create_folder(
        project_name, name=sequence_name, folder_type="Sequence"
    )
    return next(ayon_api.get_folders(project_name, folder_ids=[sequence_id]))


def ensure_turnover_task_exists(shot_entity_dict):
    project_name = os.environ["AYON_PROJECT_NAME"]
    turnover_task_entity_dicts = tuple(
        ayon_api.get_tasks(
            project_name,
            task_names=["TURNOVER"],
            task_types=["Edit"],
            folder_ids=[shot_entity_dict["id"]],
        )
    )
    if len(turnover_task_entity_dicts) > 1:
        raise ValueError(
            f"More than one Task exists called TURNOVER: {turnover_task_entity_dicts}"
        )

    if turnover_task_entity_dicts:
        return turnover_task_entity_dicts[0]

    task_id = ayon_api.create_task(
        project_name,
        name="TURNOVER",
        task_type="Edit",
        folder_id=shot_entity_dict["id"],
        label="Turnover",
    )
    return next(ayon_api.get_tasks(project_name, task_ids=[task_id]))


def set_viewer_frame_ranges(frame_range_string: str) -> None:
    import nuke

    for viewer_node in nuke.allNodes(filter="Viewer"):
        viewer_node["frame_range"].setValue(frame_range_string)
        viewer_node["frame_range_lock"].setValue(True)


def get_source_range_first_frame_number_and_frame_number_map_from_timeline_clips(
    timeline: "otio.schema.Timeline", timeline_first_frame_number: int
) -> tuple["otio.opentime.TimeRange", int, dict[int, int]]:
    import opentimelineio as otio

    first_frame_number = None
    source_start_time = None
    previous_clip_source_end_time_exclusive = None
    previous_clip_timeline_end_time_exclusive = None
    frame_number_map = None
    accumulated_offset = 0
    for clip in timeline.find_clips():
        time_effect = None
        item = clip
        while item:
            time_effect = next(
                filter(
                    lambda effect: isinstance(effect, otio.schema.TimeEffect),
                    item.effects,
                ),
                None,
            )
            if time_effect:
                break

            item = item.parent()
        item_in_timeline_time = item or clip

        range_in_timeline = timeline.range_of_child(item_in_timeline_time)
        if first_frame_number is None:
            first_frame_number = (
                timeline_first_frame_number + range_in_timeline.start_time.value
            )
            source_start_time = clip.source_range.start_time
            previous_clip_source_end_time_exclusive = source_start_time
            previous_clip_timeline_end_time_exclusive = range_in_timeline.start_time
        if range_in_timeline.start_time < previous_clip_timeline_end_time_exclusive:
            # must be simultaneous clips, which we've seen
            # happen when the edit has stacked the identical clips
            # to use different effects on them and comp them together.
            # so we'll just happily ignore all but one in the stack,
            # and assume there's no ambiguity (can be picked up by
            # eye when comparing to edit ref and can come back to it
            # if it's something that needs validating)
            log.info(
                f"Skipping Clip {clip.name}. starts:"
                f" {range_in_timeline.start_time.to_timecode()}"
                " which is before the end of the previous clip:"
                f" {previous_clip_timeline_end_time_exclusive.to_timecode()}"
            )
            continue

        if range_in_timeline.start_time > previous_clip_timeline_end_time_exclusive:
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
            frame_number_map.update(
                {
                    frame_number: timeline_first_frame_number
                    + previous_clip_timeline_end_time_exclusive.value
                    - 1
                    + accumulated_offset
                    for frame_number in range(
                        round(
                            timeline_first_frame_number
                            + previous_clip_timeline_end_time_exclusive.value
                        ),
                        round(
                            timeline_first_frame_number
                            + range_in_timeline.start_time.value
                        ),
                    )
                }
            )
            accumulated_offset -= (
                range_in_timeline.start_time - previous_clip_timeline_end_time_exclusive
            ).value
            previous_clip_timeline_end_time_exclusive = range_in_timeline.start_time

        source_offset = (
            clip.source_range.start_time - previous_clip_source_end_time_exclusive
        ).value

        keyframe_values = None
        if time_effect:
            if isinstance(time_effect, otio.schema.FreezeFrame):
                keyframe_values = tuple(
                    (output_relative_frame_number, source_offset)
                    for output_relative_frame_number in range(
                        round(range_in_timeline.duration.value)
                    )
                )
            else:
                keyframe_values = (
                    time_effect.metadata.get("AAF", {})
                    .get("Parameters", {})
                    .get("PARAM_SPEED_OFFSET_MAP_U", {})
                    .get("keyframe_values", ())
                )
                if keyframe_values:
                    keyframe_values = tuple(
                        (
                            output_relative_frame_number,
                            input_relative_frame_number + source_offset,
                        )
                        for output_relative_frame_number, input_relative_frame_number in keyframe_values
                    )

        if source_offset and not keyframe_values:
            keyframe_values = tuple(
                (
                    output_relative_frame_number,
                    output_relative_frame_number + source_offset,
                )
                for output_relative_frame_number in range(
                    round(range_in_timeline.duration.value)
                )
            )

        previous_clip_source_end_time_exclusive = (
            clip.source_range.start_time + range_in_timeline.duration
        )
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
            max_input_relative_frame_number = 0
            for (
                output_relative_frame_number,
                input_relative_frame_number,
            ) in keyframe_values:
                max_input_relative_frame_number = max(
                    max_input_relative_frame_number, input_relative_frame_number
                )
                frame_number = (
                    timeline_first_frame_number
                    + previous_clip_timeline_end_time_exclusive.value
                    + output_relative_frame_number
                )
                offset = input_relative_frame_number - output_relative_frame_number
                frame_number_map[frame_number] = (
                    frame_number + accumulated_offset + offset
                )
            max_offset = max_input_relative_frame_number - output_relative_frame_number
            accumulated_offset += max_offset
            previous_clip_source_end_time_exclusive += otio.opentime.RationalTime(
                max_offset - source_offset, source_start_time.rate
            )
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

    print(
        range_in_timeline.end_time_exclusive().value,
        first_frame_number,
        timeline_first_frame_number,
        accumulated_offset,
    )
    return (
        otio.opentime.TimeRange(
            source_start_time,
            otio.opentime.RationalTime(
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
    timeline: "otio.schema.Timeline",
) -> Path:
    source_name = None
    for clip in timeline.find_clips():
        clip_source_name = clip.metadata.get("cmx_3600", {}).get("reel") or next(
            iter(clip.media_references().keys())
        )
        if clip_source_name == "DEFAULT_MEDIA":
            clip_source_name = re.sub(
                r"-[^-]+$",
                "",
                Path(
                    clip.media_references()[clip_source_name].metadata["AAF"][
                        "UserComments"
                    ]["Filepath"]
                ).stem,
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


def get_plate_name_prefix_layer_name_source_file_path_scale_quadruplets(
    source_name, settings
):
    still_life_take_version_full_name_regex = settings[
        "still_life_take_version_full_name_regex"
    ]
    match_ = re.search(still_life_take_version_full_name_regex, source_name)
    if match_:
        return get_still_life_plate_name_prefix_layer_name_source_file_path_scale_quadruplets(
            match_.group(1), settings
        )

    return get_reel_plate_name_prefix_layer_name_source_file_path_scale_quadruplets(
        source_name, settings
    )


def get_still_life_plate_name_prefix_layer_name_source_file_path_scale_quadruplets(
    take_version_full_name, settings
) -> Iterable[tuple[str, Path, float]]:
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
    source_name: str, settings
) -> Iterable[tuple[str, Path, float]]:
    reels_search_root_folder_path = Path(
        settings["reels_search_root_folder_path"][platform.system().lower()]
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
    import opentimelineio as otio

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
    current_node["use_limit"].setValue(True)
    current_node["first"].setValue(first_frame_number)
    current_node["last"].setValue(first_frame_number + source_range.duration.value - 1)

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
