bl_info = {
    "name": "FS25 Texture-Bake-Machine",
    "author": "Maddog Design & Djain",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "3D View > Sidebar > FS25 Bake",
    "description": "GIANTS FS25 shader-aware texture bake workflow",
    "category": "Material",
}

from pathlib import Path
import re

import bpy
import bmesh
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup
from bpy_extras.io_utils import ExportHelper

try:
    from .translations import TRANSLATIONS
except ImportError:
    TRANSLATIONS = {}


ADDON_ID = __package__ or __name__


def _shader_root(game_root: str) -> Path | None:
    """Resolve an FS25 game root or directly selected data directory."""
    if not game_root:
        return None

    root = Path(bpy.path.abspath(game_root))
    candidates = (root / "data" / "shaders", root / "shaders")
    return next((path for path in candidates if path.is_dir()), None)


def _addon_preferences(context):
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def _shader_found(prefs, property_name: str, filename: str) -> bool:
    if getattr(prefs, property_name, False):
        return True
    if not prefs.shader_directory:
        return False
    return (Path(prefs.shader_directory) / filename).is_file()


def _available_official_shaders(_settings, context):
    prefs = _addon_preferences(context)
    if not prefs:
        return [("NONE", "Shader source unavailable", "")]

    shaders = []
    if _shader_found(prefs, "has_building_shader", "buildingShader.xml"):
        shaders.append(("BUILDING", "buildingShader", "Official GIANTS Building Shader"))
    if _shader_found(prefs, "has_placeable_shader", "placeableShader.xml"):
        shaders.append(("PLACEABLE", "placeableShader", "Official GIANTS Placeable Shader"))
    return shaders or [("NONE", "No matching shaders found", "Please check the FS25 path")]


def _available_vehicle_shaders(_settings, context):
    prefs = _addon_preferences(context)
    if not prefs:
        return [("NONE", "Shader source unavailable", "")]
    if _shader_found(prefs, "has_vehicle_shader", "vehicleShader.xml"):
        return [("VEHICLE", "vehicleShader", "Official GIANTS Vehicle Shader")]
    return [("NONE", "vehicleShader not found", "Please check the FS25 path")]


class FS25BakePreferences(AddonPreferences):
    bl_idname = ADDON_ID

    game_root: StringProperty(
        name="FS25 Game Folder",
        description="Farming Simulator 25 root folder; read-only access",
        subtype="DIR_PATH",
    )
    scan_status: StringProperty(name="Status", default="Not checked yet")
    shader_directory: StringProperty(name="Shader Folder", default="")
    shader_count: IntProperty(name="Shader Files", default=0, min=0)
    has_building_shader: BoolProperty(default=False)
    has_placeable_shader: BoolProperty(default=False)
    has_vehicle_shader: BoolProperty(default=False)

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Official GIANTS Shader Source", icon="LOCKED")
        box.prop(self, "game_root")
        box.operator("fs25_bake.scan_shader_folder", icon="VIEWZOOM")

        status = layout.box()
        icon = "CHECKMARK" if self.shader_count else "INFO"
        status.label(text=self.scan_status, icon=icon)
        if self.shader_directory:
            status.label(text=f"Folder: {self.shader_directory}")
        status.label(text=f"Shader XML files found: {self.shader_count}")
        status.label(text="Read-only access – game files are never modified.", icon="LOCKED")


class FS25BAKE_OT_scan_shader_folder(Operator):
    bl_idname = "fs25_bake.scan_shader_folder"
    bl_label = "Check Shader Folder"
    bl_description = "Search FS25 subfolders and official shaders using read-only access"

    def execute(self, context):
        prefs = _addon_preferences(context)
        if not prefs:
            self.report({"ERROR"}, "Add-on preferences could not be read")
            return {"CANCELLED"}

        shader_root = _shader_root(prefs.game_root)
        prefs.shader_directory = ""
        prefs.shader_count = 0
        prefs.has_building_shader = False
        prefs.has_placeable_shader = False
        prefs.has_vehicle_shader = False

        if shader_root is None:
            prefs.scan_status = "No data/shaders folder found"
            self.report({"ERROR"}, prefs.scan_status)
            return {"CANCELLED"}

        xml_files = tuple(shader_root.rglob("*.xml"))
        names = {path.name.casefold() for path in xml_files}
        prefs.shader_directory = str(shader_root)
        prefs.shader_count = len(xml_files)
        prefs.has_building_shader = "buildingshader.xml" in names
        prefs.has_placeable_shader = "placeableshader.xml" in names
        prefs.has_vehicle_shader = "vehicleshader.xml" in names
        prefs.scan_status = "FS25 shader source ready"

        found = []
        if prefs.has_building_shader:
            found.append("buildingShader")
        if prefs.has_placeable_shader:
            found.append("placeableShader")
        if prefs.has_vehicle_shader:
            found.append("vehicleShader")
        suffix = f" ({', '.join(found)})" if found else ""
        self.report({"INFO"}, f"{len(xml_files)} shader XML files found{suffix}")
        return {"FINISHED"}


CHANNEL_ITEMS = (
    ("WEAR_GLOSS", "Wear / Gloss", "Abnutzung oder Glanz"),
    ("AO", "AO", "Ambient Occlusion"),
    ("DIRT", "Dirt", "Dirt mask"),
    ("MOSS", "Moss", "Moss mask"),
    ("CUSTOM", "Custom", "Custom mask"),
    ("NONE", "None", "Leave channel unused"),
)


def _update_preview_channel(settings, _context):
    settings.preview_combined = False
    if not (settings.preview_wear or settings.preview_ao or settings.preview_dirt):
        settings.preview_ao = True


def _blend_directory_display(_settings):
    if bpy.data.filepath:
        return str(Path(bpy.data.filepath).parent)
    return bpy.path.abspath("//") or str(Path.home())


def _effective_export_directory(settings):
    if settings.export_use_blend_directory:
        return _blend_directory_display(settings)
    return bpy.path.abspath(settings.export_directory)

def _reset_export_name_confirmation(settings, _context):
    settings.export_name_confirmed = False


class FS25BakeSettings(PropertyGroup):
    mask_enabled: BoolProperty(name="Enable Shader", default=False)
    mask_profile: EnumProperty(
        name="Mask Profile",
        items=(
            ("MMASK", "Building Shader - mMask", "Building and placeable masks"),
            ("VMASK", "Vehicle Shader - vMask", "Vehicle masks – coming later"),
            ("CUSTOM", "Custom Specular", "Specular from Diffuse, Normal, AO and material values"),
            ("ATLAS", "Texture Array | Atlas", "Stack four equally sized textures vertically"),
        ),
        default="MMASK",
    )
    mmask_shader: EnumProperty(name="Shader", items=_available_official_shaders)
    vmask_shader: EnumProperty(name="Shader", items=_available_vehicle_shaders)
    bake_uv_mode: EnumProperty(
        name="Bake UV",
        items=(
            ("AUTO", "Automatic", "Automatically determine a suitable target UV"),
            ("SAME", "Same UV", "Use active UV as source and target"),
            ("CREATE", "Auto Bake UV", "Prepare a new compact Bake UV"),
        ),
        default="AUTO",
    )
    output_resolution: EnumProperty(
        name="Resolution",
        items=(
            ("AUTO", "Automatic", "Use source texture resolution"),
            ("1024", "1024 x 1024", "1K output"),
            ("2048", "2048 x 2048", "2K output"),
            ("4096", "4096 x 4096", "4K output"),
            ("8192", "8192 x 8192", "8K output"),
        ),
        default="AUTO",
    )
    channel_r: EnumProperty(name="R", items=CHANNEL_ITEMS, default="WEAR_GLOSS")
    channel_g: EnumProperty(name="G", items=CHANNEL_ITEMS, default="AO")
    channel_b: EnumProperty(name="B", items=CHANNEL_ITEMS, default="DIRT")
    channel_a: EnumProperty(name="A", items=CHANNEL_ITEMS, default="NONE")
    wear_image: PointerProperty(name="Wear / Gloss", type=bpy.types.Image)
    ao_image: PointerProperty(name="AO", type=bpy.types.Image)
    dirt_image: PointerProperty(name="Dirt", type=bpy.types.Image)
    moss_image: PointerProperty(name="Moss", type=bpy.types.Image)
    packed_preview_image: PointerProperty(name="Combined RGB", type=bpy.types.Image)
    specular_diffuse_image: PointerProperty(name="Diffuse", type=bpy.types.Image)
    specular_normal_image: PointerProperty(name="Normal", type=bpy.types.Image)
    specular_ao_image: PointerProperty(name="AO Texture", type=bpy.types.Image)
    specular_preview_image: PointerProperty(name="Specular RGB", type=bpy.types.Image)
    specular_ao_mode: EnumProperty(
        name="AO Source",
        items=(
            ("AUTO", "Automatic", "AO Texture or bake AO from mesh"),
            ("TEXTURE", "Texture", "Select AO texture manually"),
        ),
        default="AUTO",
    )
    specular_use_material_values: BoolProperty(
        name="Use Material Values",
        description="Use Metallic and Roughness directly from the active Principled BSDF",
        default=True,
    )
    specular_metallic: FloatProperty(
        name="Metallic",
        description="Metallic value for the blue Specular channel",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    specular_roughness: FloatProperty(
        name="Roughness",
        description="Roughness; Red automatically receives 1 minus Roughness",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    export_base_name: StringProperty(
        name="Filename",
        description="Custom base name for the mask texture",
        default="",
        update=_reset_export_name_confirmation,
    )
    export_auto_name: BoolProperty(
        name="Automatic Name",
        description="Create the output name automatically from the active object",
        default=True,
        update=_reset_export_name_confirmation,
    )
    export_name_confirmed: BoolProperty(default=False)
    export_use_blend_directory: BoolProperty(
        name="Use Blender File Folder",
        description="Export to the folder of the currently saved Blend file",
        default=True,
    )
    export_blend_directory_display: StringProperty(
        name="File Location",
        description="Folder of the currently saved Blend file",
        subtype="DIR_PATH",
        get=_blend_directory_display,
        options={"SKIP_SAVE"},
    )
    export_directory: StringProperty(
        name="File Location",
        description="Manually selected export folder",
        subtype="DIR_PATH",
    )
    preview_combined: BoolProperty(name="Combined RGB", default=True)
    preview_wear: BoolProperty(name="Wear R", default=False, update=_update_preview_channel)
    preview_ao: BoolProperty(name="AO G", default=True, update=_update_preview_channel)
    preview_dirt: BoolProperty(name="Dirt B", default=False, update=_update_preview_channel)
    show_mmask: BoolProperty(name="mMask", default=False)
    show_preview: BoolProperty(name="Mask Preview", default=False)
    show_vmask: BoolProperty(name="vMask", default=False)
    show_custom: BoolProperty(name="Custom Mask", default=False)
    show_export: BoolProperty(name="Export", default=False)
    atlas_map_type: EnumProperty(
        name="Atlas Type",
        items=(
            ("DIFFUSE", "Diffuse", "Color texture atlas"),
            ("NORMAL", "Normal", "Non-Color normal texture atlas"),
            ("SPECULAR", "Specular", "Non-Color specular texture atlas"),
        ),
        default="DIFFUSE",
    )
    atlas_layer_1: PointerProperty(name="Texture 1", type=bpy.types.Image)
    atlas_layer_2: PointerProperty(name="Texture 2", type=bpy.types.Image)
    atlas_layer_3: PointerProperty(name="Texture 3", type=bpy.types.Image)
    atlas_layer_4: PointerProperty(name="Texture 4", type=bpy.types.Image)
    atlas_preview_image: PointerProperty(name="Texture Atlas", type=bpy.types.Image)


def _draw_collapsible_header(layout, settings, prop_name: str, label: str, icon: str):
    header = layout.box()
    row = header.row(align=True)
    expanded = getattr(settings, prop_name)
    row.prop(
        settings,
        prop_name,
        text="",
        icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
        emboss=False,
    )
    row.label(text=label, icon=icon)
    return expanded


def _draw_centered_title(layout, text: str, icon: str = "NONE"):
    """Draw a native, theme-safe framed section title."""
    title_box = layout.box()
    title_row = title_box.row()
    title_row.alignment = "CENTER"
    title_row.label(text=text, icon=icon)


def _draw_mask_channel(layout, settings, title, color_name, channel_prop, image_prop):
    """Draw one compact RGB(A) assignment and its native Blender image field."""
    _draw_centered_title(layout, title)
    selection = layout.row(align=True)
    selection.label(text=f"{color_name}:")
    selection.prop(settings, channel_prop, text="")
    layout.template_ID(settings, image_prop, new="image.new", open="image.open")


def _active_mesh_uv_info(context):
    obj = context.active_object
    if obj is None or obj.type != "MESH":
        return None

    uv_layers = obj.data.uv_layers
    active_uv = uv_layers.active.name if uv_layers.active else "None"
    enabled_addons = context.preferences.addons.keys()
    node_wrangler = any(
        addon_id == "node_wrangler" or addon_id.endswith(".node_wrangler")
        for addon_id in enabled_addons
    )

    if obj.mode == "EDIT":
        mesh = bmesh.from_edit_mesh(obj.data)
        selected_faces = sum(face.select for face in mesh.faces)
    else:
        selected_faces = sum(face.select for face in obj.data.polygons)

    mapping_node = None
    material = obj.active_material
    if material is not None and material.use_nodes and material.node_tree is not None:
        mapping_node = next(
            (node for node in material.node_tree.nodes if node.type == "MAPPING"),
            None,
        )

    mapping_scale = None
    if mapping_node is not None and mapping_node.inputs.get("Scale") is not None:
        mapping_scale = tuple(mapping_node.inputs["Scale"].default_value)

    detected_target = None
    if mapping_node is not None and uv_layers:
        detected_target = "Same UV - mapping detected"
    elif len(uv_layers) >= 2:
        detected_target = f"UV2 detected: {uv_layers[1].name}"

    if not uv_layers:
        status = ("No UV map available.", "ERROR")
    elif mapping_node is not None:
        status = ("One UV is sufficient: mapping detected.", "CHECKMARK")
    elif len(uv_layers) >= 2:
        status = ("UV2 detected and ready as target.", "CHECKMARK")
    else:
        status = ("No mapping and no UV2 detected.", "ERROR")

    return {
        "mesh": obj.name,
        "selected_faces": selected_faces,
        "uv_count": len(uv_layers),
        "active_uv": active_uv,
        "node_wrangler": node_wrangler,
        "mapping_node": mapping_node,
        "mapping_scale": mapping_scale,
        "detected_target": detected_target,
        "status": status,
    }


def _draw_info_row(layout, label: str, value: str):
    row = layout.row(align=True)
    split = row.split(factor=0.38)
    split.label(text=label)
    split.label(text=value)


def _draw_shader_activation(layout, settings):
    """Draw a Blender-native shader state and its full-width toggle button."""
    enabled = settings.mask_enabled

    status = layout.row(align=True)
    status.alert = enabled
    status.label(
        text="Shader enabled" if enabled else "Shader disabled",
        icon="REC" if enabled else "RADIOBUT_OFF",
    )

    toggle = layout.row()
    toggle.scale_y = 1.15
    toggle.prop(
        settings,
        "mask_enabled",
        text="Disable Shader" if enabled else "Enable Shader",
        icon="PAUSE" if enabled else "PLAY",
        toggle=True,
    )



class FS25BAKE_OT_invert_preview_channel(Operator):
    bl_idname = "fs25_bake.invert_preview_channel"
    bl_label = "Invert Channel"
    bl_description = "Invert the currently selected preview channel"

    @classmethod
    def poll(cls, context):
        settings = context.scene.fs25_bake_settings
        active = sum((settings.preview_wear, settings.preview_ao, settings.preview_dirt))
        return not settings.preview_combined and active == 1

    def execute(self, context):
        self.report({"INFO"}, "Channel inversion will be added with image processing")
        return {"FINISHED"}


def _output_size(settings, preferred_image=None):
    if settings.output_resolution != "AUTO":
        size = int(settings.output_resolution)
        return size, size
    if preferred_image is not None and preferred_image.size[0] and preferred_image.size[1]:
        return int(preferred_image.size[0]), int(preferred_image.size[1])
    return 2048, 2048


def _image_luminance(image, width, height):
    source_width, source_height = int(image.size[0]), int(image.size[1])
    if source_width < 1 or source_height < 1:
        raise ValueError(f"Texture has no image data: {image.name}")
    pixels = np.empty(source_width * source_height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    rgba = pixels.reshape((source_height, source_width, 4))
    if (source_width, source_height) != (width, height):
        x_index = np.minimum(
            (np.arange(width, dtype=np.int64) * source_width) // width,
            source_width - 1,
        )
        y_index = np.minimum(
            (np.arange(height, dtype=np.int64) * source_height) // height,
            source_height - 1,
        )
        rgba = rgba[y_index[:, None], x_index[None, :]]
    return (
        rgba[..., 0] * 0.2126
        + rgba[..., 1] * 0.7152
        + rgba[..., 2] * 0.0722
    ).astype(np.float32, copy=False)


def _write_packed_image(existing, name, width, height, red, green, blue, alpha=None):
    image = existing
    if image is None or image.name not in bpy.data.images:
        image = bpy.data.images.new(name, width=width, height=height, alpha=True)
    elif tuple(image.size) != (width, height):
        image.scale(width, height)

    pixels = np.ones((height, width, 4), dtype=np.float32)
    pixels[..., 0] = red
    pixels[..., 1] = green
    pixels[..., 2] = blue
    if alpha is not None:
        pixels[..., 3] = alpha
    image.file_format = "PNG"
    try:
        image.colorspace_settings.name = "Non-Color"
    except TypeError:
        pass
    image.pixels.foreach_set(pixels.ravel())
    image.update()
    return image



def _atlas_layers(settings):
    return (
        settings.atlas_layer_1,
        settings.atlas_layer_2,
        settings.atlas_layer_3,
        settings.atlas_layer_4,
    )


def _atlas_validation(settings):
    layers = _atlas_layers(settings)
    missing = [str(index) for index, image in enumerate(layers, 1) if image is None]
    if missing:
        return False, f"Missing textures: {', '.join(missing)}", 0, 0, 0

    sizes = [(int(image.size[0]), int(image.size[1])) for image in layers]
    if any(width < 1 or height < 1 for width, height in sizes):
        return False, "One or more textures have no image data", 0, 0, 0
    if any(width != height for width, height in sizes):
        return False, "All textures must be square", 0, 0, 0
    if len(set(sizes)) != 1:
        expected = f"{sizes[0][0]} x {sizes[0][1]}"
        received = ", ".join(f"{width} x {height}" for width, height in sizes[1:])
        return False, f"Layer size mismatch: expected {expected}; received {received}", 0, 0, 0

    width, layer_height = sizes[0]
    if width not in {512, 1024, 2048, 4096}:
        return False, "Layer size must be 512, 1024, 2048, or 4096 pixels", 0, 0, 0

    depths = [int(image.depth) for image in layers]
    if len(set(depths)) != 1:
        return False, "All textures must use the same color depth", 0, 0, 0

    return True, f"Ready: 4 layers at {width} x {layer_height}, {depths[0]} bit", width, layer_height * 4, depths[0]


def _image_rgba(image):
    width, height = int(image.size[0]), int(image.size[1])
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return pixels.reshape((height, width, 4))


def _build_vertical_atlas(settings):
    valid, message, width, height, _depth = _atlas_validation(settings)
    if not valid:
        raise RuntimeError(message)

    # Blender stores rows bottom-to-top. Reverse the layer list so Texture 1
    # appears at the top of the exported vertical atlas.
    pixels = np.concatenate(
        [_image_rgba(image) for image in reversed(_atlas_layers(settings))],
        axis=0,
    )
    image = settings.atlas_preview_image
    if image is None or image.name not in bpy.data.images:
        image = bpy.data.images.new("FS25_Texture_Array_Atlas", width=width, height=height, alpha=True)
    elif tuple(image.size) != (width, height):
        image.scale(width, height)

    image.file_format = "PNG"
    try:
        image.colorspace_settings.name = "sRGB" if settings.atlas_map_type == "DIFFUSE" else "Non-Color"
    except TypeError:
        pass
    image.pixels.foreach_set(pixels.ravel())
    image.update()
    settings.atlas_preview_image = image
    return image

def _mask_source_image(settings, choice):
    return {
        "WEAR_GLOSS": settings.wear_image,
        "AO": settings.ao_image,
        "DIRT": settings.dirt_image,
        "MOSS": settings.moss_image,
    }.get(choice)


def _bake_mesh_ao(context, width, height):
    obj = context.active_object
    if obj is None or obj.type != "MESH":
        raise RuntimeError("A mesh must be active for AO")
    if not obj.data.uv_layers:
        raise RuntimeError("The active mesh has no UV map")

    image = bpy.data.images.new("FS25_AO_Bake", width=width, height=height, alpha=False)
    scene = context.scene
    previous_engine = scene.render.engine
    previous_mode = obj.mode
    temporary_material = None
    created_nodes = []
    previous_active_nodes = []

    try:
        if previous_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        if not obj.material_slots:
            temporary_material = bpy.data.materials.new("FS25_AO_Temporary")
            temporary_material.use_nodes = True
            obj.data.materials.append(temporary_material)

        for slot in obj.material_slots:
            material = slot.material
            if material is None:
                continue
            material.use_nodes = True
            nodes = material.node_tree.nodes
            previous_active_nodes.append((nodes, nodes.active))
            node = nodes.new("ShaderNodeTexImage")
            node.name = "FS25_AO_BAKE_TARGET"
            node.image = image
            nodes.active = node
            node.select = True
            created_nodes.append((nodes, node))

        scene.render.engine = "CYCLES"
        bpy.ops.object.bake(type="AO", margin=16, use_clear=True)
        image.update()
        return image
    except Exception:
        if image.name in bpy.data.images:
            bpy.data.images.remove(image)
        raise
    finally:
        scene.render.engine = previous_engine
        for nodes, node in created_nodes:
            try:
                nodes.remove(node)
            except ReferenceError:
                pass
        for nodes, active in previous_active_nodes:
            if active is not None and nodes.get(active.name) is active:
                nodes.active = active
        if temporary_material is not None:
            if obj.data.materials and obj.data.materials[-1] == temporary_material:
                obj.data.materials.pop(index=len(obj.data.materials) - 1)
            bpy.data.materials.remove(temporary_material)
        if previous_mode != "OBJECT":
            bpy.ops.object.mode_set(mode=previous_mode)

def _upstream_image(socket, visited=None):
    if socket is None or not socket.is_linked:
        return None
    visited = visited or set()
    for link in socket.links:
        node = link.from_node
        if node.as_pointer() in visited:
            continue
        visited.add(node.as_pointer())
        if node.type == "TEX_IMAGE" and node.image is not None:
            return node.image
        for node_input in node.inputs:
            image = _upstream_image(node_input, visited)
            if image is not None:
                return image
    return None


def _detected_material_source_images(context):
    obj = context.active_object
    material = obj.active_material if obj is not None else None
    if material is None or not material.use_nodes or material.node_tree is None:
        return None, None
    principled = next(
        (node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"),
        None,
    )
    if principled is None:
        return None, None
    diffuse = _upstream_image(principled.inputs.get("Base Color"))
    normal = _upstream_image(principled.inputs.get("Normal"))
    return diffuse, normal


def _sync_detected_specular_sources(context, settings):
    diffuse, normal = _detected_material_source_images(context)
    if settings.specular_diffuse_image is None and diffuse is not None:
        settings.specular_diffuse_image = diffuse
    if settings.specular_normal_image is None and normal is not None:
        settings.specular_normal_image = normal
    return settings.specular_diffuse_image, settings.specular_normal_image

def _active_principled_inputs(context):
    obj = context.active_object
    material = obj.active_material if obj is not None else None
    if material is None or not material.use_nodes or material.node_tree is None:
        return material, None, None
    principled = next(
        (node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"),
        None,
    )
    if principled is None:
        return material, None, None
    return material, principled.inputs.get("Metallic"), principled.inputs.get("Roughness")


def _resolved_specular_values(context, settings):
    material, metallic_input, roughness_input = _active_principled_inputs(context)
    if settings.specular_use_material_values and metallic_input and roughness_input:
        return float(metallic_input.default_value), float(roughness_input.default_value), material
    return settings.specular_metallic, settings.specular_roughness, None


def _detected_specular_ao_image(settings):
    if settings.specular_ao_image is not None:
        return settings.specular_ao_image
    for image in bpy.data.images:
        name = Path(image.name).stem.casefold()
        if name.endswith("_ao") or "ambientocclusion" in name or "ambient_occlusion" in name:
            return image
    return None

def _mask_export_filename(settings, context) -> str:
    obj = context.active_object
    fallback = obj.name if obj is not None else "texture"
    base_name = fallback if settings.export_auto_name else settings.export_base_name.strip()
    base_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base_name).strip(" .") or "texture"
    suffixes = {
        "MMASK": "_mMask.png",
        "VMASK": "_vMask.png",
        "CUSTOM": "_specular.png",
    }
    return f"{base_name}{suffixes[settings.mask_profile]}"


class FS25BAKE_OT_bake_texture(Operator):
    bl_idname = "fs25_bake.bake_texture"
    bl_label = "Bake Texture"
    bl_description = "Bake selected textures and material values to the target texture"

    def execute(self, context):
        settings = context.scene.fs25_bake_settings
        progress = context.window_manager
        progress.progress_begin(0, 100)
        try:
            progress.progress_update(5)
            self.report({"INFO"}, "Preparing bake...")
            if settings.mask_profile == "CUSTOM":
                _sync_detected_specular_sources(context, settings)
                if settings.specular_diffuse_image is None:
                    raise RuntimeError("Please select a Diffuse texture first")
                if settings.specular_normal_image is None:
                    raise RuntimeError("Please select a Normal texture first")

                width, height = _output_size(settings, settings.specular_diffuse_image)
                progress.progress_update(20)
                if settings.specular_ao_mode == "TEXTURE":
                    ao_image = settings.specular_ao_image
                    if ao_image is None:
                        raise RuntimeError("Please select an AO texture")
                else:
                    ao_image = _detected_specular_ao_image(settings)
                    if ao_image is None:
                        progress.progress_update(35)
                        self.report({"INFO"}, "Baking ambient occlusion...")
                        ao_image = _bake_mesh_ao(context, width, height)

                progress.progress_update(70)
                self.report({"INFO"}, "Packing Specular channels...")
                metallic, roughness, _material = _resolved_specular_values(context, settings)
                ao = _image_luminance(ao_image, width, height)
                coverage = (ao > np.float32(0.5 / 255.0)).astype(np.float32)
                settings.specular_preview_image = _write_packed_image(
                    settings.specular_preview_image,
                    "FS25_Specular_RGB",
                    width,
                    height,
                    np.float32(1.0 - roughness) * coverage,
                    ao * coverage,
                    np.float32(metallic) * coverage,
                )
                progress.progress_update(100)
                self.report({"INFO"}, f"Specular RGB created: {width} x {height}")
                return {"FINISHED"}

            progress.progress_update(20)
            choices = (settings.channel_r, settings.channel_g, settings.channel_b)
            images = [_mask_source_image(settings, choice) for choice in choices]
            missing = [choice for choice, image in zip(choices, images) if choice != "NONE" and image is None]
            if missing:
                raise RuntimeError("Missing channel texture: " + ", ".join(missing))
            preferred = next((image for image in images if image is not None), None)
            if preferred is None:
                raise RuntimeError("Select at least one channel texture")
            width, height = _output_size(settings, preferred)

            packed_channels = []
            progress.progress_update(40)
            self.report({"INFO"}, "Reading mask channels...")
            for choice, image in zip(choices, images):
                if choice == "NONE":
                    packed_channels.append(np.zeros((height, width), dtype=np.float32))
                elif choice == "CUSTOM":
                    raise RuntimeError("Custom channel does not have a texture source yet")
                else:
                    packed_channels.append(_image_luminance(image, width, height))

            alpha = None
            if settings.channel_a == "NONE":
                alpha = np.ones((height, width), dtype=np.float32)
            else:
                alpha_image = _mask_source_image(settings, settings.channel_a)
                if alpha_image is None:
                    raise RuntimeError("Missing Alpha channel texture")
                alpha = _image_luminance(alpha_image, width, height)

            label = "vMask" if settings.mask_profile == "VMASK" else "mMask"
            progress.progress_update(75)
            self.report({"INFO"}, f"Packing {label} channels...")
            settings.packed_preview_image = _write_packed_image(
                settings.packed_preview_image,
                f"FS25_{label}_RGB",
                width,
                height,
                packed_channels[0],
                packed_channels[1],
                packed_channels[2],
                alpha,
            )
            settings.preview_combined = True
            progress.progress_update(100)
            self.report({"INFO"}, f"{label} RGB created: {width} x {height}")
            return {"FINISHED"}
        except (RuntimeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        finally:
            progress.progress_end()

class FS25BAKE_OT_confirm_export_name(Operator):
    bl_idname = "fs25_bake.confirm_export_name"
    bl_label = "Confirm Name"
    bl_description = "Confirm the entered filename for export"

    @classmethod
    def poll(cls, context):
        settings = context.scene.fs25_bake_settings
        return not settings.export_auto_name and bool(settings.export_base_name.strip())

    def execute(self, context):
        settings = context.scene.fs25_bake_settings
        cleaned = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]',
            "_",
            settings.export_base_name.strip(),
        ).strip(" .")
        if not cleaned:
            self.report({"ERROR"}, "Please enter a valid name")
            return {"CANCELLED"}
        settings.export_base_name = cleaned
        settings.export_name_confirmed = True
        self.report({"INFO"}, f"Name confirmed: {_mask_export_filename(settings, context)}")
        return {"FINISHED"}


class FS25BAKE_OT_export_mask(Operator):
    bl_idname = "fs25_bake.export_mask"
    bl_label = "Overwrite File?"
    bl_description = "Export texture as PNG; overwrite existing files only after confirmation"

    overwrite_confirmed: BoolProperty(
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )

    def invoke(self, context, event):
        settings = context.scene.fs25_bake_settings
        directory_text = _effective_export_directory(settings)
        if directory_text:
            target = Path(directory_text) / _mask_export_filename(settings, context)
            if target.exists():
                self.overwrite_confirmed = True
                return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        settings = context.scene.fs25_bake_settings
        if not settings.export_auto_name and not settings.export_name_confirmed:
            self.report({"ERROR"}, "Confirm the filename with the checkmark first")
            return {"CANCELLED"}

        image = settings.specular_preview_image if settings.mask_profile == "CUSTOM" else settings.packed_preview_image
        if image is None:
            self.report({"ERROR"}, "No Combined RGB image available yet")
            return {"CANCELLED"}

        directory_text = _effective_export_directory(settings)
        if not directory_text:
            self.report({"ERROR"}, "No export folder selected")
            return {"CANCELLED"}

        directory = Path(directory_text)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.report({"ERROR"}, f"Export folder could not be created: {error}")
            return {"CANCELLED"}

        target = directory / _mask_export_filename(settings, context)
        existed_before = target.exists()
        if existed_before and not self.overwrite_confirmed:
            self.report({"ERROR"}, f"Overwrite not confirmed: {target.name}")
            return {"CANCELLED"}

        image_settings = context.scene.render.image_settings
        previous_format = image_settings.file_format
        try:
            image_settings.file_format = "PNG"
            image.save_render(str(target), scene=context.scene)
        except (OSError, RuntimeError) as error:
            self.report({"ERROR"}, f"Export failed: {error}")
            return {"CANCELLED"}
        finally:
            image_settings.file_format = previous_format

        message = "Texture overwritten" if existed_before else "New texture saved"
        self.report({"INFO"}, f"{message}: {target.name}")
        return {"FINISHED"}



class FS25BAKE_OT_create_atlas(Operator):
    bl_idname = "fs25_bake.create_atlas"
    bl_label = "Create Texture Atlas"
    bl_description = "Stack four equally sized textures vertically into one PNG atlas"

    def execute(self, context):
        settings = context.scene.fs25_bake_settings
        progress = context.window_manager
        progress.progress_begin(0, 100)
        try:
            progress.progress_update(10)
            self.report({"INFO"}, "Validating atlas layers...")
            valid, message, _width, _height, _depth = _atlas_validation(settings)
            if not valid:
                raise RuntimeError(message)
            progress.progress_update(35)
            self.report({"INFO"}, "Stacking atlas layers...")
            image = _build_vertical_atlas(settings)
            progress.progress_update(100)
            self.report({"INFO"}, f"Texture atlas created: {image.size[0]} x {image.size[1]}")
            return {"FINISHED"}
        except (RuntimeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        finally:
            progress.progress_end()


class FS25BAKE_OT_export_atlas(Operator, ExportHelper):
    bl_idname = "fs25_bake.export_atlas"
    bl_label = "Export Texture Atlas"
    bl_description = "Export the generated vertical texture atlas as PNG"
    filename_ext = ".png"
    filter_glob: StringProperty(default="*.png", options={"HIDDEN"})
    check_existing: BoolProperty(default=True, options={"HIDDEN"})

    def invoke(self, context, event):
        settings = context.scene.fs25_bake_settings
        suffix = settings.atlas_map_type.casefold()
        if not self.filepath:
            self.filepath = f"textureAtlas_{suffix}.png"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        image = context.scene.fs25_bake_settings.atlas_preview_image
        if image is None:
            self.report({"ERROR"}, "Create the texture atlas first")
            return {"CANCELLED"}
        target = Path(bpy.path.abspath(self.filepath))
        image_settings = context.scene.render.image_settings
        previous_format = image_settings.file_format
        try:
            image_settings.file_format = "PNG"
            image.save_render(str(target), scene=context.scene)
        except (OSError, RuntimeError) as error:
            self.report({"ERROR"}, f"Atlas export failed: {error}")
            return {"CANCELLED"}
        finally:
            image_settings.file_format = previous_format
        self.report({"INFO"}, f"Texture atlas saved: {target.name}")
        return {"FINISHED"}

class FS25BAKE_OT_save_preview(Operator):
    bl_idname = "fs25_bake.save_preview"
    bl_label = "Save"
    bl_description = "Save the fully combined mask"

    def execute(self, context):
        self.report({"INFO"}, "Saving will be added with channel packing")
        return {"FINISHED"}



def _draw_atlas_content(layout, settings):
    atlas_type = layout.column(align=False)
    atlas_type.prop(settings, "atlas_map_type", text="Atlas Type")

    _draw_centered_title(layout, "SOURCE TEXTURES", "TEXTURE")
    layers = layout.column(align=False)
    for index in range(1, 5):
        prop_name = f"atlas_layer_{index}"
        row = layers.row()
        row.label(text=f"Texture {index}")
        layers.template_ID(settings, prop_name, new="image.new", open="image.open")

    valid, message, width, height, _depth = _atlas_validation(settings)
    status = layout.box()
    status.label(text=message, icon="CHECKMARK" if valid else "ERROR")
    if valid:
        status.label(text=f"Output: {width} x {height}", icon="IMAGE_DATA")

    create = layout.row()
    create.scale_y = 1.25
    create.enabled = valid
    create.operator("fs25_bake.create_atlas", text="CREATE TEXTURE ATLAS", icon="RENDER_STILL")

    if settings.atlas_preview_image is not None:
        _draw_centered_title(layout, "ATLAS PREVIEW", "IMAGE_DATA")
        preview = layout.column(align=False)
        preview.template_preview(settings.atlas_preview_image, show_buttons=False)
        export = layout.row()
        export.scale_y = 1.2
        export.operator("fs25_bake.export_atlas", text="EXPORT ATLAS PNG", icon="EXPORT")

class FS25BAKE_PT_main(Panel):
    bl_label = "FS25 Texture-Bake-Machine"
    bl_idname = "FS25BAKE_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FS25 Bake"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.fs25_bake_settings
        prefs = _addon_preferences(context)

        source = layout.box()
        if prefs and prefs.shader_count:
            source.label(text="FS25 Shader Source: Ready", icon="CHECKMARK")
        else:
            source.label(text="FS25 Shader Source: Not Checked", icon="ERROR")
            source.operator("fs25_bake.scan_shader_folder", icon="VIEWZOOM")

        _draw_centered_title(layout, "Tool Profile")
        profile = layout.column(align=False)
        mask_row = profile.row(align=True)
        mask_row.enabled = (
            not settings.mask_enabled
            or settings.mask_profile in {"CUSTOM", "ATLAS"}
        )
        mask_row.label(text="Profile:")
        mask_row.prop(settings, "mask_profile", text="")

        if settings.mask_profile == "ATLAS":
            _draw_atlas_content(layout, settings)
            return

        if settings.mask_profile == "CUSTOM":
            self.draw_uv_status(context, layout)
            self.draw_mmask(context, layout, settings)
            self.draw_export(context, layout, settings)
            return

        _draw_shader_activation(profile, settings)
        if not settings.mask_enabled:
            layout.label(text="Shader is disabled", icon="INFO")
            return

        self.draw_uv_status(context, layout)
        self.draw_mmask(context, layout, settings)
        if settings.mask_profile != "CUSTOM":
            self.draw_mask_preview(context, layout, settings)
        self.draw_export(context, layout, settings)

    @staticmethod
    def draw_mmask(context, layout, settings):
        box = layout.box()
        profile_labels = {
            "MMASK": ("mMask", "LIGHT"),
            "VMASK": ("vMask", "TEXTURE"),
            "CUSTOM": ("SPECULAR", "MATERIAL"),
        }
        label, icon = profile_labels[settings.mask_profile]
        if not _draw_collapsible_header(box, settings, "show_mmask", label, icon):
            return

        body = layout.column(align=False)
        body.separator(factor=0.5)
        if settings.mask_profile == "MMASK":
            body.prop(settings, "mmask_shader")
        elif settings.mask_profile == "VMASK":
            body.prop(settings, "vmask_shader")
        else:
            FS25BAKE_PT_main.draw_specular(context, body, settings)
            return
        body.separator()

        for title, color_name, channel_prop, image_prop in (
            ("Wear / Gloss", "Red", "channel_r", "wear_image"),
            ("Ambient Occlusion | AO", "Green", "channel_g", "ao_image"),
            ("Dirt", "Blue", "channel_b", "dirt_image"),
        ):
            _draw_mask_channel(body, settings, title, color_name, channel_prop, image_prop)

        _draw_centered_title(body, "Additional Channels")
        advanced = body.column(align=False)
        alpha_row = advanced.row(align=True)
        alpha_row.label(text="Alpha:")
        alpha_row.prop(settings, "channel_a", text="")
        advanced.template_ID(settings, "moss_image", new="image.new", open="image.open")

        body.separator()
        bake_row = body.row()
        bake_row.scale_y = 1.35
        bake_label = "BAKE vMask" if settings.mask_profile == "VMASK" else "BAKE mMask"
        bake_row.operator("fs25_bake.bake_texture", text=bake_label, icon="RENDER_STILL")

    @staticmethod
    def draw_specular(context, layout, settings):
        detected_diffuse, detected_normal = _detected_material_source_images(context)
        diffuse_source = settings.specular_diffuse_image or detected_diffuse
        normal_source = settings.specular_normal_image or detected_normal

        _draw_centered_title(layout, "QUELLTEXTUREN", "TEXTURE")
        sources = layout.column(align=False)
        diffuse_row = sources.split(factor=0.22, align=True)
        diffuse_row.label(text="Diffuse")
        diffuse_select = diffuse_row.row(align=True)
        if settings.specular_diffuse_image is not None or detected_diffuse is None:
            diffuse_select.template_ID(
                settings, "specular_diffuse_image", new="image.new", open="image.open"
            )
        else:
            diffuse_select.label(text=detected_diffuse.name, icon="IMAGE_DATA")

        normal_row = sources.split(factor=0.22, align=True)
        normal_row.label(text="Normal")
        normal_select = normal_row.row(align=True)
        if settings.specular_normal_image is not None or detected_normal is None:
            normal_select.template_ID(
                settings, "specular_normal_image", new="image.new", open="image.open"
            )
        else:
            normal_select.label(text=detected_normal.name, icon="IMAGE_DATA")

        if detected_diffuse is not None or detected_normal is not None:
            detected_status = sources.box()
            detected_status.label(text="Material textures detected automatically", icon="CHECKMARK")

        _draw_centered_title(layout, "AMBIENT OCCLUSION | AO", "SHADING_RENDERED")
        ao_box = layout.column(align=False)
        ao_box.prop(settings, "specular_ao_mode", text="Source")
        detected_ao = None
        if settings.specular_ao_mode == "TEXTURE":
            ao_box.template_ID(
                settings, "specular_ao_image", new="image.new", open="image.open"
            )
            detected_ao = settings.specular_ao_image
        else:
            detected_ao = _detected_specular_ao_image(settings)

        ao_status = ao_box.box()
        if detected_ao is not None:
            ao_status.label(text=f"AO texture detected: {detected_ao.name}", icon="CHECKMARK")
        elif settings.specular_ao_mode == "AUTO":
            ao_status.label(text="No AO texture detected", icon="INFO")
            ao_status.label(text="AO will be baked from the mesh.")
        else:
            ao_status.label(text="Please select an AO texture", icon="INFO")

        _draw_centered_title(layout, "SPECULAR-WERTE", "MATERIAL")
        values = layout.column(align=False)
        values.prop(settings, "specular_use_material_values")
        material, metallic_input, roughness_input = _active_principled_inputs(context)
        has_principled_values = metallic_input is not None and roughness_input is not None

        if settings.specular_use_material_values and has_principled_values:
            values.prop(metallic_input, "default_value", text="Metallic", slider=True)
            values.prop(roughness_input, "default_value", text="Roughness", slider=True)
            values.label(text=f"Directly linked: {material.name}", icon="CHECKMARK")
        else:
            values.prop(settings, "specular_metallic", slider=True)
            values.prop(settings, "specular_roughness", slider=True)
            if settings.specular_use_material_values:
                values.label(text="No Principled BSDF detected", icon="INFO")

        metallic, roughness, _material = _resolved_specular_values(context, settings)
        channels = values.box()
        channels.label(text=f"Red · Gloss: {1.0 - roughness:.3f}")
        channels.label(text="Green · Ambient Occlusion")
        channels.label(text=f"Blue · Metallic: {metallic:.3f}")
        channels.label(text="Alpha · Not Used", icon="X")

        _draw_centered_title(layout, "SPECULAR-VORSCHAU", "IMAGE_DATA")
        preview = layout.column(align=False)
        if settings.specular_preview_image is not None:
            preview.template_preview(settings.specular_preview_image, show_buttons=False)
        else:
            preview.separator(factor=1.5)
            preview.label(text="Specular RGB not created yet", icon="IMAGE_DATA")
            preview.separator(factor=1.5)

        bake_row = layout.row()
        bake_row.scale_y = 1.35
        bake_ready = (
            diffuse_source is not None
            and normal_source is not None
            and (
                settings.specular_ao_mode == "AUTO"
                or settings.specular_ao_image is not None
            )
        )
        bake_row.enabled = bake_ready
        bake_row.operator(
            "fs25_bake.bake_texture", text="BAKE SPECULAR", icon="RENDER_STILL"
        )


    @staticmethod
    def draw_mask_preview(context, layout, settings):
        box = layout.box()
        preview_label = "vMask Preview" if settings.mask_profile == "VMASK" else "mMask Preview"
        if settings.mask_profile == "CUSTOM":
            preview_label = "Mask Preview"
        if not _draw_collapsible_header(box, settings, "show_preview", preview_label, "IMAGE"):
            return

        body = layout.column(align=False)
        info = _active_mesh_uv_info(context)

        bake_settings = body.column(align=False)
        bake_uv = bake_settings.row(align=True)
        bake_uv.label(text="Bake UV")
        detected_target = info["detected_target"] if info else None
        if detected_target:
            detected = bake_uv.row(align=True)
            detected.enabled = False
            detected.label(text=detected_target, icon="LOCKED")
        else:
            bake_uv.prop(settings, "bake_uv_mode", text="")

        resolution = bake_settings.row(align=True)
        resolution.label(text="Resolution")
        resolution.prop(settings, "output_resolution", text="")

        preview = body.column(align=False)
        combined = preview.row(align=True)
        combined.prop(settings, "preview_combined", text="", toggle=False)
        combined.label(text="Combined RGB")

        channels = preview.row(align=True)
        channels.enabled = not settings.preview_combined
        channels.prop(settings, "preview_wear", text="Wear R")
        channels.prop(settings, "preview_ao", text="AO G")
        channels.prop(settings, "preview_dirt", text="Dirt B")

        display = preview.box()
        preview_image = None
        if settings.preview_combined:
            preview_image = settings.packed_preview_image
        else:
            selected = []
            if settings.preview_wear and settings.wear_image:
                selected.append(settings.wear_image)
            if settings.preview_ao and settings.ao_image:
                selected.append(settings.ao_image)
            if settings.preview_dirt and settings.dirt_image:
                selected.append(settings.dirt_image)
            if len(selected) == 1:
                preview_image = selected[0]
            elif len(selected) > 1:
                preview_image = settings.packed_preview_image

        if preview_image is not None:
            display.template_preview(preview_image, show_buttons=False)
        else:
            display.separator(factor=2.0)
            message = (
                "Combined RGB not created yet"
                if settings.preview_combined
                else "No texture loaded for this selection"
            )
            row = display.row()
            row.alignment = "CENTER"
            row.label(text=message, icon="IMAGE_DATA")
            display.separator(factor=2.0)

        actions = body.row(align=True)
        actions.operator("fs25_bake.invert_preview_channel", icon="ARROW_LEFTRIGHT")
        actions.operator("fs25_bake.save_preview", icon="FILE_TICK")

    @staticmethod
    def draw_uv_status(context, layout):
        _draw_centered_title(layout, "UV | STATUSINFO", "GROUP_UVS")
        body = layout.column(align=False)
        info = _active_mesh_uv_info(context)
        if info is None:
            body.label(text="No active mesh object", icon="ERROR")
            return

        _draw_info_row(body, "Mesh", info["mesh"])
        face_text = (
            f"Selection detected ({info['selected_faces']})"
            if info["selected_faces"]
            else "No faces selected"
        )
        _draw_info_row(body, "Faces", face_text)
        _draw_info_row(body, "UV Maps", str(info["uv_count"]))
        _draw_info_row(body, "Active UV", info["active_uv"])
        _draw_info_row(
            body,
            "Tool Node Wrangler",
            "Active" if info["node_wrangler"] else "Not enabled",
        )
        _draw_info_row(
            body,
            "Node-Mapping",
            "Active" if info["mapping_node"] is not None else "Not detected",
        )

        scale = info["mapping_scale"]
        scale_text = " / ".join(f"{value:.3f}" for value in scale) if scale else "—"
        _draw_info_row(body, "Mapping Scale", scale_text)

        status_text, status_icon = info["status"]
        status_box = body.box()
        status_box.label(text=status_text, icon=status_icon)
    @staticmethod
    def draw_export(context, layout, settings):
        box = layout.box()
        if not _draw_collapsible_header(box, settings, "show_export", "EXPORT", "EXPORT"):
            return

        body = layout.column(align=False)
        filename = _mask_export_filename(settings, context)

        auto_row = body.split(factor=0.36, align=True)
        auto_row.label(text="Automatic Name")
        auto_row.prop(settings, "export_auto_name", text="")

        name_row = body.split(factor=0.36, align=True)
        name_row.label(text="Filename")
        name_controls = name_row.row(align=True)
        name_controls.enabled = not settings.export_auto_name
        name_controls.prop(settings, "export_base_name", text="")
        confirm = name_controls.row(align=True)
        confirm.enabled = bool(settings.export_base_name.strip())
        confirm.operator("fs25_bake.confirm_export_name", text="", icon="CHECKMARK")

        name_status = body.box()
        if settings.export_auto_name:
            name_status.label(text=f"Automatically detected: {filename}", icon="OBJECT_DATA")
        elif settings.export_name_confirmed:
            name_status.label(text=f"Name confirmed: {filename}", icon="CHECKMARK")
        elif settings.export_base_name.strip():
            name_status.label(text="Confirm name with the checkmark", icon="INFO")
        else:
            name_status.label(text="Enter a custom name", icon="INFO")

        body.prop(settings, "export_use_blend_directory")
        folder_row = body.split(factor=0.36, align=True)
        folder_row.label(text="File Location")
        folder_field = folder_row.row(align=True)
        if settings.export_use_blend_directory:
            folder_field.enabled = False
            folder_field.prop(settings, "export_blend_directory_display", text="")
        else:
            folder_field.prop(settings, "export_directory", text="")

        effective_directory = _effective_export_directory(settings)

        if not effective_directory:
            body.label(text="Select file location ...", icon="FILE_FOLDER")

        output = body.box()
        output.label(text=f"Output: {filename}", icon="IMAGE_DATA")

        output_image = (
            settings.specular_preview_image
            if settings.mask_profile == "CUSTOM"
            else settings.packed_preview_image
        )
        name_ready = settings.export_auto_name or settings.export_name_confirmed
        export_row = body.row()
        export_row.scale_y = 1.2
        export_row.enabled = bool(output_image and effective_directory and name_ready)
        export_row.operator("fs25_bake.export_mask", text="EXPORT UV*", icon="EXPORT")

        if output_image is None:
            missing = "Specular RGB" if settings.mask_profile == "CUSTOM" else "Combined RGB"
            body.label(text=f"{missing} must be created first.", icon="INFO")
    @staticmethod
    def draw_placeholder(layout, settings, prop_name, label, icon):
        box = layout.box()
        if not _draw_collapsible_header(box, settings, prop_name, label, icon):
            return
        body = box.column()
        body.separator(factor=1.5)
        body.label(text=f"{label}: section prepared", icon="INFO")
        body.label(text="Content coming later.")
        body.separator(factor=1.5)



CLASSES = (
    FS25BakePreferences,
    FS25BAKE_OT_scan_shader_folder,
    FS25BakeSettings,
    FS25BAKE_OT_bake_texture,
    FS25BAKE_OT_confirm_export_name,
    FS25BAKE_OT_export_mask,
    FS25BAKE_OT_invert_preview_channel,
    FS25BAKE_OT_save_preview,
    FS25BAKE_OT_create_atlas,
    FS25BAKE_OT_export_atlas,
    FS25BAKE_PT_main,
)


def register():
    if TRANSLATIONS:
        bpy.app.translations.register(ADDON_ID, TRANSLATIONS)
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.fs25_bake_settings = bpy.props.PointerProperty(type=FS25BakeSettings)


def unregister():
    del bpy.types.Scene.fs25_bake_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if TRANSLATIONS:
        bpy.app.translations.unregister(ADDON_ID)


if __name__ == "__main__":
    register()
