import json
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from importlib.metadata import version
from pathlib import Path

from mashumaro.mixins.json import DataClassJSONMixin

try:
    from ._version import __version__
except ImportError:
    __version__ = version("via-tools")


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


class ConfigManager:
    def __init__(self, plugin_path: Path, project_path: Path):
        # Initialize plugin config
        self.plugin_config = Config()
        self.plugin_file = plugin_path / "config.json"
        if plugin_path.is_file():
            plugin_path.unlink()
        plugin_path.mkdir(exist_ok=True)
        if self.plugin_file.is_file():
            self.plugin_config = Config.from_json(self.plugin_file.read_text())

        # Initialise project config
        self.project_config = ProjectConfig()
        self.project_file = project_path / ".via-tools.json"
        if self.project_file.is_file():
            config = ProjectConfig.from_json(self.project_file.read_text())
            self.project_config.config_history = config.config_history

    def get_config(self, item_id: str, group_id) -> Config:
        config = self.plugin_config
        zone_config_history = self.project_config.config_history.get(item_id, [])
        if zone_config_history:
            config = next(
                (
                    group_config.config
                    for group_config in zone_config_history
                    if group_config.group_id == group_id
                ),
                zone_config_history[0].config,
            )

        return deepcopy(config)

    def update_config(self, config: Config, item_id: str, group_id: str) -> None:
        self.plugin_file.write_text(config.to_json())
        group_config = GroupConfig(deepcopy(config), group_id)
        self.project_config.add_history_entry(item_id, group_config)
        self.project_file.write_text(
            self.project_config.to_json(encoder=lambda x: json.dumps(x, indent=2))
        )

    def group_ids(self) -> list[str]:
        return [
            config.group_id
            for configs in self.project_config.config_history.values()
            for config in configs
        ]

    def group_configs(self, item_id: str) -> list[GroupConfig]:
        return self.project_config.config_history.get(item_id, [])

    def zone_from_group(self, group_id: str) -> str | None:
        return self.project_config.get_zone_by_group(group_id)
