# FS25 Texture-Bake-Machine

**Version 1.0.0** · **Maintainers: Maddog Design & Djain**  
[Deutsche Dokumentation](README.de.md)

A Blender add-on for preparing and packing texture maps for GIANTS Farming Simulator 25 workflows. It combines recurring channel-packing and texture-atlas tasks in one compact Blender sidebar tool.

Tested with Blender **4.0.2**, **4.5.9 LTS**, **5.1.2**, and **5.2.1 LTS**.

## Installation and shader source

Install and enable the add-on in Blender's Preferences. In the add-on preferences, select the Farming Simulator 25 game folder and run **Check Shader Folder**. The scan uses read-only access; game files are never modified.

![Installation, activation, and GIANTS shader source](images/install.png)

## Tool profiles

Open the **FS25 Bake** tab in the 3D View sidebar and select the required workflow:

- **Building Shader – mMask**
- **Vehicle Shader – vMask**
- **Custom Specular**
- **Texture Array | Atlas**

Only the mMask and vMask profiles require shader activation. Custom Specular and Texture Array | Atlas are available directly.

![Tool profile selection](images/profile-selection.png)

## Building mMask and Vehicle vMask

The mMask and vMask profiles use the same compact channel-assignment workflow. Select the corresponding shader profile and assign the painted grayscale textures:

| Channel | Default content |
| --- | --- |
| Red | Wear / Gloss |
| Green | Ambient Occlusion / AO |
| Blue | Dirt |
| Alpha | Optional additional channel |

The add-on validates the active mesh and UV setup, packs the selected maps, provides a combined preview, and exports the result with the profile-specific filename suffix (`_mMask.png` or `_vMask.png`).

![mMask and vMask workflow](images/mmask-vmask.png)

## Custom Specular

Custom Specular creates a packed RGB specular texture without requiring a GIANTS shader to be activated. Diffuse and Normal textures can be detected from the active material or selected manually. AO can be supplied as a texture or baked automatically from the mesh.

| Channel | Output |
| --- | --- |
| Red | Gloss (`1 − Roughness`) |
| Green | Ambient Occlusion |
| Blue | Metallic |
| Alpha | Not used |

Material Metallic and Roughness values can be read directly from the active Principled BSDF or entered manually.

![Custom Specular settings](images/custom-specular.png)

## Texture Array | Atlas

This profile vertically stacks four source textures into one PNG atlas. Texture 1 is placed at the top and Texture 4 at the bottom.

All four source textures must:

- be present;
- have identical square dimensions;
- use one of the supported sizes: 512, 1024, 2048, or 4096 pixels;
- use the same image depth.

Example: four 1024 × 1024 textures produce one 1024 × 4096 atlas. Diffuse, Normal, and Specular atlas types are available.

![Texture Array and Atlas settings](images/texture-array-atlas.png)

## Output and status

- Native Blender progress reporting remains visible during processing.
- Automatic or custom output filenames are supported.
- The current `.blend` folder or a manually selected folder can be used.
- Existing files are overwritten only after confirmation.
- Atlas output is exported as PNG for later conversion when another game texture format is required.


## License

Copyright © 2026 Maddog Design & Djain.

This project is released under the **GNU General Public License v3.0 or later** (`GPL-3.0-or-later`). See [LICENSE](LICENSE) for details.
