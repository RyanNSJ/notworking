"""Loads the versioned option enums (and their steward-written descriptions) from options.yaml."""

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

import yaml

MAX_DESCRIPTION = 200


@dataclass(frozen=True)
class Option:
    value: str
    description: str


@dataclass(frozen=True)
class Options:
    version: int
    what_failed: tuple[Option, ...]
    agent_type: tuple[str, ...]
    target_type: tuple[str, ...]

    @property
    def what_failed_values(self) -> tuple[str, ...]:
        return tuple(o.value for o in self.what_failed)

    def what_failed_payload(self) -> list[dict[str, str]]:
        return [{"value": o.value, "description": o.description} for o in self.what_failed]


def _unique(key: str, values: list[str]) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"options.yaml: '{key}' contains duplicates")


def _enum(raw: dict[str, object], key: str) -> tuple[str, ...]:
    values = raw.get(key)
    if not isinstance(values, list) or not values:
        raise ValueError(f"options.yaml: '{key}' must be a non-empty list")
    if not all(isinstance(v, str) and v for v in values):
        raise ValueError(f"options.yaml: '{key}' must contain only non-empty strings")
    _unique(key, values)
    return tuple(values)


def _described(raw: dict[str, object], key: str) -> tuple[Option, ...]:
    items = raw.get(key)
    if not isinstance(items, list) or not items:
        raise ValueError(f"options.yaml: '{key}' must be a non-empty list")
    out = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"options.yaml: '{key}' entries need 'value' and 'description'")
        value, desc = item.get("value"), item.get("description")
        if not isinstance(value, str) or not value:
            raise ValueError(f"options.yaml: '{key}' entry has no value")
        if not isinstance(desc, str) or not desc.strip():
            raise ValueError(f"options.yaml: '{key}.{value}' has no description")
        if len(desc) > MAX_DESCRIPTION:
            raise ValueError(f"options.yaml: '{key}.{value}' description is over 200 characters")
        out.append(Option(value, desc.strip()))
    _unique(key, [o.value for o in out])
    return tuple(out)


def parse_options(text: str) -> Options:
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("options.yaml must be a mapping")
    version = raw.get("version")
    if not isinstance(version, int) or version < 1:
        raise ValueError("options.yaml: 'version' must be a positive integer")
    return Options(
        version=version,
        what_failed=_described(raw, "what_failed"),
        agent_type=_enum(raw, "agent_type"),
        target_type=_enum(raw, "target_type"),
    )


@lru_cache
def load_options() -> Options:
    text = files("agentdown.core").joinpath("options.yaml").read_text(encoding="utf-8")
    return parse_options(text)
