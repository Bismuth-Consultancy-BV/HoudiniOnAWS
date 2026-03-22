"""
HDA (Houdini Digital Asset) utility functions.

This module handles:
- Installing HDA files into a Houdini session
- Instantiating HDAs inside container nodes
- Extracting parameter schemas from HDA instances for UI generation
- Wiring HDA output into the export pipeline
"""

import logging
import os
import hou
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# These must match the nodes in session_runner.hip
CONTAINER_PATH = "/obj/CONTAINER"
EXPORT_NODE_REF_PATH = "/obj/EXPORT/EXPORT_NODE_REF"
EXPORT_GLTF_PATH = "/obj/EXPORT/EXPORT_GLTF"
OBJPATH_PARM = "objpath1"


def install_and_instantiate_hda(hda_file_path: str) -> "hou.Node":
    """
    Install an HDA file and instantiate it inside the CONTAINER node.

    Steps:
        1. Verify session_runner.hip nodes exist
        2. Install the HDA definition(s) from the file
        3. Create an instance of the HDA inside /obj/CONTAINER
        4. Wire the HDA into the EXPORT pipeline by setting objpath1

    Args:
        hda_file_path: Absolute path to the .hda file on disk.

    Returns:
        The instantiated HDA node.

    Raises:
        FileNotFoundError: If the HDA file does not exist.
        RuntimeError: If required session nodes are missing or HDA install fails.
    """
    
    # --- Validate the HDA file ---
    if not os.path.isfile(hda_file_path):
        raise FileNotFoundError(f"HDA file not found: {hda_file_path}")

    logger.info(f"Installing HDA from: {hda_file_path}")

    # --- Verify session_runner.hip nodes ---
    container_node = hou.node(CONTAINER_PATH)
    if not container_node:
        raise RuntimeError(
            f"CONTAINER node not found at {CONTAINER_PATH}. "
            "Make sure session_runner.hip is loaded first."
        )

    export_ref_node = hou.node(EXPORT_NODE_REF_PATH)
    if not export_ref_node:
        raise RuntimeError(
            f"EXPORT_NODE_REF not found at {EXPORT_NODE_REF_PATH}. "
            "Make sure session_runner.hip is loaded first."
        )

    export_gltf_node = hou.node(EXPORT_GLTF_PATH)
    if not export_gltf_node:
        raise RuntimeError(
            f"EXPORT_GLTF ROP not found at {EXPORT_GLTF_PATH}. "
            "Make sure session_runner.hip is loaded first."
        )

    # --- Install the HDA definitions ---
    # First, destroy any existing children so no instances of old node types remain.
    # This must happen BEFORE uninstalling the old file to avoid stale references.
    for child in container_node.children():
        try:
            child_name = child.name()
            child.destroy()
            logger.info(f"  Removed existing child: {child_name}")
        except hou.ObjectWasDeleted:
            logger.warning("  Skipped already-deleted child node")

    # Uninstall the old file to flush Houdini's cached definitions.
    # Since we always overwrite the same path, Houdini won't re-read the file
    # on installFile() unless we uninstall first.
    try:
        hou.hda.uninstallFile(hda_file_path)
        logger.info("Uninstalled previous HDA definitions from file")
    except hou.OperationFailed:
        pass  # No previous install at this path — that's fine

    logger.info("Installing HDA definitions...")
    hou.hda.installFile(hda_file_path)

    # Find what definitions were added
    definitions = hou.hda.definitionsInFile(hda_file_path)
    if not definitions:
        raise RuntimeError(
            f"No HDA definitions found in {hda_file_path}. "
            "The file may be corrupt or not a valid HDA."
        )

    for defn in definitions:
        logger.info(
            f"  Found definition: {defn.nodeTypeName()} "
            f"(category: {defn.nodeTypeCategory().name()}, "
            f"label: {defn.description()})"
        )

    # Use the first definition by default
    hda_def = definitions[0]
    node_type_name = hda_def.nodeTypeName()
    node_type_category = hda_def.nodeTypeCategory().name()

    logger.info(f"Using HDA: {node_type_name} (category: {node_type_category})")

    # --- Instantiate HDA inside CONTAINER ---
    hda_node = container_node.createNode(node_type_name, "user_hda")
    if not hda_node:
        raise RuntimeError(
            f"Failed to create node of type '{node_type_name}' inside {CONTAINER_PATH}"
        )

    hda_node.moveToGoodPosition()
    logger.info(f"Instantiated HDA node at: {hda_node.path()}")

    # --- Wire into EXPORT pipeline ---
    # The EXPORT_NODE_REF object merge needs the path to pull geometry from.
    # For a SOP-level HDA inside a geo container, we point to the node's render/display.
    # We set objpath1 to the full path of the HDA instance node.
    hda_node_path = hda_node.path()
    export_ref_parm = export_ref_node.parm(OBJPATH_PARM)
    if not export_ref_parm:
        raise RuntimeError(
            f"Parameter '{OBJPATH_PARM}' not found on {EXPORT_NODE_REF_PATH}"
        )

    export_ref_parm.set(hda_node_path)
    logger.info(f"Set {EXPORT_NODE_REF_PATH}/{OBJPATH_PARM} = {hda_node_path}")

    # No explicit cook here — the first GLTF ROP render will cook
    # the full chain (EXPORT_NODE_REF -> HDA) on demand.

    return hda_node


def extract_hda_parameters(hda_node: "hou.Node") -> Dict[str, Any]:
    """
    Extract parameter schema from an HDA node for UI generation.

    Reads the parameter template group of the HDA and builds a JSON-serializable
    schema that the browser client can use to generate controls.

    Args:
        hda_node: The instantiated HDA hou.Node.

    Returns:
        Dictionary with tool metadata and parameter definitions.
    """

    hda_def = hda_node.type().definition()
    node_type = hda_node.type()

    # Tool metadata
    tool_name = hda_def.description() if hda_def else node_type.description()
    tool_version = hda_def.version() if hda_def else "1.0"
    hda_icon = node_type.icon()

    result = {
        "tool_name": tool_name,
        "tool_version": tool_version,
        "icon": hda_icon,
        "description": (
            hda_def.comment() if hda_def and hda_def.comment() else f"Houdini Digital Asset: {tool_name}"
        ),
        "hda_node_path": hda_node.path(),
        "parameters": {},
    }

    # Walk the parameter template group
    ptg = hda_node.parmTemplateGroup()
    _extract_templates(ptg.entries(), hda_node, result["parameters"], folder_label="")

    param_count = len(result["parameters"])
    logger.info(
        f"Extracted {param_count} parameters from '{tool_name}' (v{tool_version})"
    )

    return result


# ---------------------------------------------------------------------------
#  Internal helpers
# ---------------------------------------------------------------------------

# Parameter template types we skip (internal / not user-facing)
_SKIP_TAGS = {"sidefx::"}

# Map hou.parmTemplateType to our schema types
_TYPE_MAP = {
    "Float": "float",
    "Int": "int",
    "String": "string",
    "Toggle": "checkbox",
    "Menu": "menu",
    "Button": "button",
    "FolderSet": None,  # skip
    "Separator": None,  # skip
    "Label": None,  # skip
    "Ramp": "ramp",
    "Data": None,  # skip (binary blobs)
}


def _extract_templates(
    templates,
    node: "hou.Node",
    out: Dict[str, Any],
    folder_label: str,
) -> None:
    """Recursively walk parm templates and populate *out*."""

    for tmpl in templates:
        tmpl_type_name = tmpl.type().name()

        # Recurse into folders
        if tmpl_type_name == "Folder":
            sub_label = tmpl.label()
            if folder_label:
                sub_label = f"{folder_label} / {sub_label}"
            _extract_templates(tmpl.parmTemplates(), node, out, sub_label)
            continue

        # Skip non-user-facing types
        schema_type = _TYPE_MAP.get(tmpl_type_name)
        if schema_type is None:
            continue

        # Skip invisible / disabled parameters
        if tmpl.isHidden():
            continue

        # Skip parameters that are tagged as internal
        tags = tmpl.tags()
        if any(k.startswith(pfx) for k in tags for pfx in _SKIP_TAGS):
            continue

        # Build per-parameter schema
        parm_name = tmpl.name()
        num_components = tmpl.numComponents()

        # For multi-component parms (e.g. vector3) we expose the tuple parm
        if num_components > 1:
            parm_tuple = node.parmTuple(parm_name)
            if not parm_tuple:
                continue
            parm_path = parm_tuple[0].path()
            # Strip the component suffix so the path points to the tuple root
            # e.g. /obj/CONTAINER/user_hda/tx -> /obj/CONTAINER/user_hda/t
            parm_path = parm_tuple[0].node().path() + "/" + parm_name
            default_val = [p.eval() for p in parm_tuple]
        else:
            parm = node.parm(parm_name)
            if not parm:
                continue
            parm_path = parm.path()
            default_val = parm.eval()

        entry: Dict[str, Any] = {
            "name": tmpl.label(),
            "type": schema_type,
            "default": default_val,
            "num_components": num_components,
            "ui": _build_ui_hint(tmpl, schema_type, num_components),
        }

        if folder_label:
            entry["folder"] = folder_label

        # Help string
        help_text = tmpl.help()
        if help_text:
            entry["description"] = help_text

        # Menu items
        if schema_type == "menu":
            menu_items = tmpl.menuItems()
            menu_labels = tmpl.menuLabels()
            entry["ui"]["options"] = [
                {"value": item, "label": lbl}
                for item, lbl in zip(menu_items, menu_labels)
            ]

        out[parm_path] = entry


def _build_ui_hint(
    tmpl, schema_type: str, num_components: int
) -> Dict[str, Any]:
    """Build the ``ui`` sub-dict used by the browser to pick a control widget."""
    import hou

    hint: Dict[str, Any] = {
        "label": tmpl.label(),
    }

    if schema_type == "float":
        min_val = tmpl.minValue()
        max_val = tmpl.maxValue()
        if num_components == 3:
            hint["control"] = "vector3"
        elif num_components == 4:
            hint["control"] = "vector4"
        else:
            hint["control"] = "slider"
        hint["min"] = min_val
        hint["max"] = max_val
        hint["step"] = 0.01
        # Check for color tag
        look = tmpl.look()
        if look == hou.parmLook.ColorSquare:
            hint["control"] = "color_picker" if num_components >= 3 else "slider"

    elif schema_type == "int":
        hint["control"] = "number"
        hint["min"] = tmpl.minValue()
        hint["max"] = tmpl.maxValue()
        hint["step"] = 1

    elif schema_type == "string":
        string_type = tmpl.stringType()
        if string_type == hou.stringParmType.FileReference:
            hint["control"] = "file_browser"
        elif string_type == hou.stringParmType.NodeReference:
            hint["control"] = "text"  # node refs displayed as text
        else:
            hint["control"] = "text"
            # Multiline hint: tags may include "editor" = "1"
            if tags_hint := tmpl.tags():
                if tags_hint.get("editor") == "1":
                    hint["multiline"] = True

    elif schema_type == "checkbox":
        hint["control"] = "checkbox"

    elif schema_type == "menu":
        hint["control"] = "select"

    elif schema_type == "button":
        hint["control"] = "button"

    elif schema_type == "ramp":
        hint["control"] = "ramp"

    return hint


def export_gltf(output_dir: Optional[str] = None) -> str:
    """
    Trigger the GLTF export ROP and return the path to the exported file.

    If *output_dir* is given the ROP output path is overridden.

    Returns:
        The path of the exported GLTF file.
    """

    rop = hou.node(EXPORT_GLTF_PATH)
    if not rop:
        raise RuntimeError(f"GLTF ROP not found at {EXPORT_GLTF_PATH}")

    if output_dir:
        # Override the output file path on the ROP if needed
        out_parm = rop.parm("file") or rop.parm("sopoutput")
        if out_parm:
            out_path = os.path.join(output_dir, "export.glb")
            out_parm.set(out_path)

    logger.info(f"Rendering GLTF export via {EXPORT_GLTF_PATH}...")
    rop.render()
    logger.info("GLTF export complete")

    # Determine the output path from the ROP
    out_parm = rop.parm("file") or rop.parm("sopoutput")
    if out_parm:
        return hou.text.expandString(out_parm.eval())

    return ""
