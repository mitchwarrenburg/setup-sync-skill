# setup-sync-skill

A Claude Code and Codex skill that keeps iRacing team setup folders current. Setup providers
(Garage 61 data packs/Grid-and-Go, P1Doks, Track Titan/HYMO, Coach Dave Academy, VRS, GO, ARA,
MG) each install into their own layout under `Documents\iRacing\setups\<car>`. setup-sync reads
all of them, keeps the current season or newer, works out each file's track and layout, and copies
it directly into one flat folder per track inside each configured target folder (by default two
Garage 61 team folders), so every teammate finds the same setups in the same place. Setups titled
`fixed` (`fixed.sto`, any case) are never copied.

The agent does the parts that need judgement: it looks up the current season on the web and
fixes every unrecognized name in `tracks.json`, so the next run is automatic. It adds clear
aliases (`Barca`) and new tracks (`Cadwell Park`) itself, and asks you about edge cases such as
event codes (`PLM`).

## Requirements

- Windows (iRacing's setups folder; replaced or removed files go to the Recycle Bin)
- Python 3.10 or newer; no packages

## Install

```powershell
python install.py
```

This writes a small entry point for each agent that points back at this checkout. There is one
copy of the skill, its config and its learned track vocabulary:

- Claude Code: `~/.claude/skills/setup-sync/SKILL.md`
- Codex: `~/.agents/skills/setup-sync/SKILL.md`

Run it again after moving the checkout; `python install.py --uninstall` removes the entry points.

## Configure

Edit [setup-sync.yaml](setup-sync.yaml). Folders are relative to each car folder:

```yaml
defaults:
  target-dirs:
    - "/Garage 61 - Rasengrasen Racing"
    - "/Garage 61 - Eclipse Motorsport"
  clean-source:
    enabled: true
    exclude:
      - "/Garage 61*"
      - "/P1Doks"
      - "/Track Titan"
  clean-target:
    enabled: true
    past-season-count: 2
```

`--target-dir DIR` (repeatable) replaces the targets for one run. Only these target directories
are excluded from source scanning; other team shares and cleanup exclusions remain sources.

Cleanup modes run when enabled in the config or by their flags. `--dry-run` previews them
without changing setup files:

- `--clean-source` enables recycling everything in each car folder outside its targets, except
  paths matching `clean-source.exclude`. This includes loose files, non-setup files and whole
  provider folders, after all copies have been verified. `--clean-source-exclude GLOB` is
  repeatable and replaces the configured exclusion list for that run.
- `--clean-target` enables moving old setups directly in target track folders into each track
  folder's `Archive` folder. `--clean-target-seasons N` overrides
  `clean-target.past-season-count` (default 2): at 26S3, 2 archives 26S1 and older; 1 archives
  26S2 and older. The count alone does not enable cleanup.

Every track folder in a target gets a `Custom` and an `Archive` folder when it lacks one, whether
or not cleanup is enabled. setup-sync never writes to, moves from or removes anything in `Custom`.

Set a mode's `enabled` setting to `false` to disable it unless its flag is passed. Exclusion globs are
case-insensitive and relative to the car folder; a leading `/` is optional. `*`, `?` and `[]`
match within a name, and `**` matches zero or more directories. A matching folder protects
its whole subtree. Nested targets and matches keep their parent folders. Use `exclude: []`
for no exclusions.

The former `defaults.clean`, `defaults.exclude-dirs`, `--clean`, `--no-clean` and `--apply` are removed.

## Use

Type `/setup-sync` to sync every car that has a configured target folder and apply the changes,
including enabled cleanup. The agent looks up the current season and completes the run without
asking for scope or apply confirmation. Use `/setup-sync --dry-run` for a preview, or
`/setup-sync --car ferrari296gt3` to limit the run to one car.

The helper also applies to all eligible cars by default. `--car` selects particular cars
(repeatable), `--all` explicitly selects the default scope, and `--dry-run` previews changes:

```powershell
python scripts/setup_sync.py --season 26S3 --season-start 2026-06-16
python scripts/setup_sync.py --season 26S3 --season-start 2026-06-16 --dry-run
python scripts/setup_sync.py --season 26S3 --season-start 2026-06-16 --car ferrari296gt3
python scripts/setup_sync.py --season 26S3 --season-start 2026-06-16 --dry-run --clean-source --clean-target --clean-target-seasons 2
```

[SKILL.md](SKILL.md) documents the whole workflow, the season and track rules, and how cleaning
protects current and unlabelled setups.

## Test

```powershell
python -m unittest discover -s scripts -p test_setup_sync.py
```
