import math
import shapely

from pathlib import Path
from kipy import KiCad
from kipy.board_types import ArcTrack, Group, Pad, Track, Via, Zone
from kipy.geometry import Box2, Vector2, normalize_angle_pi_radians
from kipy.project_types import NetClass
from kipy.proto.common.types.base_types_pb2 import KIID
from kipy.util import units
from shapely.geometry import LineString, Point, Polygon

from .config import (
    Config,
    ConfigManager,
    Pattern,
    ViaSettings,
)


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

        self.config_manager = ConfigManager(
            plugin_path=Path(self.kicad.get_plugin_settings_path("a.a.a")).with_name(
                "com.github.narottamroyal.via-tools"
            ),
            project_path=Path(self.board.get_project().path),
        )

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
        self.stitching_item = self._load_stitching_item()
        self.netclass = self._load_netclass(self.stitching_item)
        self.group = self._load_existing_group()
        self.config = self.config_manager.get_config(
            self.stitching_item.id.value, self.group and self.group.id.value
        )

    def _load_stitching_item(self) -> Zone | Pad:
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
                return selection
            case Group(id=group_id, name=name):
                zone_id = self.config_manager.zone_from_group(group_id.value)
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
                return items[0]
            case _:
                raise ValueError("Unknown item selected.")

    def _load_netclass(self, item: Pad | Zone) -> NetClass:
        if item.net is None:
            raise ValueError("Please assign a net to the selected item.")

        netclass = self.board.get_netclass_for_nets(item.net)[item.net.name]
        if netclass.via_diameter is None or netclass.via_drill is None:
            raise ValueError(
                f"Configure via diameter and hole size for netclass '{netclass.name}'."
            )

        return netclass

    def _load_existing_group(self) -> Group | None:
        groups = [
            g for g in self.board.get_groups() if g.name.startswith("Via Stitching")
        ]

        known_group_ids = self.config_manager.group_ids()
        for group in groups:
            if group.id.value not in known_group_ids:
                self.board.clear_selection()
                self.board.add_to_selection(group)
                raise ValueError(
                    "Found orphaned via stitching group. Please delete it."
                )

        group_configs = self.config_manager.group_configs(self.stitching_item.id.value)
        group_ids = [config.group_id for config in group_configs]
        return next((g for g in groups if g.id.value in group_ids), None)

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
        self.config_manager.update_config(
            self.config, self.stitching_item.id.value, self.group.id.value
        )
