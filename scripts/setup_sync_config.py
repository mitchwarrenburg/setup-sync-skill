"""setup-sync configuration: defaults from a small YAML file, overridable on the command line.

Only the YAML the config needs is read: nested mappings, lists of plain values, quoted or bare
strings, integers, null, booleans and comments. Anything else is an error rather than a guess, so a
typo cannot silently change which folders are written. No third-party package is required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CLEAN_TARGET_SEASONS = 2
KNOWN_DEFAULTS = {"target-dirs", "clean-source", "clean-target"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    target_dirs: tuple[str, ...]        # relative to each car folder, '/'-separated
    clean_source_enabled: bool
    clean_source_exclude: tuple[str, ...]  # cleanup protection only; these remain source candidates
    clean_target_enabled: bool
    clean_target_past_season_count: int


def load_config(path: Path) -> Config:
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    unknown = set(data) - {"defaults"}
    if unknown:
        raise ConfigError(f"{path}: unknown top-level keys {sorted(unknown)}; everything lives under 'defaults'")
    defaults = _mapping(data.get("defaults"), "defaults", KNOWN_DEFAULTS)
    targets = tuple(relative_dir(v, "defaults.target-dirs") for v in _list(defaults.get("target-dirs"),
                                                                              "defaults.target-dirs"))
    source = _mapping(defaults.get("clean-source"), "defaults.clean-source", {"enabled", "exclude"})
    target = _mapping(defaults.get("clean-target"), "defaults.clean-target", {"enabled", "past-season-count"})
    source_enabled = _enabled(source.get("enabled", False), "defaults.clean-source.enabled")
    target_enabled = _enabled(target.get("enabled", False), "defaults.clean-target.enabled")
    past = target.get("past-season-count", DEFAULT_CLEAN_TARGET_SEASONS)
    if isinstance(past, bool) or not isinstance(past, int) or past < 1:
        raise ConfigError(f"{path}: defaults.clean-target.past-season-count must be a whole number of 1 or more")
    excludes = tuple(relative_dir(v, "defaults.clean-source.exclude")
                     for v in _list(source.get("exclude"), "defaults.clean-source.exclude"))
    return Config(targets, source_enabled, excludes, target_enabled, past)


def _mapping(value: object, where: str, keys: set[str]) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a mapping")
    unknown = set(value) - keys
    if unknown:
        raise ConfigError(f"unknown keys under {where}: {sorted(unknown)}")
    return value


def _enabled(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: expected true or false")
    return value


def relative_dir(value: object, where: str = "target dir") -> str:
    """'/Garage 61 - Eclipse Motorsport' -> 'Garage 61 - Eclipse Motorsport', relative to the car folder."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: expected a folder path, got {value!r}")
    text = value.strip().replace("\\", "/")
    if re.match(r"^[A-Za-z]:", text) or text.startswith("//"):
        raise ConfigError(f"{where}: {value!r} must be relative to the car folder, like \"/Garage 61 - My Team\"")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise ConfigError(f"{where}: {value!r} must name a folder inside the car folder")
    return "/".join(parts)


def _list(value: object, where: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{where}: expected a list of folders")
    return value


# --------------------------------------------------------------------------- YAML subset

def parse_yaml(text: str) -> dict:
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        body = _strip_comment(raw).rstrip()
        if not body.strip():
            continue
        indent = len(body) - len(body.lstrip(" "))
        if body[indent] == "\t":
            raise ConfigError(f"line {number}: indent with spaces, not tabs")
        lines.append((number, indent, body.strip()))
    if not lines:
        return {}
    value, end = _block(lines, 0, lines[0][1])
    if end != len(lines):
        raise ConfigError(f"line {lines[end][0]}: unexpected indentation")
    if not isinstance(value, dict):
        raise ConfigError("the top level must be a mapping")
    return value


def _is_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _block(lines: list[tuple[int, int, str]], start: int, indent: int) -> tuple[object, int]:
    if _is_item(lines[start][2]):
        items, index = [], start
        while index < len(lines) and lines[index][1] == indent and _is_item(lines[index][2]):
            number, _, text = lines[index]
            item = text[1:].strip()
            if item and item[0] not in "\"'" and ": " in item:
                raise ConfigError(f"line {number}: list items are plain values here, not mappings")
            items.append(_scalar(item, number))
            index += 1
        return items, index
    mapping: dict[str, object] = {}
    index = start
    while index < len(lines) and lines[index][1] == indent:
        number, _, text = lines[index]
        key, colon, rest = text.partition(":")
        key = key.strip()
        if _is_item(text) or not colon or not key:
            raise ConfigError(f"line {number}: expected 'key: value'")
        if key in mapping:
            raise ConfigError(f"line {number}: duplicate key {key!r}")
        index += 1
        if rest.strip():
            mapping[key] = _scalar(rest.strip(), number)
        elif index < len(lines) and (lines[index][1] > indent or
                                     (lines[index][1] == indent and _is_item(lines[index][2]))):
            mapping[key], index = _block(lines, index, lines[index][1])
        else:
            mapping[key] = None
    return mapping, index


def _scalar(text: str, number: int) -> object:
    if not text:
        return None
    if text == "[]":
        return []
    if text[0] in "\"'":
        quote = text[0]
        if len(text) < 2 or text[-1] != quote:
            raise ConfigError(f"line {number}: unterminated string {text!r}")
        inner = text[1:-1]
        return inner.replace("''", "'") if quote == "'" else re.sub(r'\\(["\\])', r"\1", inner)
    if text in ("~", "null", "Null", "NULL"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    if text[0] in "[{&*!|>%@`":
        raise ConfigError(f"line {number}: {text!r} uses YAML this config does not read; quote it")
    return text


def _strip_comment(line: str) -> str:
    """Drop a '#' comment that starts a line or follows whitespace, outside quotes."""
    quote = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'" and (index == 0 or line[index - 1] in " \t:-"):
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
    return line
