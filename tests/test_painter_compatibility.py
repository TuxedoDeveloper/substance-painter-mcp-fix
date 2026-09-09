"""Execute production snippets against the relevant Painter API semantics.

Painter 12.1 resets split sources when active_channels is assigned, while
set_source activates one channel without resetting its neighbors. Paint nodes
are fresh Python wrappers with no persistent active_channels property.
"""
import builtins
from enum import Enum
import sys
from types import ModuleType, SimpleNamespace

import pytest

from substance_painter_mcp.client import PainterScriptError
from substance_painter_mcp.operations import PainterOperations


class Channel(Enum):
    BaseColor = 0
    SpecularRoughness = 1
    Roughness = 1
    BaseMetalness = 2
    Metallic = 2
    Height = 3


class Color:
    def __init__(self, *value):
        self.value_raw = tuple(value)
        self.color_space = "Raw"


class Uniform:
    def __init__(self, color):
        self.color = color

    def get_color(self):
        return self.color


class ResourceID:
    def __init__(self, url):
        self._url = url

    @classmethod
    def from_url(cls, url):
        return cls(url)

    def url(self):
        return self._url


class ResourceSource:
    def __init__(self, url):
        self.resource_id = ResourceID(url)
        self.parameters = {"roughness": 0.27}


class Node:
    def __init__(self, state):
        self.state = state

    def uid(self):
        return self.state["uid"]

    def get_name(self):
        return "Fixture"

    def get_texture_set(self):
        return "Body"


class Paint(Node):
    pass


class Anchor(Node):
    pass


class Fill(Node):
    @property
    def source_mode(self):
        return SimpleNamespace(name=self.state["mode"])

    @property
    def active_channels(self):
        return set(self.state["sources"])

    @active_channels.setter
    def active_channels(self, channels):
        self.state["mask_writes"] += 1
        self.state["sources"] = {c: Uniform(Color(0.8, 0.8, 0.8)) for c in channels}

    def get_source(self, channel):
        return self.state["sources"].get(channel)

    def set_source(self, channel, value):
        if isinstance(value, Color):
            source = Uniform(value)
        elif isinstance(value, ResourceID):
            source = ResourceSource(value.url())
        elif isinstance(value, Anchor):
            source = SimpleNamespace(anchor=value.uid())
        else:
            raise ValueError("Painter does not accept Source objects in set_source")
        self.state["sources"][channel] = source
        return source


class ExecutingRemote:
    def __init__(self):
        self.calls = []

    def execute_python_json(self, code, params=None):
        self.calls.append((code, params))
        namespace = {"params": params or {}}
        try:
            exec(compile(code, "<Painter snippet>", "exec"), namespace)
            return {"success": True, "data": namespace["result"]}
        except Exception as exc:
            return {"success": False, "error_type": type(exc).__name__, "error": str(exc)}


@pytest.fixture
def painter(monkeypatch):
    package = ModuleType("substance_painter")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "substance_painter", package)
    modules = {}
    for name in ("layerstack", "textureset", "source", "resource", "colormanagement",
                 "baking", "event", "project", "export"):
        module = ModuleType(f"substance_painter.{name}")
        modules[name] = module
        setattr(package, name, module)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    states = {
        1: {"uid": 1, "mode": "Split", "mask_writes": 0,
            "sources": {Channel.BaseColor: Uniform(Color(0.1, 0.2, 0.3))}},
        2: {"uid": 2},
        3: {"uid": 3},
    }
    layers = modules["layerstack"]
    layers.FillLayerNode = Fill
    layers.PaintLayerNode = Paint
    layers.LayerNode = Node
    layers.AnchorPointEffectNode = Anchor
    layers.get_node_by_uid = lambda uid: {1: Fill, 2: Paint, 3: Anchor}[uid](states[uid])
    modules["textureset"].ChannelType = Channel
    modules["colormanagement"].Color = Color
    modules["source"].SourceUniformColor = Uniform
    modules["source"].SourceSubstance = ResourceSource
    modules["resource"].ResourceID = ResourceID
    modules["resource"].Shelf = type("Shelf", (), {})
    for name in ("BlendingMode", "MaskBackground", "GeometryMaskType", "ProjectionMode"):
        setattr(layers, name, SimpleNamespace(__members__={}))
    layers.InsertPosition = SimpleNamespace()
    return SimpleNamespace(modules=modules, states=states, remote=ExecutingRemote())


@pytest.mark.parametrize("existing", ["color", "bitmap", "procedural", "anchor"])
@pytest.mark.parametrize("operation", ["values", "resource", "anchor"])
def test_channel_edit_preserves_other_sources(painter, existing, operation):
    source = {
        "color": Uniform(Color(0.1, 0.2, 0.3)),
        "bitmap": ResourceSource("resource://fixture/color.png"),
        "procedural": ResourceSource("resource://fixture/material.sbsar"),
        "anchor": SimpleNamespace(anchor=3),
    }[existing]
    painter.states[1]["sources"][Channel.BaseColor] = source
    operations = PainterOperations(painter.remote)
    if operation == "values":
        operations.set_fill_channels(1, {"Roughness": 0.42})
    elif operation == "resource":
        operations.set_fill_resource(1, "resource://fixture/roughness.png", channel="Roughness")
    else:
        operations.set_fill_anchor_source(1, 3, channel="Roughness")
    assert painter.states[1]["sources"][Channel.BaseColor] is source
    assert Channel.Roughness in painter.states[1]["sources"]
    assert painter.states[1]["mask_writes"] == 0


def test_explicit_fill_mask_preserves_retained_colors(painter):
    operations = PainterOperations(painter.remote)
    operations.set_active_channels(1, ["BaseColor", "Roughness"])
    source = Fill(painter.states[1]).get_source(Channel.BaseColor)
    assert source.get_color().value_raw == (0.1, 0.2, 0.3)


def test_unchanged_fill_mask_does_not_reset_resource(painter):
    source = ResourceSource("resource://fixture/material.sbsar")
    painter.states[1]["sources"][Channel.BaseColor] = source
    PainterOperations(painter.remote).set_active_channels(1, ["BaseColor"])
    assert painter.states[1]["sources"][Channel.BaseColor] is source
    assert painter.states[1]["mask_writes"] == 0


def test_unsafe_resource_mask_change_rejected_before_mutation(painter):
    source = ResourceSource("resource://fixture/material.sbsar")
    painter.states[1]["sources"][Channel.BaseColor] = source
    with pytest.raises(PainterScriptError, match="non-uniform"):
        PainterOperations(painter.remote).set_active_channels(1, ["BaseColor", "Roughness"])
    assert painter.states[1]["sources"][Channel.BaseColor] is source
    assert painter.states[1]["mask_writes"] == 0


def test_paint_channels_rejected_instead_of_setting_wrapper_attribute(painter):
    with pytest.raises(PainterScriptError, match="Paint"):
        PainterOperations(painter.remote).set_active_channels(2, ["BaseColor"])
    assert painter.states[2] == {"uid": 2}


@pytest.mark.parametrize("operation", ["color", "values", "resource", "anchor"])
def test_per_channel_edits_reject_material_mode_without_changing_sources(painter, operation):
    painter.states[1]["mode"] = "Material"
    original = dict(painter.states[1]["sources"])
    operations = PainterOperations(painter.remote)
    with pytest.raises(PainterScriptError, match="split-source"):
        if operation == "color":
            operations.set_fill_base_color(1, [0.3, 0.4, 0.5])
        elif operation == "values":
            operations.set_fill_channels(1, {"Roughness": 0.42})
        elif operation == "resource":
            operations.set_fill_resource(1, "resource://fixture/roughness.png", channel="Roughness")
        else:
            operations.set_fill_anchor_source(1, 3, channel="Roughness")
    assert painter.states[1]["sources"] == original
    assert painter.states[1]["mask_writes"] == 0


def test_fill_mask_restores_original_colors_if_activation_fails(painter, monkeypatch):
    original_setter = Fill.active_channels.fset

    def fail_once(node, channels):
        original_setter(node, channels)
        if node.state["mask_writes"] == 1:
            raise ValueError("activation failed")

    monkeypatch.setattr(Fill, "active_channels", property(Fill.active_channels.fget, fail_once))
    with pytest.raises(PainterScriptError, match="activation failed"):
        PainterOperations(painter.remote).set_active_channels(1, ["BaseColor", "Roughness"])
    node = Fill(painter.states[1])
    assert node.active_channels == {Channel.BaseColor}
    assert node.get_source(Channel.BaseColor).get_color().value_raw == (0.1, 0.2, 0.3)


def test_fill_mask_recovers_when_retained_color_write_fails(painter, monkeypatch):
    original_set_source = Fill.set_source

    def fail_during_edit(node, channel, value):
        if node.state["mask_writes"] == 1:
            raise ValueError("retained color failed")
        return original_set_source(node, channel, value)

    monkeypatch.setattr(Fill, "set_source", fail_during_edit)
    with pytest.raises(PainterScriptError, match="retained color failed"):
        PainterOperations(painter.remote).set_active_channels(1, ["BaseColor", "Roughness"])
    node = Fill(painter.states[1])
    assert node.active_channels == {Channel.BaseColor}
    assert node.get_source(Channel.BaseColor).get_color().value_raw == (0.1, 0.2, 0.3)


@pytest.mark.parametrize("rollback_failure", ["mask", "color"])
def test_fill_mask_reports_both_errors_if_recovery_fails(painter, monkeypatch, rollback_failure):
    original_mask_setter = Fill.active_channels.fset
    original_set_source = Fill.set_source

    def set_mask(node, channels):
        original_mask_setter(node, channels)
        if node.state["mask_writes"] == 2 and rollback_failure == "mask":
            raise RuntimeError("rollback mask failed")

    def set_color(node, channel, value):
        if node.state["mask_writes"] == 1:
            raise ValueError("retained color failed")
        if rollback_failure == "color":
            raise RuntimeError("rollback color failed")
        return original_set_source(node, channel, value)

    monkeypatch.setattr(Fill, "active_channels", property(Fill.active_channels.fget, set_mask))
    monkeypatch.setattr(Fill, "set_source", set_color)
    with pytest.raises(PainterScriptError) as caught:
        PainterOperations(painter.remote).set_active_channels(1, ["BaseColor", "Roughness"])
    message = str(caught.value)
    assert "ValueError: retained color failed" in message
    assert f"RuntimeError: rollback {rollback_failure} failed" in message
    assert "restoration is incomplete" in message
    assert Fill(painter.states[1]).get_source(Channel.BaseColor).get_color().value_raw == (0.8, 0.8, 0.8)


@pytest.mark.parametrize("method", ["plan_layer_recipe", "create_layer_recipe"])
def test_paint_recipe_channels_rejected_before_remote_call(method):
    remote = ExecutingRemote()
    recipe = [{"type": "group", "name": "Parent", "children": [
        {"type": "paint", "name": "Paint", "active_channels": ["BaseColor"]}]}]
    with pytest.raises(ValueError, match="Paint"):
        getattr(PainterOperations(remote), method)(recipe)
    assert remote.calls == []


@pytest.fixture
def baking(painter, monkeypatch):
    target = SimpleNamespace(name=lambda: "Body", has_uv_tiles=lambda: False,
                             get_mesh_map_resource=lambda usage: None)
    prop = lambda value: SimpleNamespace(value=lambda: value, enum_values=lambda: {})
    settings = SimpleNamespace(
        is_textureset_enabled=lambda: True,
        get_enabled_bakers=lambda: [SimpleNamespace(name="AO")],
        get_enabled_uv_tiles=lambda: [],
        common=lambda: {"HipolyMesh": prop(""), "CageMesh": prop(""),
                        "LowAsHigh": prop(True), "CageMode": prop(0), "OutputSize": prop([8, 8])},
    )
    painter.modules["baking"].BakingParameters = SimpleNamespace(from_texture_set=lambda ts: settings)
    calls = []
    painter.modules["baking"].bake_async = lambda ts: calls.append(ts)
    ts = painter.modules["textureset"]
    ts.TextureSet = SimpleNamespace(from_name=lambda name: target)
    ts.all_texture_sets = lambda: [target]
    ts.MeshMapUsage = SimpleNamespace(__members__={"AO": "AO"})
    painter.modules["project"].is_open = lambda: True
    painter.modules["project"].is_busy = lambda: False
    events = painter.modules["event"]
    events.DISPATCHER = SimpleNamespace(connect_strong=lambda *args: None)
    for name in ("BakingProcessAboutToStart", "BakingProcessProgress", "BakingProcessEnded"):
        setattr(events, name, type(name, (), {}))
    qt = ModuleType("PySide6")
    qt.QtCore = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "PySide6", qt)
    for name in ("_sp_mcp_bake_state", "_sp_mcp_bake_refs", "_sp_mcp_bake_stop_source"):
        monkeypatch.setattr(builtins, name, None, raising=False)
    return SimpleNamespace(target=target, settings=settings, calls=calls)


@pytest.mark.parametrize("udim,tiles,ready", [(False, [], True), (True, [], False),
                                           (True, [SimpleNamespace(u=0, v=0)], True)])
def test_bake_preflight_distinguishes_udim(painter, baking, udim, tiles, ready):
    baking.target.has_uv_tiles = lambda: udim
    baking.settings.get_enabled_uv_tiles = lambda: tiles
    result = PainterOperations(painter.remote).preflight_bake(["Body"])
    assert result["ready"] is ready


@pytest.mark.parametrize("udim,tiles,enabled,allowed", [
    (False, [], True, True), (True, [], True, False),
    (True, [SimpleNamespace(u=0, v=0)], True, True), (False, [], False, False)])
def test_single_bake_validates_real_tile_requirement(painter, baking, udim, tiles, enabled, allowed):
    baking.target.has_uv_tiles = lambda: udim
    baking.settings.get_enabled_uv_tiles = lambda: tiles
    baking.settings.is_textureset_enabled = lambda: enabled
    if allowed:
        PainterOperations(painter.remote).start_bake("Body", confirm=True)
        assert baking.calls == [baking.target]
    else:
        with pytest.raises(PainterScriptError):
            PainterOperations(painter.remote).start_bake("Body", confirm=True)
        assert baking.calls == []


PRESET_METHODS = (
    "from_texture_set", "common", "baker", "set", "is_textureset_enabled",
    "get_enabled_bakers", "get_enabled_uv_tiles", "get_curvature_method",
    "set_textureset_enabled", "set_enabled_bakers", "set_enabled_uv_tiles", "set_curvature_method",
)


@pytest.mark.parametrize("missing", [None, "common", "baker", "set", "set_enabled_uv_tiles"])
def test_baking_preset_capability_tracks_used_api(painter, missing):
    api = SimpleNamespace(**{name: lambda: None for name in PRESET_METHODS if name != missing})
    painter.modules["baking"].BakingParameters = api
    features = PainterOperations(painter.remote).capabilities()["features"]
    assert features["baking_presets"] is (missing is None)
