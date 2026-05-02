from dataclasses import dataclass, field
from enum import Enum
from importlib.metadata import version

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
