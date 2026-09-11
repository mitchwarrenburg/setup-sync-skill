"""Tests for setup-sync. Names are real provider folder and file names seen under iRacing\\setups.

Run from the repository root: python -m unittest discover -s scripts -p test_setup_sync.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup_sync  # noqa: E402
from setup_sync_config import ConfigError, load_config, parse_yaml, relative_dir  # noqa: E402
from setup_sync_rules import Season, TrackTable, decide_season, parse_season, season_evidence  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1]
TABLE = TrackTable.load(SKILL_DIR / "tracks.json")
CURRENT = Season(2026, 3)
START = date(2026, 6, 16)
ECLIPSE, RASEN = "Garage 61 - Eclipse Motorsport", "Garage 61 - Rasengrasen Racing"


def where(relative: str) -> str | None:
    parts = relative.split("/")
    result = TABLE.resolve_file(parts[:-1], Path(parts[-1]).stem)
    return TABLE.canonical(result.family, result.variant) if result.family else None


def season_of(relative: str, modified: date = date(2026, 9, 1)) -> bool:
    parts = relative.split("/")
    folders = parts[:-1]
    flags = [TABLE.is_track_folder(f) for f in folders]
    evidence = season_evidence(folders, Path(parts[-1]).stem, flags, CURRENT.year + 1)
    return decide_season(evidence, CURRENT, START, modified).include


class SeasonTests(unittest.TestCase):
    def test_season_argument_forms(self):
        for text in ("26S3", "26s3", "2026S3", "2026-S3", "2026 Season 3"):
            self.assertEqual(parse_season(text), CURRENT, text)

    def test_season_arithmetic_crosses_years(self):
        self.assertEqual(CURRENT.shifted(-2), Season(2026, 1))
        self.assertEqual(Season(2026, 1).shifted(-1), Season(2025, 4))
        self.assertEqual(Season(2026, 4).shifted(1), Season(2027, 1))

    def test_current_or_newer(self):
        self.assertTrue(season_of("Garage 61/Data packs/26S3 W13 Suzuka1000 Ferrari/26S3-GnG-Suzuka1000-Ferrari-Q.sto"))
        self.assertTrue(season_of("suzuka grandprix/26s4/GO 26S3 Suzuka 1000km 296GT3 Suzuka Q Esport.sto"))
        self.assertTrue(season_of("26S3 Simucube GT3 Series/Week 12 (Suzuka)/CDA 26S3 GT3E 296GT3 SUZ E01.sto"))
        self.assertFalse(season_of("spa 2024 up/26s2/GO 26S2 GTS 296GT3 Spa Q Safe.sto"))
        self.assertFalse(season_of("25S4 IMSA Racing Series/Week 12 (Circuit Spa Francorchamps GP 2024)/x.sto"))

    def test_provider_season_folder_beats_file_label(self):
        self.assertTrue(season_of("P1Doks/lemans/2026-S3/P1Doks_296GT3_Lemans_GTS_E_25S3W8.sto"))
        self.assertFalse(season_of("P1Doks/suzuka/2026-S2/P1Doks_FerrariGT3_Suzuka_GTS_E_26S2W9.sto"))
        self.assertTrue(season_of("Track Titan/2026/Season 3/Week 1/Watkins Glen International - Boot/"
                                  "HYMO_GTS_26S2_F296_Spa_CQ.sto"))
        self.assertFalse(season_of("Track Titan/2026/Season 2/Week 11/Thruxton Circuit/HYMO_GTS_26S2_F296_Thruxton_CQ.sto"))

    def test_file_label_refines_year_only_folder(self):
        self.assertFalse(season_of("spa 2024 up/2026 season/VRS_26S2KEJ_296GT3_Spa_DTM_Q.sto"))
        self.assertTrue(season_of("spa 2024 up/26 dtm/GO 26S3 DTM 296GT3 Spa Q Esport.sto"))
        self.assertTrue(season_of("interlagos gp/2026 Season 3/VRS_263_JA_296GT3_Interlagos_Q1.sto"))

    def test_year_only_uses_file_date_with_week13_grace(self):
        path = "iRacing DTM Series 2026/Week 3 (Circuit Spa Francorchamps GP 2024)/setup.sto"
        self.assertTrue(season_of(path, date(2026, 6, 10)))
        self.assertFalse(season_of(path, date(2026, 5, 1)))

    def test_track_dirname_years_are_not_seasons(self):
        self.assertFalse(season_of("spa 2024 up/setup.sto"))
        self.assertFalse(season_of("Track Titan/Nurburgring Combined - Gesamtstrecke VLN/HYMO_NEC6_26S2_F296_CQ.sto"))


class TrackTests(unittest.TestCase):
    def test_gng_pack_names(self):
        cases = {
            "26S3 W01 DTM Ferrari SpaGP": "Spa", "26S3 W01 Watkins6H Ferrari": "Watkins Glen",
            "26S3 W02 GT-Sprint Ferrari Oran Park": "Oran Park", "26S3 W03 NEC Ferrari GT3": "Nurb VLN",
            "26S3 W04 Spa24 Ferrari": "Spa24", "26S3 W05 GT-Sprint Ferrari Virginia": "VIR",
            "26S3 W06 RAmerica6H Ferrari": "Road America", "26S3 W08 GT-Sprint Ferrari Le Mans": "Le Mans",
            "26S3 W09 DTM Ferrari Nuerburgring": "Nurb GP", "26S3 W09 GT-Sprint Ferrari Indy": "Indianapolis",
            "26S3 W10 GT-Sprint Ferrari St. Petersburg": "St. Pete", "26S3 W11 IMSA Ferrari Road Atlanta": "Road Atlanta",
            "26S3 W13 Creventic Ferrari GT3 Nuerburgring": "Nurb GP", "26S3 W13 Suzuka1000 Ferrari": "Suzuka",
            "26S3 W13 Suzuka 1000km Ford": "Suzuka", "26S3 W03 NEC M2 Nordschleife": "Nurb VLN",
            "26S3 W02 ProductionCar M2 Coronado": "Coronado", "26S3 W06 ProductionCar M2 WWT": "WWT Raceway",
            "26S3 W12 ProductionCar M2 Roval": "Charlotte", "26S3 W11 PCUP Porsche RBR": "Red Bull Ring",
            "26S3 W08 OPENWHEEL SuperFormula RAmer": "Road America", "26S3 W02 AdvancedMazda Oulton": "Oulton Park",
            "26S3 W12 GTE-Sprint Corvette Montreal": "Montreal", "26S3 W06 OPENWHEEL F3 Hungaroring": "Hungaroring",
        }
        for pack, expected in cases.items():
            self.assertEqual(where(f"Garage 61/Data packs/{pack}/setup.sto"), expected, pack)

    def test_layout_markers_outrank_weak_ones(self):
        # "DTM" only suggests the GP layout; an explicit VLN in the file name wins.
        self.assertEqual(where("Garage 61/Data packs/26S3 W09 DTM Ferrari Nuerburgring/26S3-GnG-VLN-Q.sto"), "Nurb VLN")
        self.assertEqual(where("Garage 61/Data packs/26S3 W09 DTM Ferrari Nuerburgring/26S3-GnG-DTM-Q.sto"), "Nurb GP")

    def test_p1doks_folder_and_file_markers(self):
        self.assertEqual(where("P1Doks/nurburgring/2026-S3/P1Doks_FerrariGT3_NEC_Ev2_26S2.sto"), "Nurb VLN")
        self.assertEqual(where("P1Doks/nurburgringgp/2026-S3/P1Doks_296GT3_NurbGP_DTM_Q_26S3W6.sto"), "Nurb GP")
        # P1Doks "Spa24" means the 2024 track, not the 24h race: the folder decides.
        self.assertEqual(where("P1Doks/spa/2026-S3/P1Doks_296GT3_Spa24_GTS_Ev2_26S3W4.sto"), "Spa")
        self.assertEqual(where("P1Doks/stpete/2026-S3/x.sto"), "St. Pete")
        self.assertEqual(where("P1Doks/vir/2026-S3/x.sto"), "VIR")

    def test_iracing_track_folders(self):
        self.assertEqual(where("spa 2024 combined/26s3/GO 26S3 Spa24H 296GT3 Q Esport.sto"), "Spa24")
        self.assertEqual(where("spa 2024 up/26s3/GO 26S3 GTS 296GT3 Spa Q Esport.sto"), "Spa")
        self.assertEqual(where("nurburgring combinedshortb/26nec/x.sto"), "Nurb VLN")
        self.assertEqual(where("nurburgring combined/26s3/x.sto"), "Nurb Combined 24H")
        self.assertEqual(where("watkinsglen 2021 fullcourse/2026 season/x.sto"), "Watkins Glen")
        self.assertEqual(where("roadamerica full/2026 Season 3/x.sto"), "Road America")

    def test_series_and_track_titan_folders_at_the_car_root(self):
        self.assertEqual(where("26S3 Simucube GT3 Series/Week 12 (Suzuka)/CDA 26S3 GT3E 296GT3 SUZ E01.sto"), "Suzuka")
        self.assertEqual(where("2026 iRacing SPA 24h/Week 1 (Circuit Spa Francorchamps GP 2024)/x.sto"), "Spa24")
        self.assertEqual(where("2026 iRacing 24H Nürburgring/Week 1 (Nürburgring Combined)/x.sto"), "Nurb Combined 24H")
        self.assertEqual(where("iRacing DTM Series 2026/Week 6 (nurburgring sprintchicane)/x.sto"), "Nurb GP")
        self.assertEqual(where("26S2 IMSA Racing Series/Week 10 (Nurburgring Combined - Gesamtstrecke Long)/x.sto"),
                         "Nurb Long")
        titan = "Track Titan/2026/Season 3/Week 1/{}/x.sto"
        for folder, expected in {
            "Circuit des 24 Heures du Mans - 24 Heures du Mans": "Le Mans",
            "Canadian Tire Motorsports Park": "Mosport", "Mount Panorama Circuit": "Bathurst",
            "Autodromo Jose Carlos Pace - Grand Prix": "Interlagos",
            "Hockenheimring Baden-Wurttemberg - Grand Prix": "Hockenheim",
            "Nurburgring Grand-Prix-Strecke - Sprintstrecke": "Nurb GP",
            "Nurburgring Combined - Gesamtstrecke VLN": "Nurb VLN",
            "Fuji International Speedway - No Chicane": "Fuji", "Red Bull Ring - Grand Prix": "Red Bull Ring",
            "Qualcomm Circuit (Naval Base Coronado)": "Coronado", "Circuit Gilles Villeneuve": "Montreal",
            "Barber Motorsports Park 2026 - Full Course": "Barber", "Rudskogen Motorsenter": "Rudskogen",
            "Atlanta Motor Speedway - Road - 2008": "Atlanta", "Chicago Street Course - 2023 Cup": "Chicago",
        }.items():
            self.assertEqual(where(titan.format(folder)), expected, folder)

    def test_flat_provider_folders_use_file_names(self):
        self.assertEqual(where("MG/26S2.FUJI.MUSTANGGT4.DRIVER.R.JR.sto"), "Fuji")
        self.assertEqual(where("Coach Dave Academy/CDA 26S3 GT3E 296GT3 WGB E01.sto"), "Watkins Glen")
        self.assertEqual(where("Coach Dave Academy/CDA 26S3 SUZ1000 296GT3 SUZ E01.sto"), "Suzuka")
        self.assertIsNone(where("MG/26S3.SOMEWHERE.MUSTANGGT4.R.sto"))

    def test_destination_folder_names_resolve_fully(self):
        for name, expected in {"LeMans": "Le Mans", "mexicocity gp": "Mexico City", "daytona 2011 road": "Daytona",
                               "Nurb NEC": "Nurb VLN", "Nord VLN": "Nurb VLN", "Nurb24": "Nurb Combined 24H",
                               "Spa 24": "Spa24", "Portimao": "Algarve", "Watkins": "Watkins Glen",
                               "Miami GP": "Miami GP", "St. Pete": "St. Pete", "The Bend": "The Bend",
                               "Autodromo Nazionale Monza - Combined": "Monza Combined"}.items():
            match = TABLE.resolve(name)
            self.assertTrue(match and match.full, name)
            self.assertEqual(TABLE.canonical(match.family, match.variant if match.explicit else None), expected, name)
        for name in ("Spa 12H", "P1Doks", "Nurb Combined", "Nürburgring", "Data packs", "New folder"):
            match = TABLE.resolve(name)
            reusable = match and match.full and TABLE.canonical(match.family, match.variant if match.explicit else None)
            self.assertFalse(reusable, name)


class ConfigTests(unittest.TestCase):
    def test_shipped_config(self):
        config = load_config(SKILL_DIR / "setup-sync.yaml")
        self.assertEqual(config.target_dirs, (RASEN, ECLIPSE))
        self.assertEqual(config.clean_past_seasons, 2)
        self.assertEqual(config.exclude_dirs, ("Garage 61 - *",))

    def test_yaml_subset(self):
        text = ('defaults:  # comment\n  target-dirs:\n  - "/Team #1"\n  - /Plain Team\n'
                "  clean:\n    past-seasons: ~\n")
        self.assertEqual(parse_yaml(text), {"defaults": {"target-dirs": ["/Team #1", "/Plain Team"],
                                                         "clean": {"past-seasons": None}}})

    def test_invalid_config_is_an_error_not_a_guess(self):
        for text in ("defaults:\n\ttarget-dirs: []\n", "defaults:\n  target-dirs:\n    - name: x\n",
                     "defaults:\n  targets:\n    - /x\n", "other: 1\n", 'defaults:\n  target-dirs:\n    - "/x\n',
                     "defaults:\n  clean:\n    past-seasons: 0\n", "defaults:\n  clean:\n    past-seasons: two\n"):
            path = Path(tempfile.mkdtemp()) / "setup-sync.yaml"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ConfigError, msg=text):
                load_config(path)

    def test_target_dirs_are_relative_to_the_car_folder(self):
        self.assertEqual(relative_dir("/Garage 61 - Eclipse Motorsport"), ECLIPSE)
        self.assertEqual(relative_dir("\\Shared\\Team A\\"), "Shared/Team A")
        for bad in ("C:/Users/x", "/../outside", "", "/"):
            with self.assertRaises(ConfigError, msg=bad):
                relative_dir(bad)


class PlanTests(unittest.TestCase):
    """End-to-end plan and apply on a temporary setups tree."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.car = self.root / "ferrari296gt3"
        self.eclipse = self.car / ECLIPSE
        self.rasen = self.car / RASEN
        for folder in (self.eclipse / "Spa", self.rasen / "LeMans", self.rasen / "Spa24"):
            folder.mkdir(parents=True)
        self.write("Garage 61/Data packs/26S3 W04 Spa24 Ferrari/26S3-W04-GnG-Spa24-Ferrari-Q.sto", b"spa24")
        self.write("Garage 61/Data packs/26S3 W12 IMSA Ferrari Le Mans/26S3-W12-GnG-LeMans-Ferrari-Q.sto", b"lm")
        self.write("Garage 61/Data packs/26S2 W10 GT-Sprint Ferrari Magny Cours/old.sto", b"old")
        self.write("P1Doks/nurburgring/2026-S3/P1Doks_FerrariGT3_NEC_Ev2_26S2.sto", b"nec")
        self.write(f"{ECLIPSE}/Spa/GNG/26S3-W04-GnG-Spa24-Ferrari-Q.sto", b"spa24")
        self.write(f"{RASEN}/Spa24/26S3-W04-GnG-Spa24-Ferrari-Q.sto", b"edited")
        self.config = self.config_file("")
        # Real runs send removed files to the Recycle Bin; a test tree just deletes them.
        self.saved_discard, setup_sync.discard = setup_sync.discard, Path.unlink

    def tearDown(self):
        setup_sync.discard = self.saved_discard
        self.tmp.cleanup()

    def config_file(self, extra: str, name: str = "setup-sync.yaml") -> Path:
        path = self.root / name
        path.write_text(f'defaults:\n  target-dirs:\n    - "/{ECLIPSE}"\n    - "/{RASEN}"\n{extra}', encoding="utf-8")
        return path

    def write(self, relative: str, content: bytes, when: datetime = datetime(2026, 9, 1)) -> Path:
        path = self.car / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        os.utime(path, (when.timestamp(), when.timestamp()))
        return path

    def run_sync(self, *extra: str, config: Path | None = None) -> int:
        return setup_sync.main(["--season", "26S3", "--season-start", "2026-06-16", "--root", str(self.root),
                                "--car", "ferrari296gt3", "--config", str(config or self.config), *extra])

    def nested(self) -> list[str]:
        """Every folder inside a track folder of either target."""
        return sorted(p.relative_to(self.car).as_posix() for team in (self.eclipse, self.rasen)
                      for track in team.iterdir() if track.is_dir() for p in track.rglob("*") if p.is_dir())

    def test_dry_run_changes_nothing(self):
        before = sorted(p.relative_to(self.car) for p in self.car.rglob("*"))
        self.assertEqual(self.run_sync("--clean", "1"), 0)
        self.assertEqual(sorted(p.relative_to(self.car) for p in self.car.rglob("*")), before)

    def test_apply_copies_flat_reuses_folders_and_overwrites_same_names(self):
        nested = self.nested()
        self.assertEqual(self.run_sync("--apply"), 0)
        self.assertTrue((self.eclipse / "Le Mans" / "26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())
        self.assertTrue((self.rasen / "LeMans" / "26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())  # existing spelling
        self.assertFalse((self.rasen / "Le Mans").exists())
        self.assertTrue((self.eclipse / "Nurb VLN" / "P1Doks_FerrariGT3_NEC_Ev2_26S2.sto").exists())
        # Copies land directly in the track folder; the existing Spa/GNG folder is left alone.
        self.assertEqual((self.eclipse / "Spa" / "26S3-W04-GnG-Spa24-Ferrari-Q.sto").read_bytes(), b"spa24")
        self.assertTrue((self.eclipse / "Spa" / "GNG" / "26S3-W04-GnG-Spa24-Ferrari-Q.sto").exists())
        self.assertFalse((self.eclipse / "Spa24").exists())
        # A same-named file with other bytes is overwritten: it is a sync.
        self.assertEqual((self.rasen / "Spa24" / "26S3-W04-GnG-Spa24-Ferrari-Q.sto").read_bytes(), b"spa24")
        self.assertEqual(list(self.car.glob("Garage 61 - */**/old.sto")), [])
        self.assertEqual(self.nested(), nested)             # no folder is created inside a track folder
        self.assertEqual(self.run_sync("--apply"), 0)       # a second run finds nothing to change
        self.assertEqual(self.nested(), nested)

    def test_other_team_shares_are_never_sources(self):
        self.write("Garage 61 - Radian Motorsport/Spa/26S3-W03-Radian-Spa-Q.sto", b"theirs")
        self.assertEqual(self.run_sync("--apply"), 0)
        self.assertEqual(list(self.car.glob(f"{ECLIPSE}/**/26S3-W03-Radian-Spa-Q.sto")), [])

    def test_target_dir_flag_replaces_the_configured_targets(self):
        self.assertEqual(self.run_sync("--apply", "--target-dir", f"/{ECLIPSE}"), 0)
        self.assertTrue((self.eclipse / "Le Mans" / "26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())
        self.assertFalse((self.rasen / "LeMans" / "26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())
        (self.car / "Shared" / "Team A").mkdir(parents=True)
        self.assertEqual(self.run_sync("--apply", "--target-dir", "/Shared/Team A"), 0)
        self.assertTrue((self.car / "Shared" / "Team A" / "Le Mans" / "26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())

    def test_configured_clean_is_the_default_and_no_clean_skips_it(self):
        old = f"{ECLIPSE}/Imola/25S4-W04-GnG-Imola-Ferrari-Q.sto"
        self.write(old, b"old")
        self.assertEqual(self.run_sync("--apply", "--no-clean",
                                       config=self.config_file("  clean:\n    past-seasons: 2\n", "clean.yaml")), 0)
        self.assertTrue((self.car / old).exists())
        self.assertEqual(self.run_sync("--apply", config=self.root / "clean.yaml"), 0)
        self.assertFalse((self.car / old).exists())

    def test_same_name_in_different_tracks_is_copied_to_each(self):
        self.write("P1Doks/spa/2026-S3/fixed.sto", b"spa")
        self.write("P1Doks/bathurst/2026-S3/fixed.sto", b"bathurst")
        self.assertEqual(self.run_sync("--apply"), 0)
        self.assertEqual((self.eclipse / "Spa" / "fixed.sto").read_bytes(), b"spa")
        self.assertEqual((self.eclipse / "Bathurst" / "fixed.sto").read_bytes(), b"bathurst")

    def test_clean_removes_old_labels_from_track_folders_only(self):
        self.write(f"{ECLIPSE}/Imola/25S4-W04-GnG-Imola-Ferrari-Q.sto", b"old")
        self.write(f"{ECLIPSE}/Imola/old/25S4-W03-GnG-Imola-Ferrari-R.sto", b"kept")
        self.write(f"{RASEN}/Sebring/26S1-W10-GnG-Sebring-Ferrari-Q.sto", b"old")
        self.write(f"{ECLIPSE}/P1Doks/26S1-W01-P1Doks.sto", b"kept")
        self.write(f"{ECLIPSE}/26S1-loose.sto", b"kept")
        self.write(f"{ECLIPSE}/Watkins Glen/Teammate_Ferrari296_WatkinsGlen_V1.sto", b"mine")
        self.write(f"{ECLIPSE}/Spa/HYMO_GTS_26S2_F296_Spa_CQ.sto", b"s2")
        # A P1Doks carry-over: labelled 25S3, but offered for 26S3, so it is current.
        self.write("P1Doks/spa/2026-S3/P1Doks_296GT3_Spa_GTS_E_25S3W4.sto", b"p1")
        self.write(f"{ECLIPSE}/Spa/P1Doks_296GT3_Spa_GTS_E_25S3W4.sto", b"p1")
        self.assertEqual(self.run_sync("--apply", "--clean", "2"), 0)
        self.assertFalse((self.eclipse / "Imola" / "25S4-W04-GnG-Imola-Ferrari-Q.sto").exists())
        self.assertTrue((self.eclipse / "Imola" / "old" / "25S4-W03-GnG-Imola-Ferrari-R.sto").exists())  # subfolder
        self.assertFalse((self.rasen / "Sebring").exists())                                             # emptied
        self.assertTrue((self.eclipse / "P1Doks" / "26S1-W01-P1Doks.sto").exists())                     # not a track
        self.assertTrue((self.eclipse / "26S1-loose.sto").exists())                                     # target root
        self.assertTrue((self.eclipse / "Watkins Glen" / "Teammate_Ferrari296_WatkinsGlen_V1.sto").exists())
        self.assertTrue((self.eclipse / "Spa" / "HYMO_GTS_26S2_F296_Spa_CQ.sto").exists())
        self.assertTrue((self.eclipse / "Spa" / "P1Doks_296GT3_Spa_GTS_E_25S3W4.sto").exists())
        self.assertEqual(self.run_sync("--apply", "--clean", "1"), 0)
        self.assertFalse((self.eclipse / "Spa" / "HYMO_GTS_26S2_F296_Spa_CQ.sto").exists())
        self.assertTrue((self.eclipse / "Spa" / "P1Doks_296GT3_Spa_GTS_E_25S3W4.sto").exists())

    def test_clean_needs_at_least_one_season(self):
        with self.assertRaises(SystemExit):
            self.run_sync("--clean", "0")

    def test_unresolved_blocks_apply(self):
        self.write("MG/26S3.NOWHERE.296.R.sto", b"x")
        self.assertEqual(self.run_sync("--apply"), 2)
        self.assertFalse((self.eclipse / "Le Mans").exists())
        self.assertEqual(self.run_sync("--apply", "--allow-unresolved"), 0)
        self.assertTrue((self.eclipse / "Le Mans").exists())


if __name__ == "__main__":
    unittest.main()
