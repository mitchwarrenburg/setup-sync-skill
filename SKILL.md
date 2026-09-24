---
name: setup-sync
description: Sync current-season iRacing car setups from every provider folder (Garage 61 data packs/GnG, P1Doks, Track Titan/HYMO, Coach Dave Academy, VRS, GO, ARA, MG and iRacing track folders) into configured target folders inside each car folder (by default the Garage 61 team folders), directly into one folder per track. A bare /setup-sync applies changes and configured cleanup to all eligible cars; --dry-run previews and --car selects cars. Finds the current iRacing season by web search, keeps that season or newer, infers track and layout from folder and file names, and records new inferences so the next run is automatic. Use when asked to sync, centralize, share, copy or clean iRacing setups in the team folders.
---

# Sync iRacing setups into the team folders

Every car folder under the iRacing setups folder (`%USERPROFILE%\Documents\iRacing\setups`) holds
setups from several providers in several layouts. This skill copies the current season's `.sto`
files into each configured target folder of the same car, directly into one folder per track:

```text
setups\ferrari296gt3\P1Doks\suzuka\2026-S3\P1Doks_296GT3_Suzuka_GTS_Q_26S3W12.sto
  -> setups\ferrari296gt3\Garage 61 - Eclipse Motorsport\Suzuka\P1Doks_296GT3_Suzuka_GTS_Q_26S3W12.sto
  -> setups\ferrari296gt3\Garage 61 - Rasengrasen Racing\Suzuka\P1Doks_296GT3_Suzuka_GTS_Q_26S3W12.sto
```

`<SKILL_DIR>` below is the folder that holds this file. The helper does everything it can decide
from names alone, and says how it decided. You handle what it cannot: the season lookup and any
file it reports as unresolved.

## Invocation defaults

A bare `/setup-sync` means **apply the sync and configured cleanup for all cars that have a
target folder**. Look up the season, inspect the plan, fix every unresolved name in `tracks.json`,
then finish applying the changes. This invocation authorizes the run: do not ask for a car
selection or an apply confirmation, and do not stop after the internal preview. The only question
a run asks is how to place an edge-case name (see Inference).

Honor options supplied after the skill name. `--car NAME` (repeatable) limits the scope; `--all`
explicitly selects the default scope. If the user passes `--dry-run` or asks only for a preview,
keep `--dry-run` on every helper invocation and stop after reporting the plan. A preview still
fixes unresolved names in `tracks.json` first; that file is not a setup. The helper applies
changes by default; `--apply` has been removed. Honor the configured cleanup switches and exclusions.

## Configuration

[setup-sync.yaml](setup-sync.yaml) holds the defaults. Every folder in it is relative to each car
folder:

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

- `target-dirs`: where setups are copied. `--target-dir DIR` (repeatable) replaces the list for
  one run. These exact directories are the only folders excluded from source scanning.
- `clean-source.enabled`: controls source cleanup; `--clean-source` enables it for one run.
  If the setting is omitted, it is `false`.
- `clean-source.exclude`: car-relative glob patterns protected from source cleanup. Matching
  folders and files are still source candidates. `--clean-source-exclude GLOB` (repeatable)
  replaces the list for one run. An omitted list, `null` or `[]` means no exclusions.
- `clean-target.enabled`: controls target cleanup, which moves old setups into each track
  folder's `Archive` folder; `--clean-target` enables it for one run. If the setting is omitted,
  it is `false`.
- `clean-target.past-season-count`: a positive integer, default 2. `--clean-target-seasons N`
  overrides the count for one run; setting the count does not enable cleanup.

`--config PATH` reads another file. The YAML is a strict subset (mappings, lists of plain values,
strings, integers, booleans, null and empty lists), and anything else is an error rather than a guess.

## 1. Find the current season

Web-search the iRacing season calendar every run (for example "iRacing 2026 Season 4 start
date"; iracing.com's "This Week in iRacing" and season-release posts give exact dates). Record the
year, season, week and the season's start date. Seasons are twelve race weeks plus week 13, and
start on a Tuesday at 00:00 UTC. On 2026-09-11 this gave **26S3, week 13**: 26S3 began
2026-06-16 and 26S4 begins 2026-09-15.

The rule is **current season or newer**. Providers label late-season uploads for the next season,
so during week 13 (and at any other time) a `26S4` folder next to the `26S3` ones is included.

## 2. Inspect the plan

Preview all eligible cars internally before applying, using the season and start date just
looked up (the values below are an example):

```powershell
python "<SKILL_DIR>/scripts/setup_sync.py" --season 26S3 --season-start 2026-06-16 --dry-run
```

Add `--car NAME` only when the user selected particular cars. Carry the same config, scope and
cleanup options into the apply command. Add `--verbose` for per-file lines, or `--report <file.json>`
to save the whole plan. A dry run changes no setup files; `--report` writes the requested report.
The header line shows the targets and whether each cleanup mode applies, and from where.

For each target, the plan lists:

- `copy`: a new file.
- `overwrite`: the track folder already holds that file name with different bytes.
- `present`: the identical file is already there.
- `(new)`: a track folder the run will create.
- `archive`: old setups to move into their track folder's `Archive` folder.
- `Custom/Archive folders to create`: track folders still missing them. The total line counts
  them for every car, and `--verbose` lists them.

When source cleanup is enabled, the plan also lists each file or whole folder it will recycle.
The JSON report includes these paths under `clean_source`.

## 3. Resolve what the helper could not

Review the plan and fix what it shows:

- **`? unresolved`**: no folder or file name matched a known track, or a Nürburgring file has no
  layout. Fix every one in `tracks.json` (see Inference); the helper does not apply while any
  remain.
- **`(new)` folders**: check the name is the track you would have picked.
- **`! same name from two sources`**: two providers ship one file name, with different content,
  into one track folder. The newest is used; check that decision against the source files.
- **Target cleanup**: check nothing current is about to be archived, and note any archived copy
  it will replace. It runs only when enabled.
- **Source cleanup**: review the listed files and folders against the target directories and
  preservation globs. It recycles everything else, including unlabelled files and non-setup files.
- **`--verbose` `~` lines**: files placed by file-name tokens or dated by file time rather than
  a label. Spot-check them.

### Inference

Fix every unresolved name in [tracks.json](tracks.json). Never skip a file or stop at reporting
it. Each name falls into one of three cases:

1. **It clearly names a known track: add an alias without asking.** Abbreviations, nicknames,
   misspellings and provider codes such as `Barca` (Barcelona), `RAtlanta` or `Oulten`.
2. **It is a complete track name `tracks.json` does not have yet: add the track without
   asking.** These are tracks nobody had setups for yet, such as Track Titan's
   `Cadwell Park Circuit - Full` or `Texas Motor Speedway - Oval`.
3. **Anything else is an edge case: ask the user first.** That covers an event or series code
   that names no track (`PLM` is Petit Le Mans, raced at Road Atlanta), a name that could mean
   two tracks, or a layout the name does not settle. Put the evidence and your best guess in the
   question, hold the apply until it is answered, then record the answer. This is the only
   question a run asks.

Gather the evidence for an edge case in this order:

1. Another source for the same car, season and week that names the full track or layout, e.g.
   `Track Titan\2026\Season 3\Week 9\Nurburgring Grand-Prix-Strecke - Sprintstrecke` settles
   GnG's `26S3 W09 DTM Ferrari Nuerburgring`.
2. The series convention: NEC and NLS run the VLN layout; DTM and Creventic run the Nürburgring GP.
3. The official iRacing schedule for that season and week (web search).

Record every decision in `tracks.json` so the next run needs no judgement:

- An **alias**, for a new spelling, nickname or provider code of a known track: add it to that
  track's `aliases` (a run of words such as `"rd atlanta"`, or a code such as `"suz"` or `"plm"`)
  or `exact` (a whole iRacing folder name such as `"summit summit raceway"`).
- A **new track**: add a key named the way the folder should be named, Capitalized and spaced for
  multiple words, without generic words such as Circuit or Motor Speedway (`"Oulton Park"`,
  `"Texas"`), with its spellings as aliases.
- A **layout word**: `markers` pick a variant, while `weak_markers` (series names, loose words
  such as a bare "Nordschleife") only suggest one, and any layout marker outranks them.
- A **rule**, for one-off cases no alias should generalize from:
  `{"pattern": "^Garage 61/Data packs/26S3 W13 Creventic .*Nuerburgring", "track": "Nurb GP", "why": "..."}`.
  A rule can also carry `"season": "26S3"` or `"skip": true`. Patterns are case-insensitive regexes
  on the car-relative path with `/` separators; the first match wins.

Run the tests after editing `tracks.json`, with a new case for each name you added (see Tests),
then repeat the dry run. Pass `--allow-unresolved` only when the user says to skip a file. If
the user requested a preview, report it and stop. Otherwise continue to apply without asking for
another confirmation.

## 4. Apply

Remove `--dry-run` and retain the same scope, config and cleanup options:

```powershell
python "<SKILL_DIR>/scripts/setup_sync.py" --season 26S3 --season-start 2026-06-16
```

It is a sync, and the files it copies are flat:

- Each setup lands directly in its track folder, whatever provider subfolder it came from.
- A same-named file already directly in the track folder is overwritten.
- Every track folder, existing or new, gets a `Custom` and an `Archive` folder when it lacks one
  (in any letter case). setup-sync never writes to, moves from or removes anything in `Custom`.
  `Archive` receives the setups target cleanup moves out of the track folder.
- Folders that already exist inside the target folders are otherwise left alone. That covers
  subfolders of a track folder such as `Suzuka\GNG`, folders that do not name a track such as
  `P1Doks` or `Data packs` (which get no `Custom` or `Archive`), and loose files at the target
  folder's top.
- Missing track folders are created. Inside a track folder, only `Custom` and `Archive` are.
- Every copy is verified by hash, and copies keep the provider's original file date, which is the
  date iRacing's setup dialog shows.
- Anything the run replaces or removes goes to the Windows Recycle Bin. A recycle failure stops
  the run without falling back to a hard delete.
- A file named `Custom` or `Archive` where the folder belongs, or an `Archive` folder that links
  outside the car folder, stops the run before anything changes.

When source cleanup is disabled, the provider folders stay available for another run. When it
is enabled, restore any needed source folders from the Recycle Bin before rerunning. Report
the copied, overwritten, archived and source cleanup counts, the `Custom`/`Archive` folders
created, plus anything left unresolved.

### Cleaning source files and folders

`--clean-source` or `defaults.clean-source.enabled: true` recycles everything under each
selected car root except its active target directories and paths matching
`defaults.clean-source.exclude`. Cleanup runs after all cars' copies have been verified and
target cleanup has finished. A copy or verification failure prevents cleanup. Cars without an
existing target directory are skipped.

Patterns are case-insensitive and relative to the car root; a leading `/` is optional. `*`, `?`
and `[]` match within a name; `**` matches zero or more directories. `/Garage 61*` protects all
matching folders at the car root, including their contents. `/Providers/**/keep.sto` protects
matching files at any depth under `Providers`. Nested targets and excluded files/folders keep
their ancestors, while unprotected siblings are recycled. Cleanup refuses paths that escape
the car root or traverse links/junctions.

All other content is recycled, even files that were too old or unlabelled to copy. With
`--allow-unresolved`, this also includes unresolved files outside protected paths.

### Archiving old target seasons

`--clean-target` or `defaults.clean-target.enabled: true` enables target cleanup. The
`--clean-target-seasons N` flag or `defaults.clean-target.past-season-count` moves setups
labelled N or more seasons before `--season` from the files directly in each target track
folder into that track folder's `Archive` folder. N = 2 at 26S3 archives 26S1 and older; N = 1
keeps only the current season and newer in the track folder. Two guards keep it from fighting
the sync:

- A file whose name matches any current-season source stays, whatever its label says. P1Doks
  offers `..._25S3W8.sto` as its current Le Mans setup.
- A file without a season in its name (a teammate's `Teammate_..._V3.sto`) stays.

Archived setups keep their file date. When `Archive` already holds the same file name, the
archived copy goes to the Recycle Bin and the newer one replaces it, as the sync treats same
names. Only setups (`.sto`, or extensions selected with `--ext`) are archived. Files inside
`Custom`, `Archive` or any other subfolder of a track folder are never archived or read as
setups, and nothing in the left-alone target folders above is touched. Track folders always keep
their `Custom` and `Archive` folders, so cleanup never empties or removes one.

## How names are read

| Source (under the car folder) | Season from | Track from |
| --- | --- | --- |
| `Garage 61\Data packs\26S3 W04 Spa24 Ferrari\` (GnG) | pack name | pack name |
| `P1Doks\<track>\2026-S3\` | season folder, even when the file says `25S3W8` | P1Doks folder; file tokens only pick a layout |
| `<iRacing track>\26s3`, `\2026 Season 3`, `\2026 season`, `\26 dtm` | season subfolder, else the file name | iRacing folder (`spa 2024 combined` is Spa's 24h layout) |
| `Track Titan\2026\Season 3\Week 4\<Full Track - Config>\`, `HYMO\...` | year + `Season N` folders | leaf folder |
| Coach Dave Academy: `26S3 Simucube GT3 Series\Week 12 (Suzuka)`, `2026 iRacing Suzuka 1000\Week 1 (Suzuka)` | series folder at the car root | week folder, refined by the series name |
| `Coach Dave Academy\`, `MG\`, `VRS\<track>\` (flat) | file name (`CDA 26S3 ... SUZ`, `26S2.FUJI...`, `VRS_263_...`) | folder, else file name (CDA track codes such as `SUZ`, `WGB`, `PAN`) |

- **Season precedence.** The deepest folder label wins over the file name (providers file
  carry-overs under the season they offer them for). A file label refines a year-only folder.
  A year-only label for the current year counts only when the file is dated no earlier than one
  week before the season started. Track folder years (`spa 2024 up`) are never seasons.
  Unlabelled files are left out.
- **Layouts.** Nürburgring always splits into `Nurb GP`, `Nurb VLN`, `Nurb Combined 24H`,
  `Nurb Long` and `Nordschleife`. `Spa24` and `Monza Combined` get their own folder only when the
  target already has one, and otherwise go into `Spa` or `Monza`.
- **Destination folders.** An existing folder that names the same track is reused under its
  existing spelling (`LeMans`, `mexicocity gp`, `Watkins`). If there are several, the exact
  canonical spelling wins, then the fullest. New folders use the canonical key from
  `tracks.json`, and a folder named exactly as one (such as `Nordschleife`) is always a track
  folder. Only whole-name matches are reused, so `Spa 12H` is not taken for `Spa`, and such
  folders are never written to.
- **Source scope.** Only the active target directories are excluded as source folders. Other
  team shares and source-cleanup exclusions are read normally. Only `.sto` files are copied
  (`--ext` selects other extensions). Car folders without any target folder are skipped.
- **Never copied.** A setup titled `fixed` (`fixed.sto` in any letter case, such as the one P1Doks
  ships in each track's season folder) is never copied, wherever it is found. The plan lists it
  as excluded, so it never counts as unresolved.

## Tests

From `<SKILL_DIR>`:

```powershell
python -m unittest discover -s scripts -p test_setup_sync.py
```

The fixtures are real provider names from the setups tree. Add a case whenever an alias, rule or
inference changes what a real name resolves to.
