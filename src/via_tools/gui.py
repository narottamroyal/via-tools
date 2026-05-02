import math
import dearpygui.dearpygui as dpg

from contextlib import contextmanager
from kipy.geometry import Box2, Vector2
from kipy.util import units

from .config import Pattern
from .engine import generate_positions, ViaTools


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
