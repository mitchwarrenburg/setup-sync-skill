#!/usr/bin/env python3
"""setup-sync: copy current-season iRacing setups into team folders, one flat folder per track.

Applies to all cars by default; --car selects cars and --dry-run previews without writing setups.
It is a sync: each setup lands directly in its track folder,
overwriting a same-named file there. Folders already inside the target folders are left alone,
except that every track folder gets a Custom and an Archive folder: nothing is ever written to
Custom, and target cleanup moves old setups into Archive. Setups titled "fixed" are never copied.
Target folders and optional source/target cleanup come from setup-sync.yaml; flags override them
for one run. Replaced and removed files and folders go to the Recycle Bin.
Workflow and rules: ../SKILL.md.
"""
from __future__ import annotations

import argparse
import ctypes
import fnmatch
import hashlib
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_sync_config import ConfigError, load_config, relative_dir  # noqa: E402
from setup_sync_rules import (SeasonEvidence, TrackTable, decide_season,  # noqa: E402
                              full_label, parse_season, season_evidence)

SKILL_DIR = Path(__file__).resolve().parents[1]
TRACKS_FILE = SKILL_DIR / "tracks.json"
DEFAULT_CONFIG = SKILL_DIR / "setup-sync.yaml"
DEFAULT_ROOT = Path.home() / "Documents" / "iRacing" / "setups"
SETUP_EXTENSIONS = (".sto",)
HASH_CHUNK_BYTES = 1 << 20
VERBOSE_LIST_LIMIT = 40                 # file lines per car and section under --verbose
KINDS = ("copy", "overwrite", "present")
NEVER_COPIED = "fixed"                  # a setup titled this (any case) is never copied, wherever it is
CUSTOM_DIR = "Custom"                   # made in every track folder, then never touched
ARCHIVE_DIR = "Archive"                 # made in every track folder; target cleanup moves old setups here
FO_DELETE = 0x0003
RECYCLE_FLAGS = 0x0004 | 0x0010 | 0x0040 | 0x0400   # FOF_SILENT | NOCONFIRMATION | ALLOWUNDO | NOERRORUI

_DIGESTS: dict[Path, str] = {}


def sha256(path: Path, fresh: bool = False) -> str:
    """Content digest, cached per run; `fresh` re-reads a file this run has written."""
    if fresh or path not in _DIGESTS:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
        _DIGESTS[path] = digest.hexdigest()
    return _DIGESTS[path]


class _FileOperation(ctypes.Structure):
    """SHFILEOPSTRUCTW, naturally aligned as on 64-bit Windows."""
    _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_void_p),
                ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_void_p)]


def recycle(path: Path) -> None:
    """Move a file or directory to the Windows Recycle Bin."""
    name = str(path.resolve())
    names = ctypes.create_unicode_buffer(name, len(name) + 2)    # the API takes a double-NUL list
    operation = _FileOperation(None, FO_DELETE, ctypes.addressof(names), None, RECYCLE_FLAGS, 0, None, None)
    status = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if status or operation.fAnyOperationsAborted or path.exists():
        raise OSError(f"could not move {path} to the Recycle Bin (SHFileOperationW status {status:#x})")


discard = recycle   # the only way this tool removes files or directories; tests substitute a fake bin


def validate_cleanup_path(path: Path, car_dir: Path) -> None:
    """Reject car-root removal, escapes and links/junctions before traversal or recycling."""
    root = car_dir.resolve()
    resolved = path.resolve()
    if (resolved == root or not resolved.is_relative_to(root)
            or resolved != root / path.relative_to(car_dir)):
        raise ConfigError(f"cleanup path must stay inside {car_dir} without links or junctions: {path}")


def discard_inside_car(path: Path, car_dir: Path) -> None:
    validate_cleanup_path(path, car_dir)
    discard(path)


def archive_inside_car(path: Path, car_dir: Path) -> bool:
    """Move a target setup into its track folder's Archive folder, keeping its file date.

    A same-named archived copy is replaced and goes to the Recycle Bin, as the sync treats same
    names. Returns whether one was replaced.
    """
    destination = path.parent / ARCHIVE_DIR / path.name
    validate_cleanup_path(path, car_dir)
    validate_cleanup_path(destination, car_dir)
    replaced = destination.is_file()
    if replaced:
        discard(destination)
    path.rename(destination)        # never overwrites: a file that appeared meanwhile stops the run
    return replaced


@dataclass
class Source:
    path: Path
    relative: str
    modified: date
    season: str
    inferred: bool
    family: str | None
    variant: str | None
    how: str
    note: str

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class Action:
    kind: str           # copy (new name) | overwrite (same name, other bytes) | present (identical)
    team: str           # target folder, relative to the car folder
    folder: str         # track folder the file lands in, directly
    new_folder: bool
    source: Source
    target: Path


class TeamIndex:
    """One target folder of one car: its track folders and the setups directly inside them.

    Only a track folder's own files are read or written. Its subfolders (apart from creating Custom
    and Archive), folders that do not name a track and loose files beside them are left exactly as
    they are.
    """

    def __init__(self, root: Path, table: TrackTable, extensions: tuple[str, ...]):
        self.table = table
        self.folders: dict[str, str] = {}               # canonical track -> existing folder name
        self.files: dict[str, dict[str, Path]] = {}     # track folder (lower) -> file name (lower) -> setup
        self.tracks: list[str] = []                     # every existing track folder, named as on disk
        self.untouched: list[str] = []                  # folders that do not name a track
        candidates: dict[str, list[tuple[bool, int, str]]] = defaultdict(list)
        own_names = {name.lower(): name for name in table.destinations}
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            match = table.resolve(child.name)
            canonical = (table.canonical(match.family, match.variant if match.explicit else None)
                         if match is not None and match.full else None)
            # A folder named as this tool names a track is one, even when that name alone only
            # suggests a layout in provider names ("Nordschleife"): folder_for() writes into it.
            canonical = canonical or own_names.get(child.name.lower())
            if canonical is None:
                self.untouched.append(child.name)
                continue
            setups = {p.name.lower(): p for p in child.iterdir() if p.is_file() and p.suffix.lower() in extensions}
            self.files[child.name.lower()] = setups
            self.tracks.append(child.name)
            candidates[canonical].append((child.name != canonical, -len(setups), child.name))
        for canonical, options in candidates.items():
            self.folders[canonical] = sorted(options)[0][2]    # exact spelling, then fullest, then name

    def folder_for(self, family: str, variant: str | None) -> str:
        """Existing folder for a layout, else the canonical name to create."""
        if variant is not None:
            if variant in self.folders:
                return self.folders[variant]
            if self.table.families[family].variants[variant] == "always":
                return self.folders.setdefault(variant, variant)
        base = self.table.canonical(family, None)
        return self.folders.setdefault(base, base)


def iter_setups(car_dir: Path, extensions: tuple[str, ...], targets: list[str]):
    """Every setup under a car folder, except inside the exact target directories."""
    skip = {target.lower() for target in targets}
    for current, dirs, files in os.walk(car_dir):
        base = Path(current).relative_to(car_dir)
        dirs[:] = sorted(d for d in dirs if (base / d).as_posix().lower() not in skip)
        for name in sorted(files):
            if Path(name).suffix.lower() in extensions:
                yield Path(current) / name


def matches_glob(relative: str, pattern: str) -> bool:
    """Case-insensitive, car-relative glob: * / ? / [] within a name, ** across directories."""
    parts, patterns = relative.lower().split("/"), pattern.lower().split("/")

    @lru_cache(maxsize=None)
    def match(part: int, item: int) -> bool:
        if item == len(patterns):
            return part == len(parts)
        if patterns[item] == "**":
            return match(part, item + 1) or (part < len(parts) and match(part + 1, item))
        return (part < len(parts) and fnmatch.fnmatchcase(parts[part], patterns[item])
                and match(part + 1, item + 1))

    return match(0, 0)


def source_cleanup_paths(car_dir: Path, targets: list[str], excludes: list[str]) -> list[Path]:
    """Plan whole subtrees where possible, preserving targets, glob matches and their ancestors."""
    protected = {target.lower() for target in targets}

    def visit(path: Path) -> tuple[list[Path], bool]:
        relative = path.relative_to(car_dir).as_posix()
        if relative.lower() in protected or any(matches_glob(relative, pattern) for pattern in excludes):
            return [], True
        validate_cleanup_path(path, car_dir)
        removals: list[Path] = []
        keep = False
        if path.is_dir():
            for child in sorted(path.iterdir()):
                child_removals, child_kept = visit(child)
                removals.extend(child_removals)
                keep |= child_kept
        return (removals, True) if keep else ([path], False)

    return [path for child in sorted(car_dir.iterdir()) for path in visit(child)[0]]


def plan_car(car_dir: Path, targets: list[str], table: TrackTable, args) -> dict:
    current, season_start = args.season, args.season_start
    max_year = current.year + 1
    missing = [target for target in targets if not (car_dir / target).is_dir()]
    clean_source = (source_cleanup_paths(car_dir, targets, args.clean_source_exclude)
                    if args.clean_source and len(missing) < len(targets) else [])
    included: list[Source] = []
    excluded: list[tuple[str, str]] = []
    unresolved: list[Source] = []
    for path in iter_setups(car_dir, args.extensions, targets):
        relative = path.relative_to(car_dir).as_posix()
        if path.stem.strip().lower() == NEVER_COPIED:
            excluded.append((relative, f"titled '{NEVER_COPIED}': never copied"))
            continue
        folders, stem = list(Path(relative).parts[:-1]), path.stem
        rule = table.rule_for(relative)
        if rule and rule.get("skip"):
            excluded.append((relative, f"rule: {rule.get('why', 'skip')}"))
            continue
        if rule and rule.get("season"):
            evidence = SeasonEvidence(season=parse_season(rule["season"]), source="rule")
        else:
            flags = [table.is_track_folder(folder) for folder in folders]
            evidence = season_evidence(folders, stem, flags, max_year)
        modified = date.fromtimestamp(path.stat().st_mtime)
        decision = decide_season(evidence, current, season_start, modified)
        if not decision.include:
            excluded.append((relative, decision.reason))
            continue
        where = table.resolve_file(folders, stem)
        source = Source(path, relative, modified, decision.reason, decision.inferred,
                        where.family, where.variant, where.how, where.note)
        if where.family is None or table.canonical(where.family, where.variant) is None:
            unresolved.append(source)
        else:
            included.append(source)

    notes: set[str] = set()
    actions: list[Action] = []
    team_plans: list[dict] = []
    in_season_names = {s.name.lower() for s in included + unresolved}
    cutoff = current.shifted(-args.clean_target_seasons) if args.clean_target else None
    for target in targets:
        if target in missing:
            continue
        root = car_dir / target
        index = TeamIndex(root, table, args.extensions)
        planned: dict[str, tuple[Source, str]] = {}
        for source in sorted(included, key=lambda s: s.relative):
            folder = index.folder_for(source.family, source.variant)
            key = f"{folder}/{source.name}".lower()
            if key in planned:      # two providers ship one name into one folder: the newest wins
                kept = planned[key][0]
                winner = max((kept, source), key=lambda s: (s.modified, s.relative))
                if sha256(kept.path) != sha256(source.path):
                    loser = source if winner is kept else kept
                    notes.add(f"{folder}/{source.name}: newest {winner.relative} over {loser.relative}")
                source = winner
            planned[key] = (source, folder)
        for source, folder in planned.values():
            existing = index.files.get(folder.lower(), {}).get(source.name.lower())
            if existing is None:
                actions.append(Action("copy", target, folder, not (root / folder).is_dir(), source,
                                      root / folder / source.name))
            else:
                kind = "present" if sha256(existing) == sha256(source.path) else "overwrite"
                actions.append(Action(kind, target, folder, False, source, existing))
        new_tracks = sorted({a.folder for a in actions if a.team == target and a.new_folder})
        create = [root / track / name for track in [*index.tracks, *new_tracks]
                  for name in (CUSTOM_DIR, ARCHIVE_DIR) if not (root / track / name).is_dir()]
        for path in create:
            if path.exists():
                raise ConfigError(f"{path} must be a folder: rename or move that file")
        archive: list[tuple[Path, str, bool]] = []      # setup, its label, replaces an archived copy
        if cutoff is not None:
            for setups in index.files.values():
                for path in setups.values():
                    if path.name.lower() in in_season_names:
                        continue        # a current setup, whatever season its name carries
                    label = full_label(path.stem, max_year)
                    if label is not None and label <= cutoff:
                        destination = path.parent / ARCHIVE_DIR / path.name
                        validate_cleanup_path(path, car_dir)
                        validate_cleanup_path(destination, car_dir)
                        archive.append((path, str(label), destination.is_file()))
        team_plans.append({"team": target, "root": root, "untouched": index.untouched, "create": create,
                           "archive": archive})
    return {"car": car_dir.name, "dir": car_dir, "total": len(included) + len(excluded) + len(unresolved),
            "included": included, "excluded": excluded, "unresolved": unresolved, "actions": actions,
            "teams": team_plans, "notes": sorted(notes), "missing_teams": missing, "cutoff": cutoff,
            "clean_source": clean_source}


def print_plan(plan: dict, verbose: bool) -> None:
    included, excluded, unresolved = plan["included"], plan["excluded"], plan["unresolved"]
    inferred = sum(1 for s in included if s.inferred)
    print(f"\n== {plan['car']}: {plan['total']} setups | in season {len(included)}"
          f"{f' ({inferred} dated by file time)' if inferred else ''} | excluded {len(excluded)}"
          f" | unresolved {len(unresolved)}")
    for target in plan["missing_teams"]:
        print(f"   ! no '{target}' folder; skipped")
    by_team: dict[str, list[Action]] = defaultdict(list)
    for action in plan["actions"]:
        by_team[action.team].append(action)
    for team in plan["teams"]:
        root, actions = team["root"], by_team.get(team["team"], [])
        kinds = Counter(a.kind for a in actions)
        parts = [f"{kinds[k]} {k}" for k in KINDS if kinds[k]]
        if team["archive"]:
            replacing = sum(1 for *_, replaces in team["archive"] if replaces)
            parts.append(f"{len(team['archive'])} to archive"
                         + (f" ({replacing} replacing an archived copy)" if replacing else ""))
        if team["create"]:
            parts.append(f"{len(team['create'])} Custom/Archive folders to create")
        print(f"   {team['team']}: " + (", ".join(parts) or "nothing to do"))
        folders: dict[str, Counter] = defaultdict(Counter)
        new_folders = {a.folder for a in actions if a.new_folder}
        for action in actions:
            folders[action.folder][action.kind] += 1
        for folder in sorted(folders):
            counts = folders[folder]
            if not (counts["copy"] or counts["overwrite"]) and not verbose:
                continue
            label = f"{folder} (new)" if folder in new_folders else folder
            print(f"     {label:<24} " + ", ".join(f"{counts[k]} {k}" for k in KINDS if counts[k]))
            if verbose:
                for action in actions:
                    if action.folder == folder and action.kind != "present":
                        print(f"        {action.kind:<9} {action.source.relative}  [{action.source.how}]")
        if team["archive"]:
            where = Counter(path.parent.name for path, *_ in team["archive"])
            print(f"     archive <= {plan['cutoff']}: " + ", ".join(f"{f} ({n})" for f, n in sorted(where.items())))
            if verbose:
                for path, season, replaces in team["archive"][:VERBOSE_LIST_LIMIT]:
                    print(f"        archive ({season}) {path.relative_to(root).as_posix()}"
                          f"{' (replaces the archived copy)' if replaces else ''}")
        if verbose:
            for path in team["create"][:VERBOSE_LIST_LIMIT]:
                print(f"        create {path.relative_to(root).as_posix()}")
        if verbose and team["untouched"]:
            print("     not track folders (left alone): " + ", ".join(team["untouched"]))
    if plan["clean_source"]:
        print(f"   clean source: {len(plan['clean_source'])} files/folders to the Recycle Bin after copying")
        for path in plan["clean_source"]:
            print(f"     recycle {path.relative_to(plan['dir']).as_posix()}")
    for line in plan["notes"]:
        print(f"   ! same name from two sources: {line}")
    for source in unresolved:
        needs = "unknown track" if source.family is None else f"{source.family} layout undetermined"
        print(f"   ? unresolved ({needs}): {source.relative}")
    if verbose:
        for source in included:
            if source.inferred or source.how not in ("folder", "rule"):
                print(f"   ~ {source.relative} -> {source.variant or source.family} [{source.how}; {source.season}]"
                      f"{f' {source.note}' if source.note else ''}")
        reasons = Counter(reason.split(" from ")[0] for _, reason in excluded)
        print("   excluded: " + "; ".join(f"{count} x {reason}" for reason, count in reasons.most_common()))
        for relative, reason in excluded[:VERBOSE_LIST_LIMIT]:
            print(f"     - {relative}: {reason}")


def apply_plans(plans: list[dict]) -> Counter:
    """Verify all copies before creating Custom/Archive folders and archiving old target setups, then
    clean sources. Every removal uses the Recycle Bin."""
    done: Counter = Counter()
    for plan in plans:
        for action in plan["actions"]:
            if action.kind == "present":
                continue
            if not action.target.parent.is_dir():
                action.target.parent.mkdir()
                done["folders"] += 1
            if action.kind == "overwrite":
                discard_inside_car(action.target, plan["dir"])
            shutil.copy2(action.source.path, action.target)
            if sha256(action.target, fresh=True) != sha256(action.source.path):
                raise RuntimeError(f"copy verification failed: {action.target}")
            done[action.kind] += 1
    for plan in plans:
        for team in plan["teams"]:
            for folder in team["create"]:
                folder.mkdir(exist_ok=True)
                done["scaffolded"] += 1
            for path, _, _ in team["archive"]:
                if archive_inside_car(path, plan["dir"]):
                    done["replaced_archive"] += 1
                done["archived"] += 1
    for plan in plans:
        for path in plan["clean_source"]:
            discard_inside_car(path, plan["dir"])
            done["cleaned_source"] += 1
    return done


def report_json(plan: dict) -> dict:
    def source(s: Source) -> dict:
        return {"relative": s.relative, "track": s.variant or s.family, "how": s.how, "season": s.season,
                "inferred": s.inferred, "note": s.note}
    return {"car": plan["car"], "missing_targets": plan["missing_teams"], "notes": plan["notes"],
            "clean_source": [str(p) for p in plan["clean_source"]],
            "excluded": [{"relative": r, "reason": why} for r, why in plan["excluded"]],
            "unresolved": [source(s) for s in plan["unresolved"]],
            "targets": [{"target": t["team"], "untouched": t["untouched"], "create": [str(p) for p in t["create"]],
                         "archive": [{"path": str(p), "season": season, "replaces_archived_copy": replaces}
                                     for p, season, replaces in t["archive"]]} for t in plan["teams"]],
            "actions": [{"kind": a.kind, "target": a.team, "folder": a.folder, "new_folder": a.new_folder,
                         "path": str(a.target), **source(a.source)} for a in plan["actions"]]}


def season_count(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more (1 keeps only the current season and newer)")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--season", required=True, type=parse_season, help="current season, e.g. 26S3")
    parser.add_argument("--season-start", type=date.fromisoformat,
                        help="first day of the current season (YYYY-MM-DD); dates year-only folders")
    cars = parser.add_mutually_exclusive_group()
    cars.add_argument("--car", action="append", help="car folder name; repeatable")
    cars.add_argument("--all", action="store_true", help="every car folder that has a target folder (default)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help=f"defaults file (default: {DEFAULT_CONFIG.name} beside SKILL.md)")
    parser.add_argument("--target-dir", action="append", metavar="DIR",
                        help="folder relative to each car folder to copy into; repeatable; "
                             "replaces defaults.target-dirs for this run")
    parser.add_argument("--clean-source", action="store_true",
                        help="enable recycling everything outside target dirs except clean-source.exclude matches")
    parser.add_argument("--clean-source-exclude", action="append", metavar="GLOB",
                        help="car-relative glob to preserve during source cleanup; repeatable; "
                             "replaces defaults.clean-source.exclude for this run")
    parser.add_argument("--clean-target", action="store_true",
                        help="enable moving old setups directly inside target track folders into "
                             "each track folder's Archive folder")
    parser.add_argument("--clean-target-seasons", type=season_count, metavar="N",
                        help="archive target setups labelled N or more seasons before --season "
                             "(2 at 26S3 archives 26S1 and older); overrides "
                             "defaults.clean-target.past-season-count (default 2); does not enable cleanup")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="the iRacing setups folder")
    parser.add_argument("--ext", action="append", help="setup extension; default .sto")
    parser.add_argument("--dry-run", action="store_true",
                        help="preview copies, overwrites, new folders and cleanup without changing setups "
                             "(default: apply)")
    parser.add_argument("--allow-unresolved", action="store_true", help="apply even if some files are unresolved")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--report", type=Path, help="write the full plan as JSON")
    args = parser.parse_args(argv)
    all_cars = args.car is None
    _DIGESTS.clear()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")    # provider names are not always encodable

    try:
        config = load_config(args.config)
        targets = ([relative_dir(t, "--target-dir") for t in args.target_dir] if args.target_dir
                   else list(config.target_dirs))
        args.clean_source_exclude = ([relative_dir(p, "--clean-source-exclude") for p in args.clean_source_exclude]
                                     if args.clean_source_exclude is not None else list(config.clean_source_exclude))
    except ConfigError as error:
        parser.error(str(error))
    if not targets:
        parser.error(f"no target folders: set defaults.target-dirs in {args.config} or pass --target-dir")
    source_from = "flag" if args.clean_source else "config"
    target_from = "flag" if args.clean_target else "config"
    seasons_from = "flag" if args.clean_target_seasons is not None else "config"
    args.clean_source = args.clean_source or config.clean_source_enabled
    args.clean_target = args.clean_target or config.clean_target_enabled
    if args.clean_target_seasons is None:
        args.clean_target_seasons = config.clean_target_past_season_count
    args.extensions = tuple(e.lower() if e.startswith(".") else f".{e.lower()}"
                            for e in (args.ext or SETUP_EXTENSIONS))
    table = TrackTable.load(TRACKS_FILE)
    if all_cars:
        car_dirs = [d for d in sorted(args.root.iterdir()) if d.is_dir() and any((d / t).is_dir() for t in targets)]
    else:
        car_dirs = [args.root / name for name in args.car]
        for car in car_dirs:
            if not car.is_dir():
                parser.error(f"no car folder {car}")
    clean_note = (f" | clean source ({source_from})" if args.clean_source else " | no source cleanup")
    clean_note += (f" | archive target <= {args.season.shifted(-args.clean_target_seasons)} "
                   f"({target_from}; season count from {seasons_from})" if args.clean_target else " | no target cleanup")
    print(f"setup-sync {args.season} or newer{clean_note} | root {args.root} | targets {', '.join(targets)}"
          f" | {'dry run' if args.dry_run else 'APPLY'}")
    try:
        plans = [plan_car(car, targets, table, args) for car in car_dirs]
    except ConfigError as error:
        parser.error(str(error))
    for plan in plans:
        busy = plan["clean_source"] or any(t["archive"] for t in plan["teams"])
        if plan["included"] or plan["unresolved"] or busy or args.verbose or not all_cars:
            print_plan(plan, args.verbose)
    totals = Counter(a.kind for p in plans for a in p["actions"])
    archived = sum(len(t["archive"]) for p in plans for t in p["teams"])
    created = sum(len(t["create"]) for p in plans for t in p["teams"])
    cleaned_source = sum(len(p["clean_source"]) for p in plans)
    blocked = sum(len(p["unresolved"]) for p in plans)
    print(f"\nTotal: {totals['copy']} to copy, {totals['overwrite']} to overwrite, {totals['present']} already "
          f"identical, {archived} target setups to archive, {created} Custom/Archive folders to create, "
          f"{cleaned_source} source files/folders to clean, {blocked} unresolved across {len(plans)} car(s).")
    if args.report:
        args.report.write_text(json.dumps([report_json(p) for p in plans], indent=2), encoding="utf-8")
        print(f"Report: {args.report}")
    if args.dry_run:
        return 0
    if blocked and not args.allow_unresolved:
        print("Refusing to apply: resolve the unresolved files (SKILL.md, 'Inference') or pass --allow-unresolved.")
        return 2
    done = apply_plans(plans)
    print(f"Applied: {done['copy']} copied, {done['overwrite']} overwritten, {done['folders']} track folders and "
          f"{done['scaffolded']} Custom/Archive folders created, {done['archived']} target setups archived; "
          f"to the Recycle Bin: {done['overwrite']} replaced, {done['replaced_archive']} archived copies replaced, "
          f"{done['cleaned_source']} source files/folders cleaned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
