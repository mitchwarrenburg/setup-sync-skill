"""setup-sync configuration: defaults from a small YAML file, overridable on the command line.

Only the YAML the config needs is read: nested mappings, lists of plain values, quoted or bare
strings, integers, null, booleans and comments. Anything else is an error rather than a guess, so a
typo cannot silently change which folders are written. No third-party package is required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXCLUDE_DIRS = ("Garage 61 - *",)       # other Garage 61 team shares are never sources
KNOWN_DEFAULTS = {"target-dirs", "clean", "exclude-dirs"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    target_dirs: tuple[str, ...]        # relative to each car folder, '/'-separated
    clean_past_seasons: int | None      # default for --clean; None: no cleaning unless asked
    exclude_dirs: tuple[str, ...]       # glob patterns relative to each car folder


def load_config(path: Path) -> Config:
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    unknown = set(data) - {"defaults"}
    if unknown:
        raise ConfigError(f"{path}: unknown top-level keys {sorted(unknown)}; everything lives under 'defaults'")
    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ConfigError(f"{path}: 'defaults' must be a mapping")
    unknown = set(defaults) - KNOWN_DEFAULTS
    if unknown:
        raise ConfigError(f"{path}: unknown keys under defaults: {sorted(unknown)}")
    targets = tuple(relative_dir(v, "defaults.target-dirs") for v in _list(defaults.get("target-dirs"),
                                                                              "defaults.target-dirs"))
    clean = defaults.get("clean")
    if clean is not None and not isinstance(clean, dict):
        raise ConfigError(f"{path}: 'defaults.clean' must be a mapping with 'past-seasons'")
    clean = clean or {}
    if set(clean) - {"past-seasons"}:
        raise ConfigError(f"{path}: unknown keys under defaults.clean: {sorted(set(clean) - {'past-seasons'})}")
    past = clean.get("past-seasons")
    if past is not None and (isinstance(past, bool) or not isinstance(past, int) or past < 1):
        raise ConfigError(f"{path}: defaults.clean.past-seasons must be a whole number of 1 or more, or null")
    excludes = defaults.get("exclude-dirs", list(DEFAULT_EXCLUDE_DIRS))
    excludes = tuple(relative_dir(v, "defaults.exclude-dirs") for v in _list(excludes, "defaults.exclude-dirs"))
    return Config(targets, past, excludes)


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
