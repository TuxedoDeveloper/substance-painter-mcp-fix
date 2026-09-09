"""Check the compatibility fixes on disposable projects in a running Painter.

Usage: python scripts/live_compatibility.py --mesh /path/to/mesh.fbx
The original project is backed up and restored. No user shelf is modified.
Results and disposable projects are retained in --output-root (a new temp dir
by default). Run only when Painter is idle and no one else is editing it.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import struct
import tempfile
import time
import zlib

from substance_painter_mcp.client import PainterRemote
from substance_painter_mcp.operations import PainterOperations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesh", required=True, type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    mesh = args.mesh.resolve()
    root = (args.output_root or Path(tempfile.mkdtemp(prefix="painter-compatibility-"))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.glob("*.spp")):
        raise FileExistsError("Use a new output directory; project backups must not be overwritten")
    report = {"checks": [], "mesh": str(mesh), "output_root": str(root)}
    remote = PainterRemote()
    operations = PainterOperations(remote)

    def raw(code, params=None):
        result = remote.execute_python_json(code, params)
        if not result.get("success"):
            raise RuntimeError(result)
        return result["data"]

    def record(name, detail):
        report["checks"].append({"name": name, "passed": True, "detail": detail})
        print(f"PASS {name}", flush=True)

    def reject(name, action):
        try:
            action()
        except (ValueError, TypeError, RuntimeError) as exc:
            record(name, str(exc))
        else:
            raise AssertionError(f"{name}: unexpectedly accepted")

    def wait_job(getter, job_id):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = getter(job_id)
            job = result.get("job", {})
            if job.get("status") not in {None, "starting", "running"}:
                return result
            time.sleep(0.25)
        raise TimeoutError(f"Job did not finish: {job_id}")

    def wait_idle():
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if not raw("import substance_painter.project as p\nresult=p.is_busy()"):
                return
            time.sleep(0.25)
        raise TimeoutError("Painter remained busy")

    def base(uid):
        return operations.get_fill_sources(uid)["channels"]["BaseColor"]

    def value(source):
        return source["color"]["value"]

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    # A tiny generated fixture avoids dependencies on a particular installed shelf.
    bitmap = root / "fixture.png"
    bitmap.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\0\xff\0\0\0\0\xff\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )
    original = operations.project_info()
    if not original["open"]:
        raise RuntimeError("Open a project to establish a restoration target")
    state = raw("import substance_painter.project as p\nresult={'busy':p.is_busy(),'dirty':p.needs_saving()}")
    if state["busy"]:
        raise RuntimeError("Painter must be idle before starting")
    before = operations.snapshot_layer_tree()
    original_parent = Path(original["path"]).parent if original["path"] else root
    # These roots are local to this validation process, not harness configuration.
    os.environ["SP_MCP_PROJECT_ROOTS"] = os.pathsep.join([str(root), str(original_parent)])
    os.environ["SP_MCP_MESH_ROOTS"] = str(mesh.parent)
    os.environ["SP_MCP_BAKE_MESH_ROOTS"] = str(mesh.parent)
    os.environ["SP_MCP_RESOURCE_ROOTS"] = str(root)
    os.environ["SP_MCP_EXPORT_ROOTS"] = str(root)
    operations.save_project_copy(str(root / "original.spp"))
    restore_path = str(root / "original.spp") if state["dirty"] or not original["path"] else original["path"]
    report["original"] = original
    report["painter"] = operations.status()
    try:
        features = operations.capabilities()["features"]
        assert features["baking_presets"] and features["fill_active_channels"]
        assert features["paint_active_channels"] is False
        record("capabilities", features)
        for workflow in ("Default", "UVTile"):
            started = operations.create_project(
                str(mesh), str(root / f"{workflow}.spp"),
                settings={"project_workflow": workflow, "default_texture_resolution": 256},
                replace_current=True, backup_current_path=str(root / f"before-{workflow}.spp"), confirm=True,
            )
            creation = wait_job(operations.get_project_creation_job, started["job_id"])
            assert creation["job"]["status"] == "success", creation
            wait_idle()
            ts = operations.project_info()["texture_sets"][0]
            if workflow == "Default":
                imported = operations.import_session_resource(str(bitmap), "TEXTURE", confirm=True)
                materials = operations.search_resources("Carbon Fiber", usage="BASE_MATERIAL", limit=5)
                assert materials["resources"], "Carbon Fiber fixture is unavailable"
                material_url = materials["resources"][0]["url"]
                owner = operations.create_fill_layer("Anchor owner", ts)
                anchor = operations.insert_mask_effect(owner["uid"], "anchor", name="Compatibility anchor")["effects"][0]["uid"]
                for kind in ("uniform", "bitmap", "procedural", "anchor"):
                    uid = operations.create_fill_layer(f"Preserve {kind}", ts, [0.2, 0.4, 0.6])["uid"]
                    if kind == "bitmap":
                        operations.set_fill_resource(uid, imported["url"], "BaseColor")
                    elif kind == "procedural":
                        operations.set_fill_resource(uid, material_url, "BaseColor")
                        operations.set_fill_parameters(uid, {"carbon_roughness": 0.37}, "BaseColor")
                    elif kind == "anchor":
                        operations.set_fill_anchor_source(uid, anchor, "BaseColor")
                    expected = base(uid)
                    params_before = operations.get_fill_parameters(uid, "BaseColor") if kind == "procedural" else None
                    operations.set_fill_channels(uid, {"Roughness": 0.42, "Metallic": 0.1})
                    assert base(uid) == expected, (kind, expected, base(uid))
                    roughness = operations.get_fill_sources(uid)["channels"]["SpecularRoughness"]
                    assert all(abs(x - 0.42) < 1e-5 for x in value(roughness))
                    operations.set_fill_resource(uid, imported["url"], "Height")
                    assert base(uid) == expected
                    operations.set_fill_anchor_source(uid, anchor, "Roughness")
                    assert base(uid) == expected
                    if params_before:
                        assert operations.get_fill_parameters(uid, "BaseColor") == params_before
                    record(f"preserve {kind} across value/resource/anchor edits", expected)
                    current_channels = list(operations.get_fill_sources(uid)["channels"])
                    operations.set_active_channels(uid, current_channels)
                    assert base(uid) == expected
                    reject("unsafe split mask rejected", lambda: operations.set_active_channels(uid, ["BaseColor"]))
                    assert base(uid) == expected
                    snap = operations.snapshot_layer_tree(ts)
                    reject("invalid resource is non-destructive", lambda: operations.set_fill_resource(uid, "resource://session/__missing_compatibility_resource__", "Normal"))
                    assert operations.snapshot_layer_tree(ts)["sha256"] == snap["sha256"]
                plain = operations.create_fill_layer("Uniform mask", ts, [0.2, 0.4, 0.6])["uid"]
                expected = value(base(plain))
                operations.set_active_channels(plain, ["BaseColor", "Roughness"])
                assert value(base(plain)) == expected
                operations.set_active_channels(plain, ["BaseColor"])
                assert value(base(plain)) == expected
                record("uniform colors survive explicit mask additions/removals", expected)
                material = operations.create_fill_layer("Shared material", ts)["uid"]
                operations.set_fill_resource(material, material_url, material_mode=True)
                material_before = operations.get_fill_parameters(material)
                reject("material-mode scalar edits rejected", lambda: operations.set_fill_channels(material, {"Roughness": 0.3}))
                assert operations.get_fill_parameters(material) == material_before
                active = raw("import substance_painter.layerstack as l\nresult=[c.name for c in l.get_node_by_uid(params['uid']).active_channels]", {"uid": material})
                requested = sorted(set(active) | {"BaseColor", "Height"})
                operations.set_active_channels(material, requested)
                assert operations.get_fill_parameters(material) == material_before
                record("material graph survives explicit material-mode mask", requested)
                paint = operations.create_paint_layer("Paint boundary", ts)["uid"]
                snap = operations.snapshot_layer_tree(ts)
                reject("Paint mask rejected", lambda: operations.set_active_channels(paint, ["BaseColor"]))
                reject("Paint recipe rejected", lambda: operations.create_layer_recipe([
                    {"type": "paint", "name": "Unsupported", "active_channels": ["BaseColor"]}]))
                assert operations.snapshot_layer_tree(ts)["sha256"] == snap["sha256"]
                recipe = json.loads((Path(__file__).resolve().parents[1] / "examples/recipes/vrchat_outfit.json").read_text())
                created = operations.create_layer_recipe(recipe, ts)
                assert created["created_count"] > 0
                record("updated example recipe", created["created_count"])

            operations.set_baking_mesh_inputs(ts, low_as_high=True, confirm=True)
            tiles = operations.inspect_baking_parameters(ts)["uv_tiles"]
            operations.configure_baking(
                ts, enabled=True, enabled_bakers=["AO"],
                enabled_uv_tiles=tiles if workflow == "UVTile" else None,
                common_values={"OutputSize": [8, 8], "SubSampling": 0},
                baker_values={"AO": {"NbSecondary": 8}}, confirm=True,
            )
            preset = operations.capture_baking_preset(ts, ["AO"])
            operations.apply_baking_preset(ts, preset, confirm=True)
            plan = operations.preflight_bake([ts])
            assert plan["ready"], plan
            job = operations.start_bake(ts, confirm=True)
            terminal = wait_job(operations.get_bake_job, job["job_id"])
            assert terminal["job"]["status"] == "success", terminal
            wait_idle()
            maps = operations.inspect_baking(ts)["texture_sets"][0]["mesh_maps"]
            assert any(m["usage"] == "AO" and m["resource"] for m in maps)
            record(f"{workflow} single bake and preset round-trip", terminal)
            operations.configure_baking(ts, enabled=False, confirm=True)
            reject(f"{workflow} disabled single bake", lambda: operations.start_bake(ts, confirm=True))
            job = operations.start_batch_bake([ts], confirm=True)
            terminal = wait_job(operations.get_bake_job, job["job_id"])
            assert terminal["job"]["status"] == "success" and terminal["job"]["results"][ts]["all_verified"]
            wait_idle()
            assert operations.inspect_baking(ts)["texture_sets"][0]["enabled"] is False
            record(f"{workflow} batch bake restores enablement", terminal)
            if workflow == "UVTile":
                operations.configure_baking(ts, enabled=True, enabled_uv_tiles=[], confirm=True)
                assert not operations.preflight_bake([ts])["ready"]
                reject("empty UDIM single bake", lambda: operations.start_bake(ts, confirm=True))
                reject("empty UDIM batch bake", lambda: operations.start_batch_bake([ts], confirm=True))
                operations.configure_baking(ts, enabled_uv_tiles=tiles, confirm=True)
            output = root / f"unity-{workflow}"
            exported = operations.export_with_profile(str(output), "unity-urp", size_log2=9)
            images = list(output.glob("*.png"))
            assert images and all(struct.unpack(">II", p.read_bytes()[16:24]) == (512, 512) for p in images)
            record(f"{workflow} Unity URP export", exported)
    except Exception as exc:
        report["checks"].append({"name": "live regression", "passed": False, "error": repr(exc)})
        raise
    finally:
        try:
            wait_idle()
            operations.open_project(restore_path, backup_current_path=str(root / "final-test-state.spp"), confirm=True)
            after = operations.snapshot_layer_tree()
            assert after["sha256"] == before["sha256"], "Original layer snapshot did not match"
            record("original project restored", {"path": restore_path, "sha256": after["sha256"]})
        finally:
            (root / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Results: {root / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
