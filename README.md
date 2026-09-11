# setup-sync-skill

A Claude Code and Codex skill that keeps iRacing team setup folders current. Setup providers
(Garage 61 data packs/Grid-and-Go, P1Doks, Track Titan/HYMO, Coach Dave Academy, VRS, GO, ARA,
MG) each install into their own layout under `Documents\iRacing\setups\<car>`. setup-sync reads
all of them, keeps the current season or newer, works out each file's track and layout, and copies
it directly into one flat folder per track inside each configured target folder (by default two
Garage 61 team folders), so every teammate finds the same setups in the same place.

The agent does the parts that need judgement: it looks up the current season on the web, and
records any new track spelling or layout decision in `tracks.json`, so the next run is automatic.

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
  clean:
    past-seasons: 2
  exclude-dirs:
    - "/Garage 61 - *"
```

`--target-dir` replaces the targets for one run, `--clean N` overrides the clean and
`--no-clean` skips it.

## Use

Ask the agent to sync your iRacing setups (for one car first), or run the helper yourself. It is
a dry run unless you pass `--apply`:

```powershell
python scripts/setup_sync.py --season 26S3 --season-start 2026-06-16 --car ferrari296gt3
python scripts/setup_sync.py --season 26S3 --season-start 2026-06-16 --all --apply
```

[SKILL.md](SKILL.md) documents the whole workflow, the season and track rules, and how cleaning
protects current and unlabelled setups.

## Test

```powershell
python -m unittest discover -s scripts -p test_setup_sync.py
```
