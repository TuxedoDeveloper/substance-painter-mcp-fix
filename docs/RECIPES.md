# Layer Recipes

Layer recipes create nested Group, Fill, and Paint structures as one transaction. A failed channel, mask, or insertion step removes every node created by that request.

## Recommended workflow

1. Call `plan_layer_recipe` with the complete recipe.
2. Review the target Texture Set, node counts, resolved OpenPBR channels, current snapshot digest, and optional backup path.
3. Call `create_layer_recipe` with the same arguments.
4. Capture a new `snapshot_layer_tree` and compare it with `diff_layer_snapshots`.

Pass `backup_path` to both plan and create calls when the operation should first create an `.spp` copy. The path must be inside `SP_MCP_PROJECT_ROOTS`. The copy is written through Painter's `save_as_copy` API and does not relocate the open project.

## Recipe fields

Every node accepts:

| Field | Type | Description |
|---|---|---|
| `type` | string | Required: `group`, `fill`, or `paint`. |
| `name` | string | Required non-empty layer name. |
| `visible` | boolean | Optional initial visibility. |
| `mask` | object | Optional `Black` or `White` mask and enabled state. |

Group nodes may contain a recursive `children` list. Fill nodes may contain `base_color`, `channels`, and `active_channels`. Paint nodes do not accept `active_channels`: Painter's PaintLayerNode exposes no persistent channel-mask API. Such recipes are rejected locally before any nodes or backups are created.

Scalar channel values are expanded to RGB uniform values. `Roughness`, `Metallic`, and `Emission` are resolved to the matching legacy or OpenPBR runtime channel.

For an existing split-source Fill, individual value/resource/anchor assignments activate only the requested channel and preserve unrelated sources. Per-channel setters reject material-mode Fills rather than silently replacing a whole material; use `set_fill_parameters` for material parameters, or create a separate Fill.

The explicit `set_active_channels` operation is different: Painter resets split sources when its mask changes. The server preserves retained uniform colors and skips unchanged masks. It rejects changed masks containing non-uniform split sources (bitmaps, procedural graphs, or anchors) before mutation, because rebuilding those sources could discard settings. Assign individual channel sources instead. Material-mode channel masks retain the shared material source.

If an explicit channel-mask edit fails, the server attempts to restore the original mask and colors. If restoration also fails, the error reports both failures and explicitly marks restoration as incomplete. Inspect the layer or restore a project backup before continuing edits.

## Example

The repository includes [a VRChat outfit starter recipe](../examples/recipes/vrchat_outfit.json). It is intentionally shader-neutral: review colors, mask content, and channel packing before final export.

```json
[
  {
    "type": "group",
    "name": "00_BASE",
    "children": [
      {
        "type": "fill",
        "name": "Base Material",
        "base_color": [0.5, 0.5, 0.5],
        "channels": {"Roughness": 0.5, "Metallic": 0.0}
      }
    ]
  }
]
```

## Recovery guarantees

- Local schema errors stop before Painter is called.
- Runtime validation occurs before layer creation.
- A failure during layer creation deletes every node created by the transaction.
- A failure during post-creation snapshot verification deletes every newly created root group or layer.
- An optional backup remains available even if the recipe later fails.

The transaction does not modify or roll back pre-existing layers. Existing-layer movement is intentionally unavailable until Painter exposes a confirmed lossless move primitive.
