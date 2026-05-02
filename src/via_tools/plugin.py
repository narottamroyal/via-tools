import json
import math
import shapely
import dearpygui.dearpygui as dpg

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from kipy import KiCad
from kipy.board_types import ArcTrack, Group, Pad, Track, Via, Zone
from kipy.geometry import Box2, Vector2, normalize_angle_pi_radians
from kipy.project_types import NetClass
from kipy.proto.common.types.base_types_pb2 import KIID
from kipy.util import units
from mashumaro.mixins.json import DataClassJSONMixin
from shapely.geometry import LineString, Point, Polygon

__version__ = "0.1.0"


class Pattern(Enum):
    GRID = "Grid"
    STAGGER_ROWS = "Stagger Rows"
    STAGGER_COLUMNS = "Stagger Columns"
    HEXAGONAL = "Hexagonal"


@dataclass
class ViaSettings:
    """Stores explicit physical parameters for the stitching vias."""

    net: str
    diameter: float
    hole_size: float


@dataclass
class Config(DataClassJSONMixin):
    """Core plugin configuration parameters for a stitching group."""

    pattern: Pattern = Pattern.GRID
    spacing: float = 1.0
    clearance: float = 0.0
    offset: tuple[float, float] = (0.0, 0.0)
    via_settings: ViaSettings | None = None


@dataclass
class GroupConfig(DataClassJSONMixin):
    """Associates a specific via configuration with a KiCad group ID."""

    config: Config
    group_id: str


@dataclass
class ProjectConfig(DataClassJSONMixin):
    """Manages the board-specific history of via stitching configurations."""

    version: str = __version__
    config_history: dict[str, list[GroupConfig]] = field(default_factory=dict)

    def get_zone_by_group(self, group_id: str) -> str | None:
        for zone_id, group_configs in self.config_history.items():
            if any(config.group_id == group_id for config in group_configs):
                return zone_id
        return None

    def add_history_entry(self, zone_id: str, group_config: GroupConfig) -> None:
        history = self.config_history.setdefault(zone_id, [])
        history.insert(0, group_config)
        if len(history) > 10:
            history.pop()


def arc_angle(track: ArcTrack) -> float | None:
    center = track.center()
    if center is None:
        return None

    angle1 = (track.mid - center).angle() - (track.start - center).angle()
    angle2 = (track.end - center).angle() - (track.mid - center).angle()

    return normalize_angle_pi_radians(angle1) + normalize_angle_pi_radians(angle2)


def arc_points(track: ArcTrack, max_error: float = 1e-2) -> list[tuple[float, float]]:
    center = track.center()
    radius = track.radius()
    start_angle = track.start_angle()
    signed_angle = arc_angle(track)

    if center is None or signed_angle is None or radius == 0:
        return [(track.start.x, track.start.y), (track.end.x, track.end.y)]

    cos_domain = max(-1.0, min(1.0, 1 - units.from_mm(max_error) / radius))
    error_step_size = 2 * math.acos(cos_domain)

    if error_step_size <= 0:
        steps = 2
    else:
        steps = int(math.ceil(abs(signed_angle) / error_step_size))
        steps = max(steps, 2)

    points = []
    for i in range(steps + 1):
        current_angle = start_angle + (signed_angle * (i / steps))
        x = center.x + radius * math.cos(current_angle)
        y = center.y + radius * math.sin(current_angle)
        points.append((x, y))

    points[0] = (track.start.x, track.start.y)
    points[-1] = (track.end.x, track.end.y)

    return points


def track_to_geo(
    track: Track | ArcTrack, netclasses: dict[str, NetClass]
) -> shapely.Geometry:
    points = []
    match track:
        case Track(start=start, end=end):
            points.append((start.x, start.y))
            points.append((end.x, end.y))
        case ArcTrack():
            points = arc_points(track)
        case _:
            raise ValueError(f"Incorrect track type: {track}")

    clearance = netclasses[track.net.name].clearance if track.net else 0.0
    return LineString(points).buffer(track.width / 2 + clearance)


def generate_positions(config: Config, bounding_box: Box2) -> list[tuple[float, float]]:
    start = bounding_box.pos
    size = bounding_box.size

    spacing = units.from_mm(config.spacing)
    cols = int(size.x // spacing) + 1
    rows = int(size.y // spacing) + 1

    y_spacing = spacing
    if config.pattern == Pattern.HEXAGONAL:
        y_spacing = int(spacing * (math.sqrt(3) / 2))
        rows = int(size.y // y_spacing) + 1

    grid_x = (cols - 1) * spacing
    grid_y = (rows - 1) * y_spacing

    start_x = start.x + (size.x - grid_x) // 2
    start_y = start.y + (size.y - grid_y) // 2

    start_x += units.from_mm(config.offset[0]) % spacing
    start_y += units.from_mm(config.offset[1]) % y_spacing

    positions = []
    for row in range(rows):
        for col in range(cols):
            x = start_x + (col * spacing)
            y = start_y + (row * y_spacing)

            if config.pattern == Pattern.STAGGER_ROWS and row % 2 != 0:
                x += spacing // 2
            elif config.pattern == Pattern.STAGGER_COLUMNS and col % 2 != 0:
                y += y_spacing // 2
            elif config.pattern == Pattern.HEXAGONAL and row % 2 != 0:
                x += spacing // 2

            if start.x < x < start.x + size.x and start.y < y < start.y + size.y:
                positions.append((x, y))

    return positions


class ViaTools:
    """Handles KiCad business logic, config file I/O, and boolean geometry operations."""

    def __init__(self) -> None:
        self.kicad = KiCad()
        self.board = self.kicad.get_board()
        self.plugin_config = Config()
        self.project_config = ProjectConfig()

        # Populated by initialize()
        self.stitching_item: Pad | Zone | None = None
        self.netclass: NetClass | None = None
        self.config: Config | None = None
        self.group: Group | None = None
        self.via_positions: list[Vector2] = []

    @property
    def active_via_settings(self) -> ViaSettings:
        """Returns the user-overridden via settings, or defaults to the netclass settings."""
        return self.config.via_settings or self.netclass_via_settings()

    def initialize(self) -> None:
        self._load_plugin_config()
        self._load_project_config()
        self._load_zone()
        self._load_netclass(self.stitching_item)
        self._load_existing_group()
        self._load_config()

    def _load_plugin_config(self) -> None:
        path = Path(self.kicad.get_plugin_settings_path("a.a.a"))
        self.plugin_config_path = path.with_name("com.github.narottamroyal.via-tools")
        if self.plugin_config_path.exists():
            self.plugin_config = Config.from_json(self.plugin_config_path.read_text())

    def save_plugin_config(self) -> None:
        self.plugin_config_path.write_text(self.config.to_json())

    def _load_project_config(self) -> None:
        path = Path(self.board.get_project().path)
        self.project_config_path = path / ".via-tools.json"
        if self.project_config_path.exists():
            self.project_config = ProjectConfig.from_json(
                self.project_config_path.read_text()
            )

    def save_project_config(self) -> None:
        group_config = GroupConfig(deepcopy(self.config), self.group.id.value)
        self.project_config.add_history_entry(
            self.stitching_item.id.value, group_config
        )
        self.project_config_path.write_text(
            self.project_config.to_json(encoder=lambda x: json.dumps(x, indent=2))
        )

    def _load_zone(self) -> None:
        selections = self.board.get_selection()
        if not selections:
            raise ValueError("Please select a zone, pad, or via stitching group.")
        if len(selections) > 1:
            raise ValueError(
                "Selection contains multiple items. Please select a single target."
            )

        selection = selections[0]
        match selection:
            case Zone() | Pad():
                self.stitching_item = selection
            case Group(id=group_id, name=name):
                zone_id = self.project_config.get_zone_by_group(group_id.value)
                if not zone_id:
                    if name == "Via Stitching":
                        raise ValueError(
                            "Configuration history missing for this via group. Delete and recreate."
                        )
                    raise ValueError("Unknown group selected.")

                items = self.board.get_items_by_id(KIID(value=zone_id))
                if not items:
                    raise ValueError(
                        "Cannot find the zone/pad associated with this via group."
                    )
                self.stitching_item = items[0]
            case _:
                raise ValueError("Unknown item selected.")

    def _load_netclass(self, item: Pad | Zone) -> None:
        if item.net is None:
            raise ValueError("Please assign a net to the selected item.")

        netclass = self.board.get_netclass_for_nets(item.net)[item.net.name]
        if netclass.via_diameter is None or netclass.via_drill is None:
            raise ValueError(
                f"Configure via diameter and hole size for netclass '{netclass.name}'."
            )

        self.netclass = netclass

    def _load_existing_group(self) -> None:
        groups = [
            g for g in self.board.get_groups() if g.name.startswith("Via Stitching")
        ]
        known_group_ids = [
            c.group_id
            for configs in self.project_config.config_history.values()
            for c in configs
        ]

        for group in groups:
            if group.id.value not in known_group_ids:
                self.board.clear_selection()
                self.board.add_to_selection(group)
                raise ValueError(
                    "Found orphaned via stitching group. Please delete it."
                )

        group_configs = self.project_config.config_history.get(
            self.stitching_item.id.value, []
        )
        group_ids = [config.group_id for config in group_configs]
        self.group = next((g for g in groups if g.id.value in group_ids), None)

    def _load_config(self) -> None:
        zone_config_history = self.project_config.config_history.get(
            self.stitching_item.id.value, []
        )
        config = None

        if zone_config_history:
            if self.group:
                config = next(
                    (
                        group_config.config
                        for group_config in zone_config_history
                        if group_config.group_id == self.group.id.value
                    ),
                    None,
                )
            config = config or zone_config_history[0].config
        else:
            config = self.plugin_config

        self.config = deepcopy(config)

    def netclass_via_settings(self) -> ViaSettings:
        return ViaSettings(
            self.stitching_item.net.name,
            units.to_mm(self.netclass.via_diameter),
            units.to_mm(self.netclass.via_drill),
        )

    def _get_pad_obstacles(self) -> shapely.Geometry:
        pads = [
            pad
            for pad in self.board.get_pads()
            if pad.id.value != self.stitching_item.id.value
        ]
        return shapely.union_all(
            [
                Polygon(
                    [(node.point.x, node.point.y) for node in polygon.outline.nodes]
                ).buffer(self.netclass.clearance)
                for polygon in self.board.get_pad_shapes_as_polygons(pads)
                if polygon is not None
            ]
        )

    def _get_keepout_obstacles(self) -> shapely.Geometry:
        keepout_nodes = [
            zone.outline.outline.nodes
            for zone in self.board.get_zones()
            if zone.is_rule_area and zone._proto.rule_area_settings.keepout_vias
        ]
        return shapely.union_all(
            [
                Polygon([(node.point.x, node.point.y) for node in nodes])
                for nodes in keepout_nodes
            ]
        )

    def _get_track_obstacles(self) -> shapely.Geometry:
        tracks = self.board.get_tracks()
        netclasses = self.board.get_netclass_for_nets([track.net for track in tracks])
        return shapely.union_all([track_to_geo(track, netclasses) for track in tracks])

    def _get_via_obstacles(self) -> shapely.Geometry:
        existing_via_ids = (
            {item.id.value for item in self.group.items} if self.group else set()
        )
        return shapely.union_all(
            [
                Point(via.position.x, via.position.y).buffer(
                    max(self.config.spacing, via.diameter) / 2
                )
                for via in self.board.get_vias()
                if via.net == self.stitching_item.net
                and via.id.value not in existing_via_ids
            ]
        )

    def _get_target_geometry(self) -> shapely.Geometry:
        match self.stitching_item:
            case Zone():
                return shapely.intersection_all(
                    [
                        shapely.union_all(
                            [
                                Polygon(
                                    [
                                        (node.point.x, node.point.y)
                                        for node in polygon.outline.nodes
                                    ]
                                )
                                for polygon in polygons
                            ]
                        )
                        for polygons in self.stitching_item.filled_polygons.values()
                    ]
                )
            case Pad():
                polygon = self.board.get_pad_shapes_as_polygons(self.stitching_item)
                return Polygon(
                    [(node.point.x, node.point.y) for node in polygon.outline.nodes]
                ).buffer(self.netclass.clearance)
            case _:
                return Polygon()

    def composite_polygon(self) -> shapely.Geometry:
        """Computes the valid stitching area by subtracting obstacles from the target area."""
        target_geo = self._get_target_geometry()
        obstacles = shapely.union_all(
            [
                self._get_pad_obstacles(),
                self._get_keepout_obstacles(),
                self._get_track_obstacles(),
                self._get_via_obstacles(),
            ]
        )
        return shapely.difference(target_geo, obstacles)

    def bounding_box(self) -> Box2:
        match self.stitching_item:
            case Zone():
                return self.stitching_item.bounding_box()
            case Pad():
                pads = self.board.get_pad_shapes_as_polygons(self.stitching_item)
                return pads.bounding_box()

    def update_via_positions(self) -> None:
        polygon = self.composite_polygon()
        diameter = self.active_via_settings.diameter
        circle = Point(0, 0).buffer(
            units.from_mm(diameter) // 2 + units.from_mm(self.config.clearance)
        )

        vias = []
        for x, y in generate_positions(self.config, self.bounding_box()):
            if polygon.contains(shapely.affinity.translate(circle, x, y)):
                vias.append(Vector2().from_xy(x, y))

        self.via_positions = vias

    def place_vias(self) -> list[Via]:
        commit = self.board.begin_commit()

        if self.group:
            self.board.remove_items(self.group.items)
            self.board.remove_items(self.group)

        settings = self.active_via_settings
        template_via = Via()
        template_via.net = next(
            (net for net in self.board.get_nets() if net.name == settings.net), None
        )
        template_via.diameter = units.from_mm(settings.diameter)
        template_via.drill_diameter = units.from_mm(settings.hole_size)

        new_vias = [Via(template_via.proto) for _ in self.via_positions]
        for i, position in enumerate(self.via_positions):
            new_vias[i].position = position

        print(f"Placing {len(new_vias)} stitching vias.")
        vias = self.board.create_items(new_vias)
        self.board.push_commit(commit, "Place stitching vias")

        # Bug fix: Some of the generated vias may have the wrong net
        # Keep searching for vias with the wrong net and fix them until they are all correct
        attempts = 0
        possible_bad_vias = vias
        while bad_vias := [
            v
            for v in self.board.get_items_by_id([v.id for v in possible_bad_vias])
            if v.net is None or v.net.name != settings.net
        ]:
            self.board.remove_items(bad_vias)
            if attempts > 10:
                bad_ids = [v.id.value for v in bad_vias]
                return [v for v in vias if v.id.value not in bad_ids]

            attempts += 1
            for v in bad_vias:
                v.net = template_via.net
            possible_bad_vias = self.board.create_items(bad_vias)

        return vias

    def group_vias(self, vias: list[Via]) -> Group:
        commit = self.board.begin_commit()
        group = Group()
        group.proto.name = "Via Stitching"
        group.items = vias

        print("Grouping stitching vias...")
        group = self.board.create_items(group)[0]
        self.board.push_commit(commit, "Group stitching vias")

        self.board.clear_selection()
        self.board.add_to_selection(group)

        # Bug fix: Groups returned by create_items do not contain unwrapped items
        group._unwrapped_items = self.board.get_items_by_id(group._item_ids)

        return group

    def run(self) -> None:
        vias = self.place_vias()
        self.group = self.group_vias(vias)
        self.save_plugin_config()
        self.save_project_config()


class GUI:
    """Handles the DearPyGui interface and interactions with the ViaStitching engine."""

    def __init__(self, engine: ViaTools) -> None:
        self.vs = engine
        self.error_message: str | None = None

        try:
            self.vs.initialize()
        except ValueError as error:
            self.error_message = str(error)

    def setup_viewport(self):
        dpg.set_viewport_small_icon("icon.ico")
        dpg.set_viewport_large_icon("icon.ico")
        dpg.show_viewport()
        # The usable client viewport is smaller than the specified viewport on Windows
        # Calculate the width and height required to make the client viewport the desired size
        width = dpg.get_viewport_width() * 2 - dpg.get_viewport_client_width()
        height = dpg.get_viewport_height() * 2 - dpg.get_viewport_client_height()
        dpg.configure_viewport(0, width=width, height=height)

    @contextmanager
    def ui_loading_state(self):
        try:
            dpg.disable_item("button_group")
            yield
        finally:
            self.refresh_ui()
            dpg.enable_item("button_group")

    def on_value_update(self, sender: str, value: float | list[float] | str) -> None:
        with self.ui_loading_state():
            match (sender, value):
                case ("spacing", float(spacing)):
                    self.vs.config.spacing = spacing
                case ("clearance", float(clearance)):
                    self.vs.config.clearance = clearance
                case ("offset", [float(x), float(y), *_]):
                    self.vs.config.offset = (x, y)
                case ("pattern", str(pattern)):
                    self.vs.config.pattern = Pattern(pattern)
                case ("net", str(net)) if self.vs.config.via_settings:
                    self.vs.config.via_settings.net = net
                case ("diameter", float(diameter)) if self.vs.config.via_settings:
                    self.vs.config.via_settings.diameter = diameter
                    dpg.configure_item("spacing", min_value=diameter)
                    if self.vs.config.spacing < diameter:
                        dpg.set_value("spacing", diameter)
                        self.vs.config.spacing = diameter
                case ("hole_size", float(hole_size)) if self.vs.config.via_settings:
                    self.vs.config.via_settings.hole_size = hole_size

    def on_toggle_netclass(self, _sender: str, override_netclass: bool) -> None:
        with self.ui_loading_state():
            dpg.configure_item("netclass_group", enabled=override_netclass)

            if override_netclass:
                self.vs.config.via_settings = self.vs.netclass_via_settings()
            else:
                self.vs.config.via_settings = None
                settings = self.vs.active_via_settings

                dpg.set_value("net", settings.net)
                dpg.set_value("diameter", settings.diameter)
                dpg.set_value("hole_size", settings.hole_size)
                dpg.configure_item("spacing", min_value=settings.diameter)

                if self.vs.config.spacing < settings.diameter:
                    dpg.set_value("spacing", settings.diameter)
                    self.vs.config.spacing = settings.diameter

    def on_generate(self, _sender: str) -> None:
        self.vs.run()

    def refresh_ui(self) -> None:
        self.update_preview()
        self.vs.update_via_positions()
        dpg.configure_item(
            "button", label=f"Generate {len(self.vs.via_positions)} Vias"
        )

    def update_preview(self) -> None:
        dpg.delete_item("preview", children_only=True)
        dpg.draw_rectangle(
            (0, 0), (300, 300), color=(75, 75, 75, 255), parent="preview"
        )

        settings = self.vs.active_via_settings
        size = self.vs.config.spacing * 4 + settings.diameter - 1e-4
        scale = 200 / size
        start = 50 / 200 * size

        if self.vs.config.pattern == Pattern.HEXAGONAL:
            bounding_box = Box2.from_pos_size(
                Vector2.from_xy_mm(start, start + size / 2 * (1 - (math.sqrt(3) / 2))),
                Vector2.from_xy_mm(size, size * (math.sqrt(3) / 2)),
            )
        else:
            bounding_box = Box2.from_pos_size(
                Vector2.from_xy_mm(start, start), Vector2.from_xy_mm(size, size)
            )

        positions = [
            (units.to_mm(x) * scale, units.to_mm(y) * scale)
            for x, y in generate_positions(self.vs.config, bounding_box)
        ]

        diameter = settings.diameter * scale
        hole_size = settings.hole_size * scale
        thickness = (diameter - hole_size) / 2
        radius = (diameter - thickness) / 2

        for x, y in positions:
            dpg.draw_circle(
                center=(x, y),
                radius=radius,
                color=(212, 175, 0, 255),
                thickness=thickness,
                parent="preview",
            )

        if len(positions) >= 2:
            first_pos, second_pos = positions[0:2]
            dpg.draw_line(p1=first_pos, p2=(first_pos[0], 20), parent="preview")
            dpg.draw_line(p1=second_pos, p2=(second_pos[0], 20), parent="preview")
            dpg.draw_arrow(
                p1=(first_pos[0] + 2, 25), p2=(second_pos[0] - 2, 25), parent="preview"
            )
            dpg.draw_arrow(
                p1=(second_pos[0] - 2, 25), p2=(first_pos[0] + 2, 25), parent="preview"
            )
            dpg.draw_text(
                (second_pos[0] + 8, 18), "Grid Spacing", size=13, parent="preview"
            )

    def run(self) -> None:
        dpg.create_context()
        dpg.create_viewport(title="Via Tools", width=574, height=316, resizable=False)

        with dpg.window(tag="Main Window"):
            if self.error_message:
                dpg.add_text("ERROR", color=(255, 100, 100, 255))
                dpg.add_text(
                    self.error_message, indent=15, wrap=525, color=(255, 100, 100, 255)
                )
            else:
                self.vs.update_via_positions()

                settings = self.vs.active_via_settings
                override_netclass = self.vs.config.via_settings is not None

                with dpg.group(horizontal=True):
                    with dpg.child_window(width=250, height=300, border=True):
                        with dpg.group(width=135):
                            dpg.add_separator(label="Stitching Parameters")
                            dpg.add_combo(
                                label="Via Pattern",
                                items=[x.value for x in Pattern],
                                default_value=self.vs.config.pattern.value,
                                tag="pattern",
                                callback=self.on_value_update,
                            )
                            dpg.add_input_double(
                                label="Grid Spacing",
                                min_value=settings.diameter,
                                min_clamped=True,
                                default_value=self.vs.config.spacing,
                                format="%.2f mm",
                                tag="spacing",
                                callback=self.on_value_update,
                            )
                            dpg.add_input_double(
                                label="Clearance",
                                min_clamped=True,
                                default_value=self.vs.config.clearance,
                                format="%.2f mm",
                                tag="clearance",
                                callback=self.on_value_update,
                            )
                            dpg.add_input_doublex(
                                label="Offset (X/Y)",
                                size=2,
                                default_value=self.vs.config.offset,
                                format="%.2f mm",
                                tag="offset",
                                callback=self.on_value_update,
                            )

                            dpg.add_spacer(height=4)
                            dpg.add_separator(label="Via Settings")
                            dpg.add_checkbox(
                                label="Override Netclass Values",
                                callback=self.on_toggle_netclass,
                                default_value=override_netclass,
                            )

                            with dpg.group(
                                tag="netclass_group", enabled=override_netclass
                            ):
                                dpg.add_combo(
                                    label="Net",
                                    items=[
                                        net.name for net in self.vs.board.get_nets()
                                    ],
                                    default_value=settings.net,
                                    tag="net",
                                    callback=self.on_value_update,
                                )
                                dpg.add_input_double(
                                    label="Diameter",
                                    default_value=settings.diameter,
                                    format="%.2f mm",
                                    tag="diameter",
                                    callback=self.on_value_update,
                                )
                                dpg.add_input_double(
                                    label="Hole Size",
                                    default_value=settings.hole_size,
                                    format="%.2f mm",
                                    tag="hole_size",
                                    callback=self.on_value_update,
                                )

                        dpg.add_spacer()
                        dpg.add_separator()
                        dpg.add_spacer(height=4)

                        with dpg.group(tag="button_group"):
                            dpg.add_button(
                                label=f"Generate {len(self.vs.via_positions)} Vias",
                                width=-1,
                                height=30,
                                tag="button",
                                callback=self.on_generate,
                            )

                    with dpg.child_window(width=300, height=300, border=False):
                        with dpg.drawlist(tag="preview", width=300, height=300):
                            self.update_preview()

        dpg.setup_dearpygui()
        self.setup_viewport()
        dpg.set_primary_window("Main Window", True)
        dpg.start_dearpygui()
        dpg.destroy_context()


def main() -> None:
    engine = ViaTools()
    app = GUI(engine)
    app.run()


if __name__ == "__main__":
    main()
