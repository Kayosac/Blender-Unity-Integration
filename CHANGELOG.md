# Changelog

## [0.1.0] — 2026-10-30

First version for testing on a real project.

- Open in Blender from the Project window, Hierarchy/scene and the inspector; picker when a prefab holds several models.
- Working .blend files in ArtSource/Cache (auto-cleanup) or ArtSource/Pinned (git LFS); GUID-based links.
- Smart export: content fingerprints decide which files to write; atomic replace; per-file history with rollback.
- FBX profile (axes, units, frame rate) of the source file is reproduced on export.
- Skinned models: mesh and skeleton edits, embedded takes kept with their names and timing.
- Clips: new clips as Model@Clip.fbx (bones only), editing clip FBX files, embedded takes and .anim assets.
- Unity side: clip settings (loop, root motion) for managed clips, take remapping, Humanoid avatar copy,
  material slots resolved by name or template, MeshColliders by suffix, before/after comparison report.
- Phase 0 test bench for headless Blender.
