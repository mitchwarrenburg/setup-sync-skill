---
name: setup-sync
description: Sync current-season iRacing car setups from every provider folder (Garage 61 data packs/GnG, P1Doks, Track Titan/HYMO, Coach Dave Academy, VRS, GO, ARA, MG and iRacing track folders) into configured target folders inside each car folder (by default the Garage 61 team folders), directly into one folder per track. Finds the current iRacing season by web search, keeps that season or newer, infers track and layout from folder and file names, records new inferences so the next run is automatic, and removes team setups a configured number of seasons old. Use when asked to sync, centralize, share, copy or clean iRacing setups in the team folders, for one car or all of them.
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

## Configuration

[setup-sync.yaml](setup-sync.yaml) holds the defaults. Every folder in it is relative to each car
folder:

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

- `target-dirs`: where setups are copied. `--target-dir DIR` (repeatable) replaces the list for one run.
- `clean.past-seasons`: the default for `--clean` (see Cleaning). It applies to every run unless
  `--clean N` overrides it or `--no-clean` skips it. `null` means clean only when asked.
- `exclude-dirs`: folders never read as sources (glob patterns), such as other team shares. The
  target folders are always excluded too.

`--config PATH` reads another file. The YAML is a strict subset (mappings, lists of plain values,
strings, integers, null), and anything else is an error rather than a guess.

## 1. Find the current season

Web-search the iRacing season calendar every run (for example "iRacing 2026 Season 4 start
date"; iracing.com's "This Week in iRacing" and season-release posts give exact dates). Record the
year, season, week and the season's start date. Seasons are twelve race weeks plus week 13, and
start on a Tuesday at 00:00 UTC. On 2026-09-11 this gave **26S3, week 13**: 26S3 began
2026-06-16 and 26S4 begins 2026-09-15.

The rule is **current season or newer**. Providers label late-season uploads for the next season,
so during week 13 (and at any other time) a `26S4` folder next to the `26S3` ones is included.

## 2. Dry run

Name one car first:

```powershell
python "<SKILL_DIR>/scripts/setup_sync.py" --season 26S3 --season-start 2026-06-16 --car ferrari296gt3
```

Use `--all` for every car folder that has a target folder. Add `--verbose` for per-file lines, or
`--report <file.json>` for the whole plan. The dry run writes nothing. The header line shows the
targets and whether a clean applies, and from where.

For each target, the plan lists:

- `copy`: a new file.
- `overwrite`: the track folder already holds that file name with different bytes.
- `present`: the identical file is already there.
- `(new)`: a track folder the run will create.
- `clean`: old setups to remove.

## 3. Review, then infer what the helper could not

Read the plan before applying:

- **`? unresolved`**: no folder or file name matched a known track, or a Nürburgring file has no
  layout. Apply refuses while any remain (see Inference).
- **`(new)` folders**: check the name is the track you would have picked.
- **`! same name from two sources`**: two providers ship one file name, with different content,
  into one track folder. The newest is used; confirm that is right.
- **`clean`**: check nothing current is about to go. The configured default cleans on every run.
- **`--verbose` `~` lines**: files placed by file-name tokens or dated by file time rather than
  a label. Spot-check them.

### Inference

Decide from evidence, in this order:

1. Another source for the same car, season and week that names the full track or layout, e.g.
   `Track Titan\2026\Season 3\Week 9\Nurburgring Grand-Prix-Strecke - Sprintstrecke` settles
   GnG's `26S3 W09 DTM Ferrari Nuerburgring`.
2. The series convention: NEC and NLS run the VLN layout; DTM and Creventic run the Nürburgring GP.
3. The official iRacing schedule for that season and week (web search).

Record the decision in [tracks.json](tracks.json) so the next run needs no judgement:

- An **alias**, when a new spelling or provider code of a known track appears: add it to that
  track's `aliases` (a run of words such as `"rd atlanta"`, or a Coach Dave code such as `"suz"`)
  or `exact` (a whole iRacing folder name such as `"summit summit raceway"`).
- A **new track**: add a key named the way the folder should be named, Capitalized and spaced for
  multiple words (`"Oulton Park"`), with its aliases.
- A **layout word**: `markers` pick a variant, while `weak_markers` (series names, loose words
  such as a bare "Nordschleife") only suggest one, and any layout marker outranks them.
- A **rule**, for one-off cases no alias should generalize from:
  `{"pattern": "^Garage 61/Data packs/26S3 W13 Creventic .*Nuerburgring", "track": "Nurb GP", "why": "..."}`.
  A rule can also carry `"season": "26S3"` or `"skip": true`. Patterns are case-insensitive regexes
  on the car-relative path with `/` separators; the first match wins.

Unknown stays unknown. If the evidence does not settle it, report the file and leave it out
with `--allow-unresolved` rather than guessing. Run the tests after editing `tracks.json`, then
repeat the dry run.

## 4. Apply

```powershell
python "<SKILL_DIR>/scripts/setup_sync.py" --season 26S3 --season-start 2026-06-16 --car ferrari296gt3 --apply
```

It is a sync, and the files it copies are flat:

- Each setup lands directly in its track folder, whatever provider subfolder it came from.
- A same-named file already directly in the track folder is overwritten.
- Folders that already exist inside the target folders are left alone. That covers subfolders of
  a track folder such as `Suzuka\GNG`, folders that do not name a track such as `P1Doks` or
  `Data packs`, and loose files at the target folder's top.
- Only missing track folders are created, never a folder inside one.
- Every copy is verified by hash, and copies keep the provider's original file date, which is the
  date iRacing's setup dialog shows.
- Anything the run replaces or removes goes to the Windows Recycle Bin, never a hard delete.

The provider folders stay the source: correct a bad run by fixing `tracks.json` and running
again. Report the copied, overwritten and cleaned counts, plus anything left unresolved.

### Cleaning old seasons

The clean (`--clean N`, or `clean.past-seasons` from the config) removes setups labelled N or
more seasons before `--season` from the files directly in the target track folders. N = 2 at 26S3
removes 26S1 and older; N = 1 keeps only the current season and newer. Two guards keep it from
fighting the sync:

- A file whose name matches any current-season source is kept, whatever its label says. P1Doks
  offers `..._25S3W8.sto` as its current Le Mans setup.
- A file without a season in its name (a teammate's `Teammate_..._V3.sto`) is kept.

A track folder the clean leaves empty is removed. Only setups (`.sto`) are cleaned, and nothing in
the left-alone folders above is touched.

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
  `tracks.json`. Only whole-name matches are reused, so `Spa 12H` is not taken for `Spa`, and
  such folders are never written to.
- **Out of scope.** Target and `exclude-dirs` folders are never sources. Only `.sto` files are
  copied (`--ext` adds others). Car folders without any target folder are skipped.

## Tests

From `<SKILL_DIR>`:

```powershell
python -m unittest discover -s scripts -p test_setup_sync.py
```

The fixtures are real provider names from the setups tree. Add a case whenever an alias, rule or
inference changes what a real name resolves to.
