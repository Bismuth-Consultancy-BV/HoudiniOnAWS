import array
import dataclasses
import json
import os
import platform
import re
import subprocess
import sys
import time
import typing
from enum import Enum

import hou

SUPPORTED_TERRAIN_RESOLUTIONS = [512, 1024, 2048, 4096, 8192]
GENERATED_FOLDERS_INTERNAL = ["gaea_generated_parms", "gaea_generated_bindings"]
GENERATED_FOLDERS_LABEL = ["Gaea Parameters", "Gaea Layer Bindings"]
IMPORT_BINDING_LABEL = "To Gaea"
EXPORT_BINDING_LABEL = "From Gaea"


@dataclasses.dataclass
class NodeMessages:
    FILE_NOT_FOUND = "The specified file does not exist! Please check if the path provided is correct: "
    INVALID_NODE_CACHE = (
        "The node cache is either invalid or missing. Please recook this node."
    )
    TERRAIN_FILE_INVALID = (
        "The provided terrain file cannot be parsed. Please validate it inside Gaea: "
    )
    UNDEFINED_VALUE_TYPE = (
        "The parsed value for this parameter is invalid. Please contact support: "
    )
    UNSPECIFIED_LAYER_BINDINGS = "The layer bindings for this node are not specified. Please check the Gaea Layer Bindings parameters!"
    LAYER_NOT_SQUARE = "Only square Heightfields are supported by Gaea"
    LAYER_NOT_POW2 = "Only power of 2 resolutions are supported by Gaea"
    NO_PARAMETERS = (
        "No parameters have been generated yet. Please generate them before cooking!"
    )
    REGISTRY_ERROR = (
        "An error occurred querying the registry for Gaea install. Please contact support.",
    )
    NOT_INSTALLED = (
        "Gaea is not installed on this machine. Please install Gaea to use this node."
    )
    NON_MATCHING_RESOLUTION = "The resolution of the Heightfield provided as input differs from the previous cook"
    GENERIC_GAEA_ERROR = "An error occurred inside Gaea. Please check the Gaea logs for more information."
    BAD_LICENSE = "Gaea has run into a licensing issue. If you have not yet activated your license, please do so now."
    UNSUPPORTED_LICENSE = "To run this plugin you need at least a professional license. Please consult the Gaea documentation for more information."
    UNSUPPORTED_PLATFORM = "The current platform is not supported by the plugin. Please use a supported platform to run this node. (Windows)"
    SWARMHOST_TIMEOUT = "Gaea SwarmHost did not start in time. Please check if Gaea is installed correctly and the SwarmHost service is running."

class ParmType(Enum):
    Undefined = ""
    Decoration = "Decoration"
    Choice = "Choice"
    Int = "Int"
    Bool = "Bool"
    Float = "Float"
    Range = "Range"
    Color = "Color"
    ImportBinding = IMPORT_BINDING_LABEL
    ExportBinding = EXPORT_BINDING_LABEL


class RenderIntent(Enum):
    Height = "Heightfield"
    Mask = "Mask"
    Colour = "Color"
    Default = "Default"


@dataclasses.dataclass
class GaeaInstall:
    """
    Class that defines the Gaea installation details.
    """

    ProcessName: str
    DiskLocation: str


@dataclasses.dataclass
class NodeInput:
    """
    Class that defines Gaea node Inputs.
    """

    Index: int  # pylint: disable=C0103
    Label: str  # pylint: disable=C0103
    Required: bool  # pylint: disable=C0103
    RenderIntent: str  # pylint: disable=C0103


@dataclasses.dataclass
class ParmValues:
    """
    Class that defines a ValueMap for Gaea parameters.
    """

    Value: typing.Union[float, int, str, bool, typing.List[float]]
    Values: typing.List[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class GaeaParm:
    """
    Class that defines Gaea node parameters.
    """

    Name: str  # pylint: disable=C0103
    Key: str  # pylint: disable=C0103
    Type: str  # pylint: disable=C0103
    Value: typing.Union[  # pylint: disable=C0103
        float, int, str, bool, typing.Dict[str, float], typing.List[float]
    ]
    Values: str = ""  # pylint: disable=C0103
    Tooltip: str = ""  # pylint: disable=C0103
    Min: float = 0.0  # pylint: disable=C0103
    Max: float = 1.0  # pylint: disable=C0103
    Group: str = "General"  # pylint: disable=C0103

    def __post_init__(self):
        # Convert the dictionary with X and Y to a list [X, Y] if Value is a dict
        if isinstance(self.Value, dict) and "X" in self.Value and "Y" in self.Value:
            self.Value = [self.Value["X"], self.Value["Y"]]

    def get_houdini_parm_template(self) -> hou.ParmTemplate:
        """
        Returns a hou.ParmTemplate based on the GaeaParm instance.
        """
        generated_parm = None
        clean_parm_name = self.Key
        parm_type = ParmType(self.Type)

        if parm_type == ParmType.Decoration:
            pass
        elif parm_type == ParmType.Choice:
            menu_values = tuple(self.Values)
            generated_parm = hou.MenuParmTemplate(
                clean_parm_name,
                self.Name,
                menu_items=menu_values,
                default_value=self.Value,
                menu_type=hou.menuType.Normal,  # type: ignore
            )
        elif parm_type == ParmType.Bool:
            generated_parm = hou.ToggleParmTemplate(
                clean_parm_name,
                self.Name,
                default_value=(int(self.Value),),  # type: ignore
            )
        elif parm_type == ParmType.Float:  # Float
            generated_parm = hou.FloatParmTemplate(
                clean_parm_name,
                self.Name,
                1,
                default_value=(float(self.Value),),  # type: ignore
            )
            generated_parm.setMinValue(self.Min)
            generated_parm.setMaxValue(self.Max)
            generated_parm.setMaxIsStrict(True)
        elif parm_type == ParmType.Int:  # Int
            generated_parm = hou.IntParmTemplate(
                clean_parm_name,
                self.Name,
                1,
                default_value=(int(self.Value),),  # type: ignore
            )
            generated_parm.setMinValue(self.Min)
            generated_parm.setMaxValue(self.Max)
            generated_parm.setMaxIsStrict(True)
        elif parm_type == ParmType.Range:
            generated_parm = hou.FloatParmTemplate(
                clean_parm_name,
                self.Name,
                2,
                default_value=(
                    float(self.Value[0]),  # type: ignore
                    float(self.Value[1]),  # type: ignore
                ),
            )
            generated_parm.setMinValue(self.Min)
            generated_parm.setMaxValue(self.Max)
            generated_parm.setMaxIsStrict(True)
        elif parm_type == ParmType.Color:
            generated_parm = hou.FloatParmTemplate(
                clean_parm_name,
                self.Name,
                3,
                default_value=(
                    float(self.Value[0]),  # type: ignore
                    float(self.Value[1]),  # type: ignore
                    float(self.Value[2]),  # type: ignore
                ),
                look=hou.parmLook.ColorSquare,
                naming_scheme=hou.parmNamingScheme.RGBA,  # type: ignore
            )
            generated_parm.setMinValue(self.Min)
            generated_parm.setMaxValue(self.Max)
            generated_parm.setMaxIsStrict(True)
        elif parm_type in [ParmType.ImportBinding, ParmType.ExportBinding]:
            generated_parm = hou.StringParmTemplate(
                clean_parm_name,
                self.Name,
                1,
                default_value=(self.Value,),  # type: ignore
                string_type=hou.stringParmType.Regular,  # type: ignore
            )
            parm_script = """import terraintoolutils
return terraintoolutils.buildNameMenu(kwargs['node'], 0)"""
            generated_parm.setItemGeneratorScriptLanguage(hou.scriptLanguage.Python)  # type: ignore
            generated_parm.setItemGeneratorScript(parm_script)
            generated_parm.setMenuType(hou.menuType.StringReplace)  # type: ignore
        elif parm_type == ParmType.Undefined:
            pass
        else:
            raise hou.Error(f"MISSING PARM SUPPORT: {self.Type}")

        if generated_parm:
            generated_parm.setHelp(self.Tooltip)
            generated_parm.setScriptCallbackLanguage(hou.scriptLanguage.Python)  # type: ignore
        return generated_parm, parm_type


class GaeaTerrainDefinition:
    """Class that defines the GaeaTerrainDefinition of a .terrain file"""

    def __init__(self, terrain_file):
        self.terrain_file = terrain_file
        self.raw_variables = {}

        self.parse_terrain_file()

    def parse_terrain_file(self):
        """
        Parses the terrain file and extracts the variables.
        """
        if not os.path.isfile(self.terrain_file):
            raise FileNotFoundError(NodeMessages.FILE_NOT_FOUND + self.terrain_file)
        with open(self.terrain_file, "r", encoding="utf-8") as in_file:
            terrain_data = json.load(in_file)

        terrain_assets = terrain_data.get("Assets", {})
        if not terrain_assets:
            raise AttributeError(NodeMessages.TERRAIN_FILE_INVALID + self.terrain_file)

        terrain_values = terrain_assets.get("$values", [])
        if not terrain_values:
            raise AttributeError(NodeMessages.TERRAIN_FILE_INVALID + self.terrain_file)
        terrain_values = terrain_values[0]

        automation = terrain_values.get("Automation", {})
        if not automation:
            raise AttributeError(NodeMessages.TERRAIN_FILE_INVALID + self.terrain_file)

        variables = automation.get("Variables", {})
        if not variables:
            raise AttributeError(NodeMessages.TERRAIN_FILE_INVALID + self.terrain_file)

        self.raw_variables = variables

    def get_clean_value(
        self, parm_type: ParmType, parm_data: typing.Dict
    ) -> ParmValues:
        """
        Utility that cleans the provided parm_data and returns a ParmValues instance.
        """
        parm_value = parm_data.get("Value")
        extra_data = parm_data.get("ExtraData", {})

        # Handle undefined parm types
        if parm_type == ParmType.Undefined:
            parm_type = (
                ParmType(extra_data.get("$type")) if extra_data else ParmType.Undefined
            )
            if parm_type == ParmType.Undefined:
                return ParmValues(Value=None)

        # If dealing with import/export bindings, ensure the value is a string
        if (
            parm_type in [ParmType.ImportBinding, ParmType.ExportBinding]
            and parm_value is None
        ):
            parm_value = ""

        if parm_value is None:
            raise ValueError(
                NodeMessages.UNDEFINED_VALUE_TYPE
                + f" for {parm_data.get('Name', 'UNNAMED')}"
            )

        if parm_type == ParmType.Float:
            return ParmValues(Value=parm_value)
        elif parm_type == ParmType.Decoration:
            return ParmValues(Value="")
        elif parm_type == ParmType.Range:
            range_type = parm_value.get("$type", "")
            if "QuadSpinner.Gaea.Engine.Fx.Float2" in range_type:
                return ParmValues(
                    Value=[parm_value.get("X", 0.0), parm_value.get("Y", 0.0)]
                )
            else:
                raise ValueError(NodeMessages.UNDEFINED_VALUE_TYPE + range_type)
        elif parm_type == ParmType.Color:
            color_type = parm_value.get("$type", "")
            if "QuadSpinner.Gaea.Engine.Fx.Color" in color_type:
                return ParmValues(
                    Value=[
                        parm_value.get("R", 0.0),
                        parm_value.get("G", 0.0),
                        parm_value.get("B", 0.0),
                    ]
                )
            else:
                raise ValueError(NodeMessages.UNDEFINED_VALUE_TYPE + color_type)
        elif parm_type == ParmType.Choice:
            if extra_data:
                parm_values = extra_data.get("$values", [])
                return ParmValues(Value=parm_value, Values=parm_values)
        elif parm_type == ParmType.Bool:
            return ParmValues(Value=bool(parm_value))
        return ParmValues(Value=parm_value)

    @property
    def parms(self):
        """
        Returns a list of GaeaParm instances with their associated group names.
        """
        parsed_parameters = []
        current_group = "General"

        # Sort parameters by their 'Order' field to respect layout
        items = sorted(
            ((key, p) for key, p in self.raw_variables.items() if isinstance(p, dict)),
            key=lambda kv: kv[1].get("Order", 0),
        )

        for key, parm_data in items:
            # Detect the Parm Type
            parm_type = ParmType(parm_data.get("Type", ""))

            if parm_type == ParmType.Undefined:
                if parm_data.get("ExtraData", "") == "1":
                    parm_type = ParmType.ImportBinding
                elif parm_data.get("ExtraData", "") == "2":
                    parm_type = ParmType.ExportBinding
                else:
                    raise hou.NodeError(
                        NodeMessages.UNDEFINED_VALUE_TYPE + f" for {key}"
                    )

            # If the parm is a decoration, we handle it differently
            if (
                parm_type == ParmType.Decoration
                and parm_data.get("Value") == "StartGroup"
            ):
                current_group = parm_data.get("Name", current_group)
                continue

            value_map = self.get_clean_value(parm_type=parm_type, parm_data=parm_data)

            # Build the GaeaParm, now passing Group=current_group
            parm = GaeaParm(
                Name=parm_data.get("Name", "UNDEFINED"),
                Key=parm_data.get(
                    "Name", "UNDEFINED" + f"_{parm_data.get('Order', '')}"
                ),
                Type=parm_type.value,
                Value=value_map.Value,
                Values=value_map.Values,
                Tooltip=parm_data.get("Tooltip", ""),
                Min=parm_data.get("Min", 0.0),
                Max=parm_data.get("Max", 1.0),
                Group=current_group,
            )
            parsed_parameters.append(parm)

        return parsed_parameters


def get_cleaned_internal_name(name: str) -> str:
    """
    Utility that cleans the provided name to a valid Houdini name.
    """
    text = re.sub(r"\W+", "_", name.lower())
    text = text.replace(" ", "_")
    return re.sub(r"_+", "_", text)


def set_houdini_parm_templategroup(
    node: hou.Node, gaea_terrain_definition: GaeaTerrainDefinition
) -> hou.ParmTemplateGroup:
    """
    Utility that sets the Houdini ParmTemplateGroup for the provided node based on the GaeaTerrainDefinition.
    """
    parm_group = node.parmTemplateGroup()

    # Reset the parm group to avoid duplicates
    for folder in GENERATED_FOLDERS_INTERNAL:
        parm_folder = parm_group.find(folder)
        if parm_folder:
            parm_group.remove(parm_folder)

    # Gaea Parameters Folder
    generated_parms = hou.FolderParmTemplate(
        GENERATED_FOLDERS_INTERNAL[0],
        GENERATED_FOLDERS_LABEL[0],
        folder_type=hou.folderType.Simple,  # type: ignore
    )
    parm_group.append(generated_parms)

    # Layer Bindings Folder
    generated_layer_bindings = hou.FolderParmTemplate(
        GENERATED_FOLDERS_INTERNAL[1],
        GENERATED_FOLDERS_LABEL[1],
        folder_type=hou.folderType.Simple,  # type: ignore
    )
    parm_group.append(generated_layer_bindings)

    for _, gaea_parm in enumerate(gaea_terrain_definition.parms):
        parm_template, parm_type = gaea_parm.get_houdini_parm_template()

        # Skip decorations and other unwanted types
        if parm_type in [ParmType.Decoration]:
            continue

        if parm_type in [ParmType.ImportBinding, ParmType.ExportBinding]:
            target_folder_name = GENERATED_FOLDERS_INTERNAL[1]
            gaea_parm.Group = parm_type.value
        else:
            target_folder_name = GENERATED_FOLDERS_INTERNAL[0]

        target_parm_folder_name = get_cleaned_internal_name(
            f"folder_{gaea_parm.Group.lower()}"
        )
        target_folder = parm_group.find(target_parm_folder_name)
        if not target_folder:
            target_folder = hou.FolderParmTemplate(
                target_parm_folder_name,
                gaea_parm.Group,
                folder_type=hou.folderType.Simple,  # type: ignore
            )
            parm_group.appendToFolder(
                parm_group.find(target_folder_name), target_folder
            )
            target_folder = parm_group.find(target_parm_folder_name)
        parm_group.appendToFolder(target_folder, parm_template)

    node.setParmTemplateGroup(parm_group)


def export_houdini_layers(
    geometry: hou.Geometry,
    layer_data: typing.Dict[str, typing.Dict[str, str]],
    cache_dir: str,
    extra_data: str,
    value_range: typing.Tuple[float, float],
) -> typing.Dict[str, typing.Dict[str, str]]:
    """
    Utility to export the layers from the provided geometry to the specified cache directory.
    """
    volumes = [prim.attribValue("name") for prim in geometry.prims()]

    def get_volume_data(layer_names: list) -> tuple[array.array, int, str]:
        """Extract volume data and determine metadata"""
        if len(layer_names) == 3:
            # Handle 3-channel RGB export
            rgb_volumes = []
            for layer_name in layer_names:
                if layer_name not in volumes:
                    raise hou.NodeError(f"Volume '{layer_name}' not found in geometry")
                rgb_volumes.append(geometry.prims()[volumes.index(layer_name)])

            # Extract and interleave RGB data
            channel_arrays = []
            for vol in rgb_volumes:
                data = array.array("f")
                data.frombytes(vol.allVoxelsAsString())
                channel_arrays.append(data)

            interleaved_data = array.array("f")
            array_length = channel_arrays[0].__len__()
            for i in range(array_length):
                for channel_array in channel_arrays:
                    interleaved_data.append(channel_array[i])

            return (
                interleaved_data,
                rgb_volumes[0].resolution()[0],
                RenderIntent.Colour.value,
            )
        else:
            # Handle single-channel export
            layer_name = layer_names[0]
            if layer_name not in volumes:
                raise hou.NodeError(f"Volume '{layer_name}' not found in geometry")
            volume = geometry.prims()[volumes.index(layer_name)]

            data = array.array("f")
            data.frombytes(volume.allVoxelsAsString())
            return data, volume.resolution()[0], RenderIntent.Height.value

    def write_files(
        export_path: str,
        data: array.array,
        resolution: int,
        channels: int,
        render_intent: str,
    ):
        """
        Write both .graw and .meta files.
        """
        # Write binary data
        with open(export_path + ".graw", "wb") as file:
            data.tofile(file)

        # Write metadata
        with open(export_path + ".meta", "w") as file:
            json.dump(
                {
                    "$id": str(1),
                    "Resolution": str(resolution),
                    "Channels": str(channels),
                    "RenderIntent": render_intent,
                    "Min": value_range[0],
                    "Max": value_range[1],
                },
                file,
                indent=4,
            )

    for parm_name, parm_data in layer_data.items():
        layer = parm_data["layer_name"]
        if layer == "":
            raise hou.NodeError(
                NodeMessages.UNSPECIFIED_LAYER_BINDINGS + f" for {parm_name}"
            )

        export_path = os.path.join(cache_dir, f"render_{extra_data}_{parm_name}")
        layer_names = layer.split()

        data, resolution, render_intent = get_volume_data(layer_names)
        channels = len(layer_names) if len(layer_names) == 3 else 1

        write_files(export_path, data, resolution, channels, render_intent)


def construct_dict_from_node_parms(node: hou.SopNode) -> dict[str, typing.Any]:
    """
    Utility that scrapes all Gaea parms in Houdini and parses them into the format expected by Gaea.
    """
    parm_dict = {}

    for parm in node.parmsInFolder((GENERATED_FOLDERS_LABEL[0],)):  # type: ignore

        label = parm.name()
        value = parm.eval()
        parm_template = parm.parmTemplate()

        if parm_template.type() == hou.parmTemplateType.Toggle:  # type: ignore
            value = bool(value)
        elif parm.tuple():
            parm_tuple_values = parm.tuple().eval()
            if len(parm_tuple_values) > 1:
                label = label[:-1]
            if len(parm_tuple_values) == 2:
                value = {"X": parm_tuple_values[0], "Y": parm_tuple_values[1]}
            if len(parm_tuple_values) == 3:
                value = {
                    "R": parm_tuple_values[0],
                    "G": parm_tuple_values[1],
                    "B": parm_tuple_values[2],
                }

        parm_dict[label] = value

    return parm_dict


def run_gaea_swarm(
    node: hou.SopNode,
    cache_dir: str,
    extra_data: str,
    in_layer_mappings: typing.Dict[str, typing.Dict[str, str]],
    out_layer_mappings: typing.Dict[str, typing.Dict[str, str]],
    resolution: int,
) -> bool:
    """
    Utility that runs the Gaea Swarm process with the provided parameters.
    Houdini starts SwarmHost.exe, which is a service that connects with Gaea Swarm.
    """
    node_parm_dict = construct_dict_from_node_parms(node)
    node_parm_path = os.path.join(cache_dir, f"parms_{extra_data}.json")
    # Unfortunately these are split, since Gaea expects the input to have an extension
    # and the output to not have an extension.
    for extension, mapping in [("", in_layer_mappings), (".graw", out_layer_mappings)]:
        for parm_name in mapping.keys():
            node_parm_dict[parm_name] = os.path.normpath(
                os.path.join(cache_dir, f"render_{extra_data}_{parm_name}{extension}")
            )
    terrain_file = node.parm("terrain_file").evalAsString()

    if not os.path.isfile(terrain_file):
        raise hou.NodeError(NodeMessages.FILE_NOT_FOUND + terrain_file)
    
    json.dump(
        node_parm_dict,
        open(node_parm_path, "w"),
        indent=4,
    )

    gaea_install = get_gaea_install_details()
    if node.parm("custom_swarm_exe").evalAsInt() == 1:
        gaea_swarm_executable = node.parm("swarm_exe").evalAsString()
    else:
        gaea_swarm_executable = os.path.join(
            gaea_install.DiskLocation, gaea_install.ProcessName
        )
    if not os.path.isfile(gaea_swarm_executable):
        raise hou.NodeError(
            NodeMessages.NOT_INSTALLED + f",  {gaea_swarm_executable} does not exist."
        )

    # We always need a background service instance to be running for SwarmHost
    ensure_gaea_service(gaea_swarm_executable)

    try:
        cmd = [
            gaea_swarm_executable,
            "--filename",
            terrain_file,
            "--vars",
            node_parm_path,
            "--verbose",
            "--buildpath",
            os.path.normpath(cache_dir),
            "--resolution",
            str(resolution),
            "--silent",
        ]
        cmd = [os.path.normpath(p) for p in cmd]

        log_path = os.path.join(cache_dir, f"gaea_swarm_{extra_data}.log")
        with hou.InterruptableOperation(
            "Running Gaea Swarm", open_interrupt_dialog=True
        ) as gaea_process:
            gaea_process.updateProgress(percentage=-1.0)
            with open(log_path, "w", encoding="utf-8") as log_file:
                p = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    startupinfo=get_startupinfo(),
                    text=True,
                    bufsize=1,
                )

                # Progress updates for Houdini
                for line in p.stdout:
                    sys.stdout.flush()
                    log_file.write(line)
                    log_file.flush()
                    built_match = re.search(r"Built (\d+)/(\d+)", line.rstrip())
                    if built_match:
                        current_step = int(built_match.group(1))
                        total_steps = int(built_match.group(2))
                        if total_steps > 0:
                            progress_percent = current_step / total_steps
                            gaea_process.updateProgress(percentage=progress_percent)
                    if "Build finished." in line:
                        break
                p.wait()

                if p.returncode != 0:
                    if p.returncode == 666:
                        raise hou.NodeError(NodeMessages.BAD_LICENSE)
                    elif p.returncode == 6666:
                        raise hou.NodeError(NodeMessages.UNSUPPORTED_LICENSE)
                    raise hou.NodeError(
                        NodeMessages.GENERIC_GAEA_ERROR
                        + f", Process failed with exit code {p.returncode}. Check log at {log_path}"
                    )
    except hou.OperationInterrupted:
        return False
    except subprocess.CalledProcessError as e:
        raise hou.NodeError(NodeMessages.GENERIC_GAEA_ERROR + f",  {e}")
    except FileNotFoundError as e:
        raise hou.NodeError(NodeMessages.NOT_INSTALLED + f",  {e}")
    except Exception as e:
        raise hou.NodeError(f"{e}")
    return True


def import_houdini_layers(
    geometry: hou.Geometry,
    layer_data: typing.Dict[str, typing.Dict[str, str]],
    cache_dir: str,
    extra_data: str,
) -> hou.Geometry:
    """
    Utility that imports the layers from the specified cache directory into the provided geometry.
    This function expects the cache directory to contain .graw and .meta files for each layer.
    """

    # We are using a verb here, so work around not all properties being able to be set using hou module
    volume_verb = hou.sopNodeTypeCategory().nodeVerbs()["volume"]
    volume_names = [prim.attribValue("name") for prim in geometry.prims()]
    reference_volume = geometry.prims()[0]
    reference_transform = reference_volume.intrinsicValue("transform")
    (reference_xres, reference_yres, reference_zres) = reference_volume.resolution()

    visualmode_lookup = {
        RenderIntent.Default.value: "heightfield",
        RenderIntent.Height.value: "heightfield",
        RenderIntent.Mask.value: "smoke",
        RenderIntent.Colour.value: "smoke",
    }
    for parm_name, parm_data in layer_data.items():
        layer_name = parm_data["layer_name"]
        if layer_name == "":
            raise hou.NodeError(
                NodeMessages.UNSPECIFIED_LAYER_BINDINGS + f" for {parm_name}"
            )
        import_path = os.path.join(cache_dir, f"render_{extra_data}_{parm_name}")
        metadata_path = import_path + ".meta"
        data_path = import_path + ".graw"

        if not os.path.isfile(metadata_path) or not os.path.isfile(data_path):
            raise FileNotFoundError(NodeMessages.INVALID_NODE_CACHE)

        with open(metadata_path, "r") as file:
            metadata = json.load(file)
        import_resolution = int(metadata.get("Resolution"))
        layer_channels = int(metadata.get("Channels", 1))
        intent = metadata.get("RenderIntent", RenderIntent.Height.value)
        if import_resolution != reference_xres or import_resolution != reference_yres:
            raise hou.NodeError(
                NodeMessages.NON_MATCHING_RESOLUTION
                + f" ({import_resolution}x{import_resolution}), whereas the input is ({reference_xres}x{reference_yres})"
            )

        def create_or_get_volume(vol_name, components=1):
            """
            Helper function to create or get an existing volume.
            """
            if vol_name not in volume_names:
                volume_verb.setParms(
                    {
                        "components": components,
                        "name": vol_name,
                        "samplediv": max(
                            reference_xres, reference_yres, reference_zres
                        ),
                        "twod": 1,
                    }
                )
                new_geometry = hou.Geometry()
                volume_verb.execute(new_geometry, [])
                new_volume = new_geometry.prims()[0]
                new_volume.setIntrinsicValue("transform", reference_transform)
                new_volume.setIntrinsicValue(
                    "volumevisualmode", visualmode_lookup[intent]
                )
                geometry.merge(new_geometry)
                volume = geometry.prims()[-1]
                volume_names.append(vol_name)
            else:
                volume = geometry.prims()[volume_names.index(vol_name)]
            return volume

        with open(data_path, "rb") as file:
            try:
                a = array.array("f")
                a.fromfile(
                    file,
                    reference_xres * reference_yres * reference_zres * layer_channels,
                )

                if layer_channels == 3:
                    # Create separate R, G, B volumes for 3-channel data
                    for channel_idx, suffix in enumerate([".r", ".g", ".b"]):
                        channel_layer_name = layer_name + suffix
                        channel_volume = create_or_get_volume(channel_layer_name)

                        # Extract channel data (every 3rd element starting from channel_idx)
                        channel_data = array.array("f", a[channel_idx::3])
                        channel_volume.setAllVoxelsFromString(channel_data)
                else:
                    # Single channel behavior
                    volume = create_or_get_volume(layer_name, layer_channels)
                    volume.setAllVoxelsFromString(a)

            except EOFError:
                raise ImportError(NodeMessages.NON_MATCHING_RESOLUTION)


def start_gaea_service(gaea_executable: str) -> None:
    """
    Utility that starts the Gaea SwarmHost service and waits for it to be ready.
    """

    if not os.path.isfile(gaea_executable):
        raise hou.NodeError(f"{NodeMessages.NOT_INSTALLED}: {gaea_executable}")

    try:
        p = subprocess.Popen(
            [gaea_executable],
            startupinfo=get_startupinfo(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        timeout = 10
        start_time = time.time()

        while time.time() - start_time < timeout:
            if p.poll() is not None:
                raise hou.NodeError(
                    f"Gaea SwarmHost terminated unexpectedly with code {p.returncode}"
                )
            try:
                line = p.stdout.readline()
                if line:
                    if "Primary instance running." in line.rstrip():
                        return
            except:
                time.sleep(0.1)
                continue
            time.sleep(0.1)

        # Timeout reached
        raise hou.NodeError(NodeMessages.SWARMHOST_TIMEOUT)

    except Exception as e:
        raise hou.NodeError(f"Failed to start Gaea SwarmHost: {e}")


def get_startupinfo() -> subprocess.STARTUPINFO:
    """
    Get configured startupinfo.
    """
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


def stop_gaea_service(process_name: str) -> None:
    """
    Utility that shuts down the Gaea service.
    """
    try:
        subprocess.run(
            ["taskkill", "/im", process_name],
            check=True,
            text=True,
            capture_output=True,
            startupinfo=get_startupinfo(),
        )
    except subprocess.CalledProcessError as e:
        print("Failed to kill process.", e.stderr)


def ensure_gaea_service(gaea_executable: str) -> None:
    """
    Utility that ensures Gaea SwarmHost is Running.
    """
    process_name = gaea_executable.replace("\\", "/").split("/")[-1]

    # Check if process is running but not responding
    if platform.system() == "Windows":
        try:
            process = subprocess.run(
                ["tasklist"],
                stdout=subprocess.PIPE,
                text=True,
                startupinfo=get_startupinfo(),
                check=True,
            )
            all_processes = process.stdout.split("\n")
        except subprocess.CalledProcessError:
            all_processes = []
    else:
        raise hou.NodeError(NodeMessages.UNSUPPORTED_PLATFORM)
    filtered_processes = [
        process for process in all_processes if process.startswith(process_name)
    ]

    if len(filtered_processes) > 0:
        return
    return start_gaea_service(gaea_executable)


def get_gaea_install_details() -> GaeaInstall:
    """
    Utility to get the default server details for Gaea.
    """

    key_path = r"SOFTWARE\QuadSpinner\Gaea\2.0"
    try:
        import winreg

        # Open the registry key
        registry_key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ
        )

        # Read the value
        install_path, _ = winreg.QueryValueEx(registry_key, "InstallPath")

        # Close the registry key
        winreg.CloseKey(registry_key)

    except FileNotFoundError:
        raise SystemError(NodeMessages.NOT_INSTALLED)  # pylint: disable=W0707
    except Exception as e:
        raise hou.NodeError(
            NodeMessages.REGISTRY_ERROR + f",  {e}"
        )  # pylint: disable=W0707

    return GaeaInstall(
        ProcessName="Gaea.SwarmHost.exe",
        DiskLocation=install_path,
    )
