#!/usr/bin/env python3
"""setup-sync: copy current-season iRacing setups into team folders, one flat folder per track.

Dry run by default; --apply writes. It is a sync: each setup lands directly in its track folder,
overwriting a same-named file there. Folders already inside the target folders are left alone.
Target folders and the default clean come from setup-sync.yaml; flags override them for one run.
Replaced and removed files go to the Recycle Bin; the provider folders remain the source.
Workflow and rules: ../SKILL.md.
"""
from __future__ import annotations

import argparse
import ctypes
import fnmatch
import glob
import hashlib
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
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
    """Move a file to the Windows Recycle Bin, so nothing a sync replaces or removes is destroyed."""
    name = str(path.resolve())
    names = ctypes.create_unicode_buffer(name, len(name) + 2)    # the API takes a double-NUL list
    operation = _FileOperation(None, FO_DELETE, ctypes.addressof(names), None, RECYCLE_FLAGS, 0, None, None)
    status = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if status or operation.fAnyOperationsAborted or path.exists():
        raise OSError(f"could not move {path} to the Recycle Bin (SHFileOperationW status {status:#x})")


discard = recycle   # the only way this tool removes a file; tests substitute Path.unlink


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

    Only a track folder's own files are read or written. Its subfolders, folders that do not name a
    track and loose files beside them are left exactly as they are.
    """

    def __init__(self, root: Path, table: TrackTable, extensions: tuple[str, ...]):
        self.table = table
        self.folders: dict[str, str] = {}               # canonical track -> existing folder name
        self.files: dict[str, dict[str, Path]] = {}     # track folder (lower) -> file name (lower) -> setup
        self.untouched: list[str] = []                  # folders that do not name a track
        candidates: dict[str, list[tuple[bool, int, str]]] = defaultdict(list)
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            match = table.resolve(child.name)
            canonical = (table.canonical(match.family, match.variant if match.explicit else None)
                         if match is not None and match.full else None)
            if canonical is None:
                self.untouched.append(child.name)
                continue
            setups = {p.name.lower(): p for p in child.iterdir() if p.is_file() and p.suffix.lower() in extensions}
            self.files[child.name.lower()] = setups
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


def iter_setups(car_dir: Path, extensions: tuple[str, ...], skip: list[str]):
    """Every setup under a car folder, except inside target and excluded folders (car-relative globs)."""
    for current, dirs, files in os.walk(car_dir):
        base = Path(current).relative_to(car_dir)
        dirs[:] = sorted(d for d in dirs
                         if not any(fnmatch.fnmatchcase((base / d).as_posix().lower(), pattern.lower())
                                    for pattern in skip))
        for name in sorted(files):
            if Path(name).suffix.lower() in extensions:
                yield Path(current) / name


def plan_car(car_dir: Path, targets: list[str], table: TrackTable, args) -> dict:
    current, season_start = args.season, args.season_start
    max_year = current.year + 1
    included: list[Source] = []
    excluded: list[tuple[str, str]] = []
    unresolved: list[Source] = []
    for path in iter_setups(car_dir, args.extensions, args.skip):
        relative = path.relative_to(car_dir).as_posix()
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
    missing = [target for target in targets if not (car_dir / target).is_dir()]
    in_season_names = {s.name.lower() for s in included + unresolved}
    cutoff = current.shifted(-args.clean) if args.clean else None
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
        clean: list[tuple[Path, str]] = []
        if cutoff is not None:
            for setups in index.files.values():
                for path in setups.values():
                    if path.name.lower() in in_season_names:
                        continue        # a current setup, whatever season its name carries
                    label = full_label(path.stem, max_year)
                    if label is not None and label <= cutoff:
                        clean.append((path, str(label)))
        team_plans.append({"team": target, "root": root, "untouched": index.untouched, "clean": clean})
    return {"car": car_dir.name, "dir": car_dir, "total": len(included) + len(excluded) + len(unresolved),
            "included": included, "excluded": excluded, "unresolved": unresolved, "actions": actions,
            "teams": team_plans, "notes": sorted(notes), "missing_teams": missing, "cutoff": cutoff}


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
        if team["clean"]:
            parts.append(f"{len(team['clean'])} to clean")
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
        if team["clean"]:
            where = Counter(path.parent.name for path, _ in team["clean"])
            print(f"     clean <= {plan['cutoff']}: " + ", ".join(f"{f} ({n})" for f, n in sorted(where.items())))
            if verbose:
                for path, season in team["clean"][:VERBOSE_LIST_LIMIT]:
                    print(f"        clean ({season}) {path.relative_to(root).as_posix()}")
        if verbose and team["untouched"]:
            print("     not track folders (left alone): " + ", ".join(team["untouched"]))
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
    """Copy, then clean. Every removal goes through `discard` (the Recycle Bin)."""
    done: Counter = Counter()
    for plan in plans:
        for action in plan["actions"]:
            if action.kind == "present":
                continue
            if not action.target.parent.is_dir():
                action.target.parent.mkdir()
                done["folders"] += 1
            if action.kind == "overwrite":
                discard(action.target)
            shutil.copy2(action.source.path, action.target)
            if sha256(action.target, fresh=True) != sha256(action.source.path):
                raise RuntimeError(f"copy verification failed: {action.target}")
            done[action.kind] += 1
        for team in plan["teams"]:
            for path, _ in team["clean"]:
                discard(path)
                done["cleaned"] += 1
            for folder in {path.parent for path, _ in team["clean"]}:
                if not any(folder.iterdir()):
                    folder.rmdir()          # a track folder the clean left empty
                    done["emptied"] += 1
    return done


def report_json(plan: dict) -> dict:
    def source(s: Source) -> dict:
        return {"relative": s.relative, "track": s.variant or s.family, "how": s.how, "season": s.season,
                "inferred": s.inferred, "note": s.note}
    return {"car": plan["car"], "missing_targets": plan["missing_teams"], "notes": plan["notes"],
            "excluded": [{"relative": r, "reason": why} for r, why in plan["excluded"]],
            "unresolved": [source(s) for s in plan["unresolved"]],
            "targets": [{"target": t["team"], "untouched": t["untouched"],
                         "clean": [[str(p), season] for p, season in t["clean"]]} for t in plan["teams"]],
            "actions": [{"kind": a.kind, "target": a.team, "folder": a.folder, "new_folder": a.new_folder,
                         "path": str(a.target), **source(a.source)} for a in plan["actions"]]}


def season_count(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more (1 keeps only the current season and newer)")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", required=True, type=parse_season, help="current season, e.g. 26S3")
    parser.add_argument("--season-start", type=date.fromisoformat,
                        help="first day of the current season (YYYY-MM-DD); dates year-only folders")
    cars = parser.add_mutually_exclusive_group(required=True)
    cars.add_argument("--car", action="append", help="car folder name; repeatable")
    cars.add_argument("--all", action="store_true", help="every car folder that has a target folder")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help=f"defaults file (default: {DEFAULT_CONFIG.name} beside SKILL.md)")
    parser.add_argument("--target-dir", action="append", metavar="DIR",
                        help="folder relative to each car folder to copy into; repeatable; "
                             "replaces defaults.target-dirs for this run")
    clean = parser.add_mutually_exclusive_group()
    clean.add_argument("--clean", type=season_count, metavar="N",
                       help="remove track-folder setups labelled N or more seasons before --season "
                            "(2 at 26S3 removes 26S1 and older); default: defaults.clean.past-seasons")
    clean.add_argument("--no-clean", action="store_true", help="skip the configured clean for this run")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="the iRacing setups folder")
    parser.add_argument("--ext", action="append", help="setup extension; default .sto")
    parser.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    parser.add_argument("--allow-unresolved", action="store_true", help="apply even if some files are unresolved")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--report", type=Path, help="write the full plan as JSON")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")    # provider names are not always encodable

    try:
        config = load_config(args.config)
        targets = ([relative_dir(t, "--target-dir") for t in args.target_dir] if args.target_dir
                   else list(config.target_dirs))
    except ConfigError as error:
        parser.error(str(error))
    if not targets:
        parser.error(f"no target folders: set defaults.target-dirs in {args.config} or pass --target-dir")
    clean_from = "flag" if args.clean else "config"
    if args.no_clean:
        args.clean = None
    elif args.clean is None:
        args.clean = config.clean_past_seasons
    args.skip = [glob.escape(target) for target in targets] + list(config.exclude_dirs)
    args.extensions = tuple(e.lower() if e.startswith(".") else f".{e.lower()}"
                            for e in (args.ext or SETUP_EXTENSIONS))
    table = TrackTable.load(TRACKS_FILE)
    if args.all:
        car_dirs = [d for d in sorted(args.root.iterdir()) if d.is_dir() and any((d / t).is_dir() for t in targets)]
    else:
        car_dirs = [args.root / name for name in args.car]
        for car in car_dirs:
            if not car.is_dir():
                parser.error(f"no car folder {car}")
    clean_note = f" | clean <= {args.season.shifted(-args.clean)} ({clean_from})" if args.clean else " | no clean"
    print(f"setup-sync {args.season} or newer{clean_note} | root {args.root} | targets {', '.join(targets)}"
          f" | {'APPLY' if args.apply else 'dry run'}")
    plans = [plan_car(car, targets, table, args) for car in car_dirs]
    for plan in plans:
        busy = any(t["clean"] for t in plan["teams"])
        if plan["included"] or plan["unresolved"] or busy or args.verbose or not args.all:
            print_plan(plan, args.verbose)
    totals = Counter(a.kind for p in plans for a in p["actions"])
    cleaned = sum(len(t["clean"]) for p in plans for t in p["teams"])
    blocked = sum(len(p["unresolved"]) for p in plans)
    print(f"\nTotal: {totals['copy']} to copy, {totals['overwrite']} to overwrite, {totals['present']} already "
          f"identical, {cleaned} to clean, {blocked} unresolved across {len(plans)} car(s).")
    if args.report:
        args.report.write_text(json.dumps([report_json(p) for p in plans], indent=2), encoding="utf-8")
        print(f"Report: {args.report}")
    if not args.apply:
        return 0
    if blocked and not args.allow_unresolved:
        print("Refusing to apply: resolve the unresolved files (SKILL.md, 'Inference') or pass --allow-unresolved.")
        return 2
    done = apply_plans(plans)
    print(f"Applied: {done['copy']} copied, {done['overwrite']} overwritten, {done['folders']} folders created; "
          f"to the Recycle Bin: {done['overwrite']} replaced, {done['cleaned']} cleaned; "
          f"{done['emptied']} emptied track folders removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
