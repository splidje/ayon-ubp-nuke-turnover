import platform
import re
from pathlib import Path

import opentimelineio as otio
from ayon_core.settings import get_current_project_settings
from qtpy.QtCore import QObject, QTimer
from qtpy.QtWidgets import QFileDialog


def show_timeline_file_chooser(parent: QObject) -> None:
    timeline_file_path, _ = QFileDialog.getOpenFileName(parent)
    if not timeline_file_path:
        return

    process_timeline_file(Path(timeline_file_path))


def process_timeline_file(timeline_file_path: Path) -> None:
    shot_name_regex_pattern = (
        r"(?P<shot_name>(?P<sequence_name>\d{3}_[A-Z]{2})_\d{4})"
    )
    shot_name_match = re.search(
        shot_name_regex_pattern, timeline_file_path.stem
    )
    if not shot_name_match:
        value_error_message = (
            f"File name {timeline_file_path.stem}"
            f" doesn't match regex {shot_name_regex_pattern}"
        )
        raise ValueError(value_error_message)

    plate_number_regex_pattern = r"Plate(\d+)"
    plate_number_match = re.search(
        plate_number_regex_pattern, timeline_file_path.stem
    )
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
        f"{first_handle_frame_number + 16}-{last_handle_frame_number - 16}"
    )
    root_node["lock_range"].setValue(False)
    root_node["first_frame"].setValue(first_handle_frame_number)
    root_node["last_frame"].setValue(last_handle_frame_number)
    root_node["lock_range"].setValue(True)
    QTimer.singleShot(0, lambda: set_viewer_frame_ranges(frame_range_string))

    reel_file_path = get_reel_file_path_from_timeline_clips(timeline)

    read_node = nuke.nodes.Read(
        colorspace="ACES2065-1",
        before="black",
        after="black",
    )
    read_node["file"].fromUserText(str(reel_file_path))
    read_node["sonyGamut"].setValue("ACES")

    reel_frames_per_second = read_node.metadata("input/frame_rate")
    project_frames_per_second = root_node["fps"].value()
    if reel_frames_per_second != project_frames_per_second:
        value_error_message = (
            f"Reel {reel_file_path} frames per second:"
            f" {reel_frames_per_second}"
            f" != project: {project_frames_per_second}"
        )
        raise ValueError(value_error_message)

    source_range, first_frame_number, frame_number_map = (
        get_source_range_first_frame_number_and_frame_number_map_from_timeline_clips(
            timeline, first_handle_frame_number
        )
    )

    reel_start_time = otio.opentime.RationalTime.from_timecode(
        read_node.metadata("input/timecode", 1),
        reel_frames_per_second,
    )
    plate_first_frame = (
        round((source_range.start_time - reel_start_time).value) + 1
    )
    plate_last_frame = (
        round((source_range.end_time_inclusive() - reel_start_time).value) + 1
    )
    read_node["first"].setValue(plate_first_frame)
    read_node["last"].setValue(plate_last_frame)
    current_node = nuke.nodes.Reformat(
        inputs=(
            nuke.nodes.TimeOffset(
                inputs=(
                    nuke.nodes.FrameRange(
                        inputs=(read_node,),
                        first_frame=plate_first_frame,
                        last_frame=plate_last_frame,
                    ),
                ),
                time_offset=first_frame_number - plate_first_frame,
            ),
        ),
        type="scale",
        scale=0.5,
    )

    # TODO @splidje: AYON write node

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
                timeline_first_frame_number
                + range_in_timeline.start_time.value
            )
            source_start_time = clip.source_range.start_time
            previous_clip_timeline_end_time_exclusive = (
                range_in_timeline.start_time
            )
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
                offset = (
                    input_relative_frame_number - output_relative_frame_number
                )
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


def get_reel_file_path_from_timeline_clips(
    timeline: otio.schema.Timeline,
) -> Path:
    reel_name = None
    for clip in timeline.clip_if():
        clip_reel_name = clip.metadata.get("cmx_3600", {}).get("reel") or next(
            iter(clip.media_references().keys())
        )
        if reel_name and reel_name != clip_reel_name:
            value_error_message = (
                "All clips must reference the same reel."
                " Found at least two different ones:"
                f" {reel_name} and {clip_reel_name}"
            )
            raise ValueError(value_error_message)

        reel_name = clip_reel_name

    if not reel_name:
        value_error_message = "Timeline contains no clips."
        raise ValueError(value_error_message)

    reels_search_root_folder_path = Path(
        get_current_project_settings()["ubp_nuke_turnover"][
            "reels_search_root_folder_path"
        ][platform.system().lower()]
    )
    reel_file_path_string = next(
        reels_search_root_folder_path.glob(f"**/{reel_name}.mxf"),
        None,
    )
    if not reel_file_path_string:
        file_not_found_message = (
            f"Failed to find reel {reel_name}"
            f" under {reels_search_root_folder_path}"
        )
        raise FileNotFoundError(file_not_found_message)

    return Path(reel_file_path_string)
