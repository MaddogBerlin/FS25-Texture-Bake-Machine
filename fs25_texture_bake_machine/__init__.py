bl_info = {
    "name": "FS25 Texture-Bake-Machine",
    "author": "Maddog Design & Djain",
    "version": (1, 0, 1),
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
CHANNEL_LABELS = {identifier: label for identifier, label, _description in CHANNEL_ITEMS}


def _update_preview_channel(settings, _context):
    settings.preview_combined = False
    if not (settings.preview_wear or settings.preview_ao or settings.preview_dirt):
        settings.preview_ao = True
        return
    _refresh_mask_preview_safe(settings)


def _update_preview_combined(settings, _context):
    _refresh_mask_preview_safe(settings)


def _update_mask_source(settings, _context):
    _refresh_mask_preview_safe(settings)


def _update_ao_image(settings, _context):
    """Reveal AO bake controls as soon as an AO image is assigned."""
    if settings.ao_image is not None:
        settings.show_ao_bake_settings = True
    _refresh_mask_preview_safe(settings)


def _refresh_mask_preview_safe(settings):
    """Refresh previews from RNA callbacks without breaking Blender UI updates."""
    try:
        _refresh_mask_preview(settings)
        for screen in bpy.data.screens:
            for area in screen.areas:
                area.tag_redraw()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass


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
    channel_r: EnumProperty(name="R", items=CHANNEL_ITEMS, default="WEAR_GLOSS", update=_update_mask_source)
    channel_g: EnumProperty(name="G", items=CHANNEL_ITEMS, default="AO", update=_update_mask_source)
    channel_b: EnumProperty(name="B", items=CHANNEL_ITEMS, default="DIRT", update=_update_mask_source)
    channel_a: EnumProperty(name="A", items=CHANNEL_ITEMS, default="NONE", update=_update_mask_source)
    wear_image: PointerProperty(name="Wear / Gloss", type=bpy.types.Image, update=_update_mask_source)
    ao_image: PointerProperty(name="AO", type=bpy.types.Image, update=_update_ao_image)
    dirt_image: PointerProperty(name="Dirt", type=bpy.types.Image, update=_update_mask_source)
    wear_material: PointerProperty(name="Wear Material", type=bpy.types.Material)
    ao_material: PointerProperty(name="AO Material", type=bpy.types.Material)
    dirt_material: PointerProperty(name="Dirt Material", type=bpy.types.Material)
    moss_image: PointerProperty(name="Moss", type=bpy.types.Image, update=_update_mask_source)
    packed_preview_image: PointerProperty(name="Combined RGB", type=bpy.types.Image)
    mask_preview_display_image: PointerProperty(name="Mask Preview", type=bpy.types.Image)
    specular_diffuse_image: PointerProperty(name="Diffuse", type=bpy.types.Image)
    specular_normal_image: PointerProperty(name="Normal", type=bpy.types.Image)
    baked_diffuse_image: PointerProperty(name="Baked Diffuse", type=bpy.types.Image)
    baked_normal_image: PointerProperty(name="Baked Normal", type=bpy.types.Image)
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
    preview_combined: BoolProperty(name="Combined RGB", default=True, update=_update_preview_combined)
    preview_wear: BoolProperty(name="Wear R", default=False, update=_update_preview_channel)
    preview_ao: BoolProperty(name="AO G", default=True, update=_update_preview_channel)
    preview_dirt: BoolProperty(name="Dirt B", default=False, update=_update_preview_channel)
    invert_red_preview: BoolProperty(default=False, options={"HIDDEN"})
    invert_green_preview: BoolProperty(default=False, options={"HIDDEN"})
    invert_blue_preview: BoolProperty(default=False, options={"HIDDEN"})
    show_mmask: BoolProperty(name="mMask", default=False)
    show_preview: BoolProperty(name="Mask Preview", default=False, update=_update_mask_source)
    show_vmask: BoolProperty(name="vMask", default=False)
    show_custom: BoolProperty(name="Custom Mask", default=False)
    show_export: BoolProperty(name="Export", default=False)
    show_material_bakes: BoolProperty(name="Material Bakes", default=False)
    show_ao_bake_settings: BoolProperty(name="AO Bake Settings", default=False)
    ao_bake_type_display: EnumProperty(
        name="Bake Type",
        items=(("AO", "Ambient Occlusion", "Native Cycles ambient occlusion bake"),),
        default="AO",
    )
    ao_bake_target_display: EnumProperty(
        name="Target",
        items=(("IMAGE_TEXTURES", "Image Textures", "Bake into the assigned AO image"),),
        default="IMAGE_TEXTURES",
    )
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


def _draw_labeled_prop(layout, data, prop_name: str, label: str):
    """Keep labeled controls aligned at one consistent middle width."""
    split = layout.split(factor=0.36, align=True)
    split.label(text=label)
    split.prop(data, prop_name, text="")


def _draw_mask_channel(context, layout, settings, title, color_name, channel_prop, image_prop):
    """Draw one compact RGB(A) assignment and its native Blender image field."""
    _draw_centered_title(layout, title)
    _draw_labeled_prop(layout, settings, channel_prop, f"{color_name}:")
    new_operators = {
        "wear_image": "fs25_bake.new_black_wear",
        "ao_image": "fs25_bake.new_black_ao",
        "dirt_image": "fs25_bake.new_black_dirt",
    }
    layout.template_ID(settings, image_prop, new=new_operators[image_prop], open="image.open")
    channel_image = getattr(settings, image_prop)
    material_props = {
        "wear_image": "wear_material",
        "ao_image": "ao_material",
        "dirt_image": "dirt_material",
    }
    channel_material = getattr(settings, material_props[image_prop])
    if channel_material is not None:
        material_display = layout.row(align=True)
        material_display.prop(channel_material, "name", text="", icon="MATERIAL")
    if channel_image is not None:
        width, height = (int(channel_image.size[0]), int(channel_image.size[1]))
        depth = int(channel_image.depth)
        _draw_info_row(
            layout,
            "Texture Size",
            f"{width} × {height} px · {depth} Bit",
        )

    if image_prop == "ao_image":
        bake_panel = layout.column(align=True)
        header = bake_panel.row(align=True)
        header.prop(
            settings,
            "show_ao_bake_settings",
            text="",
            icon="TRIA_DOWN" if settings.show_ao_bake_settings else "TRIA_RIGHT",
            emboss=False,
        )
        header.label(text="AO BAKE SETTINGS", icon="SETTINGS")
        if settings.show_ao_bake_settings:
            native_bake = context.scene.render.bake
            info = bake_panel.column(align=False)
            _draw_labeled_prop(info, settings, "ao_bake_type_display", "Bake Type:")
            _draw_labeled_prop(info, settings, "ao_bake_target_display", "Target:")
            info.prop(native_bake, "use_clear", text="Clear Image")
            if hasattr(native_bake, "margin_type"):
                _draw_labeled_prop(info, native_bake, "margin_type", "Margin Type:")
            _draw_labeled_prop(info, native_bake, "margin", "Margin Size")

    actions = layout.row(align=True)
    if image_prop == "ao_image":
        bake = actions.row(align=True)
        obj = context.active_object
        bake.enabled = bool(
            context.scene.render.engine == "CYCLES"
            and obj is not None
            and obj.type == "MESH"
            and obj.data.uv_layers
        )
        bake.operator("fs25_bake.bake_ao_channel", text="BAKE AO", icon="RENDER_STILL")
    save = actions.row(align=True)
    save.enabled = channel_image is not None
    save_label = "SAVE IMAGE*" if channel_image is not None and channel_image.is_dirty else "SAVE IMAGE"
    operator = save.operator("fs25_bake.save_channel_image", text=save_label, icon="FILE_TICK")
    operator.image_prop = image_prop

    if image_prop == "ao_image" and context.scene.render.engine != "CYCLES":
        warning = layout.box()
        warning.alert = True
        warning.label(text="Cycles must be active for native AO baking.", icon="ERROR")


def _draw_material_map_bake(context, layout, settings, label, map_type, image_prop):
    """Draw one native material-map bake result with bake and save actions."""
    _draw_centered_title(layout, f"{label} Bake")
    layout.template_ID(settings, image_prop, open="image.open")
    image = getattr(settings, image_prop)
    if image is not None:
        width, height = int(image.size[0]), int(image.size[1])
        _draw_info_row(layout, "Texture Size", f"{width} × {height} px · {int(image.depth)} Bit")

    actions = layout.row(align=True)
    obj = context.active_object
    can_bake = bool(
        context.scene.render.engine == "CYCLES"
        and obj is not None
        and obj.type == "MESH"
        and obj.data.uv_layers
        and obj.material_slots
    )
    bake = actions.row(align=True)
    bake.enabled = can_bake
    operator = bake.operator(
        "fs25_bake.bake_material_map",
        text=f"BAKE {label.upper()}",
        icon="RENDER_STILL",
    )
    operator.map_type = map_type

    save = actions.row(align=True)
    save.enabled = image is not None
    save_label = "SAVE IMAGE*" if image is not None and image.is_dirty else "SAVE IMAGE"
    operator = save.operator("fs25_bake.save_channel_image", text=save_label, icon="FILE_TICK")
    operator.image_prop = image_prop


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


def _assign_channel_image_node(context, image, image_prop: str):
    """Create a dedicated channel material and connect its image to Base Color."""
    obj = context.active_object
    if obj is None or obj.type != "MESH":
        return None

    settings = context.scene.fs25_bake_settings
    material_props = {
        "wear_image": "wear_material",
        "ao_image": "ao_material",
        "dirt_image": "dirt_material",
    }
    material_names = {
        "wear_image": "Wear-Mask",
        "ao_image": "AO-Mask",
        "dirt_image": "Dirt-Mask",
    }
    material_prop = material_props[image_prop]
    material = None
    slot_index = None
    for index, slot in enumerate(obj.material_slots):
        candidate = slot.material
        if candidate is not None and candidate.get("fs25_channel_material") == image_prop:
            material = candidate
            slot_index = index
            break

    active_material = obj.active_material
    known_mask_names = set(material_names.values())
    active_channel = (
        active_material.get("fs25_channel_material")
        if active_material is not None
        else None
    )
    if material is None and active_material is not None and (
        not active_channel and active_material.name not in known_mask_names
    ):
        material = active_material
        slot_index = obj.active_material_index
        material.name = material_names[image_prop]

    if material is None:
        material = bpy.data.materials.new(material_names[image_prop])
        material.use_nodes = True
        obj.data.materials.append(material)
        slot_index = len(obj.material_slots) - 1

    obj.active_material_index = slot_index
    selected_polygons = [polygon for polygon in obj.data.polygons if polygon.select]
    target_polygons = selected_polygons or obj.data.polygons
    for polygon in target_polygons:
        polygon.material_index = slot_index
    material["fs25_channel_material"] = image_prop
    setattr(settings, material_prop, material)
    material.use_nodes = True

    nodes = material.node_tree.nodes
    node = next(
        (
            candidate
            for candidate in nodes
            if candidate.type == "TEX_IMAGE"
            and candidate.get("fs25_channel_image_prop") == image_prop
        ),
        None,
    )
    if node is None:
        node = nodes.new("ShaderNodeTexImage")
        node["fs25_channel_image_prop"] = image_prop

    node.name = image.name
    node.label = image.name
    node.image = image

    principled = next(
        (candidate for candidate in nodes if candidate.type == "BSDF_PRINCIPLED"),
        None,
    )
    if principled is not None:
        vertical_offsets = {
            "wear_image": 220.0,
            "ao_image": 0.0,
            "dirt_image": -220.0,
        }
        node.location = (
            principled.location.x - 320.0,
            principled.location.y + vertical_offsets.get(image_prop, 0.0),
        )
        base_color = principled.inputs.get("Base Color")
        color_output = node.outputs.get("Color")
        if base_color is not None and color_output is not None:
            links = material.node_tree.links
            for link in tuple(base_color.links):
                links.remove(link)
            links.new(color_output, base_color)

    for candidate in nodes:
        candidate.select = False
    node.select = True
    nodes.active = node
    return node


def _activate_channel_image_for_painting(context, image):
    """Synchronize Blender's paint canvas and visible Image Editors."""
    image_paint = context.scene.tool_settings.image_paint
    if hasattr(image_paint, "canvas"):
        try:
            image_paint.canvas = image
        except (AttributeError, TypeError, RuntimeError):
            pass

    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "IMAGE_EDITOR":
                continue
            space = area.spaces.active
            if hasattr(space, "image"):
                space.image = image


class _FS25BAKE_OT_new_black_channel:
    image_name_input: StringProperty(name="Name", default="")
    width: IntProperty(name="Width", default=1024, min=1, max=16384)
    height: IntProperty(name="Height", default=1024, min=1, max=16384)
    image_prop = ""
    default_image_name = "FS25_Black_Channel"

    def invoke(self, context, _event):
        settings = context.scene.fs25_bake_settings
        preferred = getattr(settings, self.image_prop, None)
        width, height = _output_size(settings, preferred)
        self.image_name_input = self.default_image_name
        self.width = width
        self.height = height
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        image_name = self.image_name_input.strip() or self.default_image_name
        image = bpy.data.images.new(
            image_name,
            width=self.width,
            height=self.height,
            alpha=True,
            float_buffer=False,
        )
        image.generated_color = (0.0, 0.0, 0.0, 1.0)
        image.file_format = "PNG"
        try:
            image.colorspace_settings.name = "Non-Color"
        except TypeError:
            pass
        pixels = np.zeros(self.width * self.height * 4, dtype=np.float32)
        pixels[3::4] = 1.0
        image.pixels.foreach_set(pixels)
        image.update()
        setattr(context.scene.fs25_bake_settings, self.image_prop, image)
        _assign_channel_image_node(context, image, self.image_prop)
        _activate_channel_image_for_painting(context, image)
        self.report(
            {"INFO"},
            f"Black channel created: {image.name} · {self.width} x {self.height}",
        )
        return {"FINISHED"}


class FS25BAKE_OT_new_black_wear(_FS25BAKE_OT_new_black_channel, Operator):
    bl_idname = "fs25_bake.new_black_wear"
    bl_label = "New Black Wear / Gloss"
    bl_description = "Create a black Wear / Gloss painting image"
    image_prop = "wear_image"
    default_image_name = "FS25_Wear_Gloss"


class FS25BAKE_OT_new_black_ao(_FS25BAKE_OT_new_black_channel, Operator):
    bl_idname = "fs25_bake.new_black_ao"
    bl_label = "New Black AO"
    bl_description = "Create a black ambient occlusion image"
    image_prop = "ao_image"
    default_image_name = "FS25_AO"


class FS25BAKE_OT_new_black_dirt(_FS25BAKE_OT_new_black_channel, Operator):
    bl_idname = "fs25_bake.new_black_dirt"
    bl_label = "New Black Dirt"
    bl_description = "Create a black Dirt painting image"
    image_prop = "dirt_image"
    default_image_name = "FS25_Dirt"


class FS25BAKE_OT_bake_ao_channel(Operator):
    bl_idname = "fs25_bake.bake_ao_channel"
    bl_label = "Bake AO"
    bl_description = "Bake native Cycles ambient occlusion into the AO channel"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(
            context.scene.render.engine == "CYCLES"
            and obj is not None
            and obj.type == "MESH"
            and obj.data.uv_layers
        )

    def execute(self, context):
        settings = context.scene.fs25_bake_settings
        width, height = _output_size(settings, settings.ao_image)
        try:
            image = _bake_mesh_ao(context, width, height)
            settings.ao_image = image
            _assign_channel_image_node(context, image, "ao_image")
            _activate_channel_image_for_painting(context, image)
        except RuntimeError as error:
            self.report({"ERROR"}, f"AO bake failed: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"AO baked: {width} x {height}")
        return {"FINISHED"}


class FS25BAKE_OT_bake_material_map(Operator):
    bl_idname = "fs25_bake.bake_material_map"
    bl_label = "Bake Material Map"
    bl_description = "Bake Diffuse or tangent-space Normal through the active mesh UV"

    map_type: EnumProperty(
        name="Map Type",
        items=(
            ("DIFFUSE", "Diffuse", "Bake the material color without lighting"),
            ("NORMAL", "Normal", "Bake a tangent-space normal map"),
        ),
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(
            context.scene.render.engine == "CYCLES"
            and obj is not None
            and obj.type == "MESH"
            and obj.data.uv_layers
            and obj.material_slots
        )

    def execute(self, context):
        settings = context.scene.fs25_bake_settings
        source = (
            settings.specular_normal_image
            if self.map_type == "NORMAL"
            else settings.specular_diffuse_image
        )
        if source is None:
            detected_diffuse, detected_normal = _detected_material_source_images(context)
            source = detected_normal if self.map_type == "NORMAL" else detected_diffuse
        width, height = _output_size(settings, source)
        try:
            image = _bake_mesh_material_map(context, width, height, self.map_type)
        except RuntimeError as error:
            self.report({"ERROR"}, f"{self.map_type.title()} bake failed: {error}")
            return {"CANCELLED"}

        target_prop = "baked_normal_image" if self.map_type == "NORMAL" else "baked_diffuse_image"
        setattr(settings, target_prop, image)
        _activate_channel_image_for_painting(context, image)
        self.report({"INFO"}, f"{self.map_type.title()} baked: {width} x {height}")
        return {"FINISHED"}


class FS25BAKE_OT_save_channel_image(Operator, ExportHelper):
    bl_idname = "fs25_bake.save_channel_image"
    bl_label = "Save Channel PNG"
    bl_description = "Save this channel image as PNG"

    filename_ext = ".png"
    filter_glob: StringProperty(default="*.png", options={"HIDDEN"})
    image_prop: StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        image = getattr(context.scene.fs25_bake_settings, self.image_prop, None)
        if image is None:
            self.report({"ERROR"}, "No channel image selected")
            return {"CANCELLED"}
        safe_name = re.sub(r'[^A-Za-z0-9_.-]+', "_", Path(image.name).stem) or "FS25_channel"
        self.filepath = str(Path(_blend_directory_display(context.scene.fs25_bake_settings)) / f"{safe_name}.png")
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        image = getattr(context.scene.fs25_bake_settings, self.image_prop, None)
        if image is None:
            self.report({"ERROR"}, "No channel image selected")
            return {"CANCELLED"}
        target = Path(bpy.path.abspath(self.filepath))
        previous_path = image.filepath_raw
        previous_format = image.file_format
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            image.filepath_raw = str(target)
            image.file_format = "PNG"
            image.save()
        except (OSError, RuntimeError) as error:
            image.filepath_raw = previous_path
            image.file_format = previous_format
            self.report({"ERROR"}, f"Channel save failed: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Channel saved: {target.name}")
        return {"FINISHED"}



class FS25BAKE_OT_invert_preview_channel(Operator):
    bl_idname = "fs25_bake.invert_preview_channel"
    bl_label = "Invert Channel"
    bl_description = "Invert the currently selected preview channel"

    @classmethod
    def poll(cls, context):
        settings = context.scene.fs25_bake_settings
        active = sum((settings.preview_wear, settings.preview_ao, settings.preview_dirt))
        if settings.preview_combined or active != 1:
            return False
        selected_choice = next(
            choice
            for enabled, choice in (
                (settings.preview_wear, settings.channel_r),
                (settings.preview_ao, settings.channel_g),
                (settings.preview_dirt, settings.channel_b),
            )
            if enabled
        )
        return selected_choice not in {"NONE", "CUSTOM"} and _mask_source_image(settings, selected_choice) is not None

    def execute(self, context):
        settings = context.scene.fs25_bake_settings
        selections = (
            (settings.preview_wear, "invert_red_preview", "Red"),
            (settings.preview_ao, "invert_green_preview", "Green"),
            (settings.preview_dirt, "invert_blue_preview", "Blue"),
        )
        selected = next((item for item in selections if item[0]), None)
        if selected is None:
            return {"CANCELLED"}
        _active, prop_name, label = selected
        setattr(settings, prop_name, not getattr(settings, prop_name))
        _refresh_mask_preview_safe(settings)
        state = "on" if getattr(settings, prop_name) else "off"
        self.report({"INFO"}, f"{label} preview inversion: {state}")
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
    try:
        image.preview_ensure().reload()
    except (AttributeError, RuntimeError):
        pass
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


def _mask_choice_luminance(settings, choice, width, height, invert=False):
    image = _mask_source_image(settings, choice)
    if image is None:
        return np.zeros((height, width), dtype=np.float32)
    values = _image_luminance(image, width, height)
    if invert:
        values = np.float32(1.0) - values
    return values


def _mask_preview_size(settings):
    preferred = next(
        (
            image
            for image in (
                settings.wear_image,
                settings.ao_image,
                settings.dirt_image,
                settings.moss_image,
            )
            if image is not None and image.size[0] and image.size[1]
        ),
        None,
    )
    return _output_size(settings, preferred) if preferred is not None else (0, 0)


def _refresh_mask_preview(settings):
    """Build the combined export image and the current non-destructive display preview."""
    width, height = _mask_preview_size(settings)
    if width < 1 or height < 1:
        settings.mask_preview_display_image = None
        return None

    channel_choices = (settings.channel_r, settings.channel_g, settings.channel_b)
    inversion = (
        settings.invert_red_preview,
        settings.invert_green_preview,
        settings.invert_blue_preview,
    )
    channels = tuple(
        _mask_choice_luminance(settings, choice, width, height, invert=invert)
        if choice not in {"NONE", "CUSTOM"}
        else np.zeros((height, width), dtype=np.float32)
        for choice, invert in zip(channel_choices, inversion)
    )
    alpha = (
        np.ones((height, width), dtype=np.float32)
        if settings.channel_a == "NONE"
        else _mask_choice_luminance(settings, settings.channel_a, width, height)
    )
    label = "vMask" if settings.mask_profile == "VMASK" else "mMask"
    settings.packed_preview_image = _write_packed_image(
        settings.packed_preview_image,
        f"FS25_{label}_RGB",
        width,
        height,
        channels[0],
        channels[1],
        channels[2],
        alpha,
    )

    if settings.preview_combined:
        old_display = settings.mask_preview_display_image
        settings.mask_preview_display_image = settings.packed_preview_image
        if (
            old_display is not None
            and old_display is not settings.packed_preview_image
            and old_display.name in bpy.data.images
        ):
            bpy.data.images.remove(old_display)
        return settings.mask_preview_display_image

    selected = []
    if settings.preview_wear:
        selected.append((0, channels[0]))
    if settings.preview_ao:
        selected.append((1, channels[1]))
    if settings.preview_dirt:
        selected.append((2, channels[2]))

    if len(selected) == 1:
        gray = selected[0][1]
        red = green = blue = gray
    else:
        empty = np.zeros((height, width), dtype=np.float32)
        selected_by_channel = {channel: values for channel, values in selected}
        red = selected_by_channel.get(0, empty)
        green = selected_by_channel.get(1, empty)
        blue = selected_by_channel.get(2, empty)

    old_display = settings.mask_preview_display_image
    settings.mask_preview_display_image = _write_packed_image(
        None,
        f"FS25_{label}_Preview",
        width,
        height,
        red,
        green,
        blue,
        np.ones((height, width), dtype=np.float32),
    )
    if (
        old_display is not None
        and old_display is not settings.packed_preview_image
        and old_display is not settings.mask_preview_display_image
        and old_display.name in bpy.data.images
    ):
        bpy.data.images.remove(old_display)
    return settings.mask_preview_display_image


def _bake_mesh_ao(context, width, height):
    obj = context.active_object
    if obj is None or obj.type != "MESH":
        raise RuntimeError("A mesh must be active for AO")
    if not obj.data.uv_layers:
        raise RuntimeError("The active mesh has no UV map")

    image = bpy.data.images.new("FS25_AO_Bake", width=width, height=height, alpha=True)
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
        native_bake = scene.render.bake
        bake_arguments = {
            "type": "AO",
            "margin": native_bake.margin,
            "use_clear": native_bake.use_clear,
        }
        if hasattr(native_bake, "margin_type"):
            bake_arguments["margin_type"] = native_bake.margin_type
        bpy.ops.object.bake(**bake_arguments)
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


def _bake_mesh_material_map(context, width, height, bake_type):
    """Bake the active material through the mesh UVs into a new image."""
    obj = context.active_object
    if obj is None or obj.type != "MESH":
        raise RuntimeError("A mesh must be active for baking")
    if not obj.data.uv_layers:
        raise RuntimeError("The active mesh has no UV map")
    if not obj.material_slots or not any(slot.material for slot in obj.material_slots):
        raise RuntimeError("The active mesh has no material")

    is_normal = bake_type == "NORMAL"
    image_name = "FS25_Normal_Bake" if is_normal else "FS25_Diffuse_Bake"
    image = bpy.data.images.new(image_name, width=width, height=height, alpha=True)
    image.file_format = "PNG"
    try:
        image.colorspace_settings.name = "Non-Color" if is_normal else "sRGB"
    except TypeError:
        pass

    scene = context.scene
    previous_engine = scene.render.engine
    previous_mode = obj.mode
    created_nodes = []
    previous_active_nodes = []
    native_bake = scene.render.bake
    previous_normal_space = getattr(native_bake, "normal_space", None)

    try:
        if previous_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        for slot in obj.material_slots:
            material = slot.material
            if material is None:
                continue
            material.use_nodes = True
            nodes = material.node_tree.nodes
            previous_active_nodes.append((nodes, nodes.active))
            for candidate in nodes:
                candidate.select = False
            node = nodes.new("ShaderNodeTexImage")
            node.name = f"FS25_{bake_type}_BAKE_TARGET"
            node.image = image
            node.select = True
            nodes.active = node
            created_nodes.append((nodes, node))

        scene.render.engine = "CYCLES"
        if is_normal and hasattr(native_bake, "normal_space"):
            native_bake.normal_space = "TANGENT"
        bake_arguments = {
            "type": bake_type,
            "margin": native_bake.margin,
            "use_clear": native_bake.use_clear,
        }
        if bake_type == "DIFFUSE":
            bake_arguments["pass_filter"] = {"COLOR"}
        if hasattr(native_bake, "margin_type"):
            bake_arguments["margin_type"] = native_bake.margin_type
        bpy.ops.object.bake(**bake_arguments)
        image.update()
        return image
    except Exception:
        if image.name in bpy.data.images:
            bpy.data.images.remove(image)
        raise
    finally:
        scene.render.engine = previous_engine
        if previous_normal_space is not None and hasattr(native_bake, "normal_space"):
            native_bake.normal_space = previous_normal_space
        for nodes, node in created_nodes:
            try:
                nodes.remove(node)
            except ReferenceError:
                pass
        for nodes, active in previous_active_nodes:
            if active is not None and nodes.get(active.name) is active:
                nodes.active = active
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
            inversion = (
                settings.invert_red_preview,
                settings.invert_green_preview,
                settings.invert_blue_preview,
            )
            progress.progress_update(40)
            self.report({"INFO"}, "Reading mask channels...")
            for choice, image, invert in zip(choices, images, inversion):
                if choice == "NONE":
                    packed_channels.append(np.zeros((height, width), dtype=np.float32))
                elif choice == "CUSTOM":
                    raise RuntimeError("Custom channel does not have a texture source yet")
                else:
                    packed_channels.append(
                        _mask_choice_luminance(settings, choice, width, height, invert=invert)
                    )

            alpha = None
            if settings.channel_a == "NONE":
                alpha = np.ones((height, width), dtype=np.float32)
            else:
                alpha_image = _mask_source_image(settings, settings.channel_a)
                if alpha_image is None:
                    raise RuntimeError("Missing Alpha channel texture")
                alpha = _mask_choice_luminance(settings, settings.channel_a, width, height)

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
            _refresh_mask_preview_safe(settings)
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
        settings = context.scene.fs25_bake_settings
        try:
            image = _refresh_mask_preview(settings)
        except (RuntimeError, TypeError, ValueError) as error:
            self.report({"ERROR"}, f"Preview could not be combined: {error}")
            return {"CANCELLED"}
        if image is None or settings.packed_preview_image is None:
            self.report({"ERROR"}, "No mask channel image available")
            return {"CANCELLED"}
        return bpy.ops.fs25_bake.export_mask("INVOKE_DEFAULT")



def _draw_atlas_content(layout, settings):
    atlas_type = layout.column(align=False)
    _draw_labeled_prop(atlas_type, settings, "atlas_map_type", "Atlas Type:")

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
    bl_category = "FS25 Bake Machine"

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
        _draw_labeled_prop(mask_row, settings, "mask_profile", "Profile:")

        if settings.mask_profile == "ATLAS":
            _draw_atlas_content(layout, settings)
            return

        if settings.mask_profile == "CUSTOM":
            self.draw_uv_status(context, layout)
            self.draw_material_bakes(context, layout, settings)
            self.draw_mmask(context, layout, settings)
            self.draw_export(context, layout, settings)
            return

        _draw_shader_activation(profile, settings)
        if not settings.mask_enabled:
            layout.label(text="Shader is disabled", icon="INFO")
            return

        self.draw_uv_status(context, layout)
        self.draw_material_bakes(context, layout, settings)
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
            _draw_labeled_prop(body, settings, "mmask_shader", "Shader:")
        elif settings.mask_profile == "VMASK":
            _draw_labeled_prop(body, settings, "vmask_shader", "Shader:")
        else:
            FS25BAKE_PT_main.draw_specular(context, body, settings)
            return
        body.separator()

        for title, color_name, channel_prop, image_prop in (
            ("Wear / Gloss", "Red", "channel_r", "wear_image"),
            ("Ambient Occlusion | AO", "Green", "channel_g", "ao_image"),
            ("Dirt", "Blue", "channel_b", "dirt_image"),
        ):
            _draw_mask_channel(context, body, settings, title, color_name, channel_prop, image_prop)

        _draw_centered_title(body, "Additional Channels")
        advanced = body.column(align=False)
        _draw_labeled_prop(advanced, settings, "channel_a", "Alpha:")
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
        _draw_labeled_prop(ao_box, settings, "specular_ao_mode", "Source:")
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
        bake_uv = bake_settings.split(factor=0.36, align=True)
        bake_uv.label(text="Bake UV")
        detected_target = info["detected_target"] if info else None
        if detected_target:
            detected = bake_uv.row(align=True)
            detected.enabled = False
            detected.label(text=detected_target, icon="LOCKED")
        else:
            bake_uv.prop(settings, "bake_uv_mode", text="")

        _draw_labeled_prop(bake_settings, settings, "output_resolution", "Resolution")

        preview = body.column(align=False)
        combined = preview.row(align=True)
        combined.prop(settings, "preview_combined", text="", toggle=False)
        combined.label(text="Combined RGB")

        channels = preview.row(align=True)
        channels.enabled = not settings.preview_combined
        channels.prop(
            settings,
            "preview_wear",
            text=f"R · {CHANNEL_LABELS.get(settings.channel_r, settings.channel_r)}",
        )
        channels.prop(
            settings,
            "preview_ao",
            text=f"G · {CHANNEL_LABELS.get(settings.channel_g, settings.channel_g)}",
        )
        channels.prop(
            settings,
            "preview_dirt",
            text=f"B · {CHANNEL_LABELS.get(settings.channel_b, settings.channel_b)}",
        )

        inverted = []
        if settings.invert_red_preview:
            inverted.append("R")
        if settings.invert_green_preview:
            inverted.append("G")
        if settings.invert_blue_preview:
            inverted.append("B")
        if inverted:
            preview.label(text=f"Inverted: {', '.join(inverted)}", icon="ARROW_LEFTRIGHT")

        display = preview.box()
        preview_image = settings.mask_preview_display_image

        if preview_image is not None:
            display.template_ID_preview(
                settings,
                "mask_preview_display_image",
                rows=6,
                cols=6,
                hide_buttons=True,
            )
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
        save = actions.row(align=True)
        save.enabled = settings.packed_preview_image is not None
        save.operator("fs25_bake.save_preview", icon="FILE_TICK")

    @staticmethod
    def draw_material_bakes(context, layout, settings):
        box = layout.box()
        if not _draw_collapsible_header(
            box, settings, "show_material_bakes", "Material Bakes", "RENDER_STILL"
        ):
            return

        body = layout.column(align=False)
        detected_diffuse, detected_normal = _detected_material_source_images(context)
        _draw_labeled_prop(body, settings, "output_resolution", "Resolution")
        source_info = body.box()
        source_info.label(
            text=f"Diffuse source: {detected_diffuse.name}" if detected_diffuse else "Diffuse source: Not detected",
            icon="CHECKMARK" if detected_diffuse else "INFO",
        )
        source_info.label(
            text=f"Normal source: {detected_normal.name}" if detected_normal else "Normal source: Not detected",
            icon="CHECKMARK" if detected_normal else "INFO",
        )
        _draw_material_map_bake(
            context, body, settings, "Diffuse", "DIFFUSE", "baked_diffuse_image"
        )
        _draw_material_map_bake(
            context, body, settings, "Normal", "NORMAL", "baked_normal_image"
        )
        if context.scene.render.engine != "CYCLES":
            warning = body.box()
            warning.alert = True
            warning.label(text="Cycles must be active for native material baking.", icon="ERROR")

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
    FS25BAKE_OT_new_black_wear,
    FS25BAKE_OT_new_black_ao,
    FS25BAKE_OT_new_black_dirt,
    FS25BAKE_OT_bake_ao_channel,
    FS25BAKE_OT_bake_material_map,
    FS25BAKE_OT_save_channel_image,
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
