import dataclasses
import json
import os
import typing
import argparse

import hou


@dataclasses.dataclass
class HoudiniNodeError:
    """Dataclass to hold Houdini node error information."""

    node_path: str
    error_message: str


def get_errors(node: hou.node) -> typing.List[HoudiniNodeError]:
    """Retrieve errors from a Houdini node and its subchildren."""
    out_errors = [
        HoudiniNodeError(n.path(), "\n".join(n.errors()))
        for n in node.allSubChildren(top_down=True, recurse_in_locked_nodes=True)
        if n.errors()
    ]
    if node.errors():
        out_errors.append(HoudiniNodeError(node.path(), "\n".join(node.errors())))

    return out_errors


def save_geometry_from_houdini(config_json_path: str) -> None:
    """
    Load a Houdini file based on a configuration JSON, extract geometry from a specified node, and save it to disk.

    Args:
    config_json_path (str): Path to the JSON file containing the processing instructions.
    """
    # Load and parse the JSON configuration file
    with open(config_json_path, "r", encoding="utf-8") as file:
        config = json.load(file)

    current_node = hou.node("/obj")
    try:
        for directive in config:
            if not directive["enabled"]:
                continue
            # Load the Houdini file
            hou.hipFile.load(hou.text.expandString(directive["hip_file"]))

            for input in directive["inputs"]:
                _node = hou.node(input["node"])
                required = input.get("required", False)

                if not _node:
                    raise ValueError(
                        f"The specified node '{input['node']}' does not exist!"
                    )
                _parm = _node.parm(input["parm"])
                if not _parm:
                    raise ValueError(
                        f"The specified parameter '{input['parm']}' on {{input['node']}} does not exist!"
                    )
                if input["type"] == "input_file":
                    if (
                        not os.path.isfile(hou.text.expandString(input["value"]))
                        and required
                    ):
                        raise ValueError(
                            f"The specified file '{input['value']}' for parm '{input['parm']}' on node '{input['node']}' does not exist!"
                        )
                _parm.set(input["value"])

            debug_hip_path = directive.get("hip_file_debug", None)
            if debug_hip_path:
                debug_hip_path = hou.text.expandString(debug_hip_path)
                os.makedirs(os.path.dirname(debug_hip_path), exist_ok=True)
                hou.hipFile.save(file_name=debug_hip_path, save_to_recent_files=False)

            for executebutton_path in directive["execute"]:
                executebutton = hou.parm(executebutton_path)
                current_node = executebutton.node()
                executebutton.pressButton()
                print(
                    f"Pressed button {current_node}, {len(current_node.errors())} errors"
                )
                current_node.cook(force=True)
                if get_errors(current_node):
                    raise RuntimeError(
                        f"Errors encountered while processing {executebutton_path}"
                    )
    except Exception as e:
        print("Begin Houdini node errors".center(75, "-"))
        for node_info in get_errors(current_node):
            print(f"\nNODE:\n{node_info.node_path}\nERRORS:\n{node_info.error_message}")
        print("End Houdini node errors".center(75, "-"))
        raise e


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process Houdini geometry extraction.")
    parser.add_argument(
        "--work_directive",
        type=str,
        required=True,
        help="Path to the JSON configuration file for Houdini processing.",
    )
    args = parser.parse_args()

    save_geometry_from_houdini(args.work_directive)
