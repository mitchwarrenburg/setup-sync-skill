"""Tests for setup-sync. Names are real provider folder and file names seen under iRacing\\setups.

Run from the repository root: python -m unittest discover -s scripts -p test_setup_sync.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

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
    def test_explicitly_disabled_cleanup_config(self):
        # The checkout's defaults are user-editable; exercise a fixed config fixture.
        config = self.read_config(f'defaults:\n  target-dirs:\n    - "/{RASEN}"\n    - "/{ECLIPSE}"\n'
                                  '  clean-source:\n    enabled: false\n    exclude:\n      - "/Garage 61*"\n'
                                  '  clean-target:\n    enabled: false\n    past-season-count: 2\n')
        self.assertEqual(config.target_dirs, (RASEN, ECLIPSE))
        self.assertFalse(config.clean_source_enabled)
        self.assertEqual(config.clean_source_exclude, ("Garage 61*",))
        self.assertFalse(config.clean_target_enabled)
        self.assertEqual(config.clean_target_past_season_count, 2)

    def read_config(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "setup-sync.yaml"
            path.write_text(text, encoding="utf-8")
            return load_config(path)

    def test_cleanup_is_disabled_when_omitted(self):
        config = self.read_config("defaults:\n  target-dirs:\n    - /Team\n")
        self.assertFalse(config.clean_source_enabled)
        self.assertFalse(config.clean_target_enabled)
        self.assertEqual(config.clean_source_exclude, ())
        self.assertEqual(config.clean_target_past_season_count, 2)

    def test_cleanup_settings(self):
        config = self.read_config('defaults:\n  clean-source:\n    enabled: true\n    exclude:\n'
                                  '      - "/Garage 61*"\n      - /Shared/Keep\n'
                                  '  clean-target:\n    enabled: true\n    past-season-count: 3\n')
        self.assertTrue(config.clean_source_enabled)
        self.assertTrue(config.clean_target_enabled)
        self.assertEqual(config.clean_source_exclude, ("Garage 61*", "Shared/Keep"))
        self.assertEqual(config.clean_target_past_season_count, 3)
        self.assertEqual(self.read_config("defaults:\n  clean-source:\n    exclude: []\n").clean_source_exclude, ())

    def test_yaml_subset(self):
        text = ('defaults:  # comment\n  target-dirs:\n  - "/Team #1"\n  - /Plain Team\n'
                "  clean-source:\n    enabled: false\n    exclude: ~\n")
        self.assertEqual(parse_yaml(text), {"defaults": {"target-dirs": ["/Team #1", "/Plain Team"],
                                                         "clean-source": {"enabled": False, "exclude": None}}})

    def test_invalid_config_is_an_error_not_a_guess(self):
        for text in ("defaults:\n\ttarget-dirs: []\n", "defaults:\n  target-dirs:\n    - name: x\n",
                     "defaults:\n  targets:\n    - /x\n", "other: 1\n", 'defaults:\n  target-dirs:\n    - "/x\n',
                     "defaults:\n  clean:\n    past-seasons: 2\n", "defaults:\n  exclude-dirs:\n    - /x\n",
                     "defaults:\n  clean-source: false\n", "defaults:\n  clean-target: true\n",
                     "defaults:\n  clean-target:\n    past-seasons: 2\n",
                     "defaults:\n  clean-source:\n    exclude: /x\n",
                     "defaults:\n  clean-source:\n    exclude:\n      - /../outside\n",
                     "defaults:\n  clean-source:\n    unexpected: true\n"):
            with self.assertRaises(ConfigError, msg=text):
                self.read_config(text)

    def test_enabled_requires_a_boolean_and_season_count_a_positive_integer(self):
        for section in ("clean-source", "clean-target"):
            for value in ("0", "1", "null", '"true"', "yes", "[]"):
                with self.assertRaises(ConfigError, msg=f"{section}: {value}"):
                    self.read_config(f"defaults:\n  {section}:\n    enabled: {value}\n")
        for value in ("0", "-1", "null", "true", "2.5", "two"):
            with self.assertRaises(ConfigError, msg=value):
                self.read_config(f"defaults:\n  clean-target:\n    past-season-count: {value}\n")

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
        self.cars = [self.car]
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
        # Keep everything recycled by a test in a fake bin, including whole directory trees.
        self.bin = self.root / "recycle-bin"
        self.bin.mkdir()
        self.discarded = []
        self.discard_patch = patch.object(setup_sync, "discard", side_effect=self.fake_recycle)
        self.discard_patch.start()

    def tearDown(self):
        self.discard_patch.stop()
        self.tmp.cleanup()

    def fake_recycle(self, path: Path) -> None:
        resolved = path.resolve()
        car = next((car for car in self.cars if resolved.is_relative_to(car.resolve())), None)
        self.assertIsNotNone(car)
        self.assertNotEqual(resolved, car.resolve())
        self.discarded.append(path.relative_to(self.car if car == self.car else self.root).as_posix())
        path.rename(self.bin / str(len(self.discarded)))

    def another_car(self) -> Path:
        car = self.root / "second-car"
        (car / ECLIPSE / "Spa").mkdir(parents=True)
        (car / "26S3-Spa-Q.sto").write_bytes(b"second car setup")
        (car / ECLIPSE / "Spa/25S1-Spa-Q.sto").write_bytes(b"second car old setup")
        self.cars.append(car)
        return car

    def snapshot(self) -> dict:
        return {p.relative_to(self.root): p.read_bytes() if p.is_file() else None
                for car in self.cars for p in car.rglob("*")}

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

    def run_sync(self, *extra: str, config: Path | None = None,
                 cars: tuple[str, ...] = ("ferrari296gt3",)) -> int:
        scope = [arg for car in cars for arg in ("--car", car)]
        return setup_sync.main(["--season", "26S3", "--season-start", "2026-06-16", "--root", str(self.root),
                                *scope, "--config", str(config or self.config), *extra])

    def nested(self) -> list[str]:
        """Every folder inside a track folder of either target."""
        return sorted(p.relative_to(self.car).as_posix() for team in (self.eclipse, self.rasen)
                      for track in team.iterdir() if track.is_dir() for p in track.rglob("*") if p.is_dir())

    def test_dry_run_changes_nothing(self):
        old = self.write(f"{ECLIPSE}/Imola/25S1-Imola-Q.sto", b"old")
        before = self.snapshot()
        report = self.root / "report.json"
        self.assertEqual(self.run_sync("--dry-run", "--clean-source", "--clean-target", "--clean-target-seasons", "1",
                                       "--report", str(report)), 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.discarded, [])
        plan = json.loads(report.read_text())[0]
        self.assertEqual({Path(p).name for p in plan["clean_source"]},
                         {"Garage 61", "P1Doks"})
        self.assertIn(str(old), [path for team in plan["targets"] for path, _ in team["clean"]])

    def test_default_applies_to_all_eligible_cars_with_configured_cleanup(self):
        second = self.another_car()
        old = self.write(f"{ECLIPSE}/Imola/25S1-Imola-Q.sto", b"old")
        orphan = self.root / "no-targets"
        orphan.mkdir()
        untouched = orphan / "26S3-Spa-Q.sto"
        untouched.write_bytes(b"no target")
        config = self.config_file('  clean-source:\n    enabled: true\n    exclude:\n      - "/Garage 61*"\n'
                                  '  clean-target:\n    enabled: true\n', "enabled.yaml")
        report = self.root / "default-report.json"
        self.assertEqual(self.run_sync("--report", str(report), config=config, cars=()), 0)
        self.assertEqual((second / ECLIPSE / "Spa/26S3-Spa-Q.sto").read_bytes(), b"second car setup")
        self.assertTrue((self.eclipse / "Le Mans/26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())
        self.assertFalse(old.exists())
        self.assertFalse((second / ECLIPSE / "Spa/25S1-Spa-Q.sto").exists())
        self.assertFalse((second / "26S3-Spa-Q.sto").exists())
        self.assertFalse((self.car / "P1Doks").exists())
        self.assertTrue((self.car / "Garage 61").exists())
        self.assertEqual(untouched.read_bytes(), b"no target")
        self.assertEqual({p["car"] for p in json.loads(report.read_text())}, {self.car.name, second.name})

    def test_dry_run_previews_all_cars_by_default_or_with_all_flag(self):
        second = self.another_car()
        config = self.config_file('  clean-source:\n    enabled: true\n  clean-target:\n    enabled: true\n')
        before = self.snapshot()
        report = self.root / "preview.json"
        for scope in ((), ("--all",)):
            with self.subTest(scope=scope):
                self.assertEqual(self.run_sync(*scope, "--dry-run", "--report", str(report),
                                               config=config, cars=()), 0)
                plans = json.loads(report.read_text())
                self.assertEqual({p["car"] for p in plans}, {self.car.name, second.name})
                self.assertTrue(all(p["clean_source"] for p in plans))
                self.assertTrue(any(t["clean"] for p in plans for t in p["targets"]))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.discarded, [])

    def test_car_flag_limits_the_default_all_car_scope(self):
        second = self.another_car()
        self.assertEqual(self.run_sync(), 0)
        self.assertTrue((self.eclipse / "Le Mans/26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())
        self.assertFalse((second / ECLIPSE / "Spa/26S3-Spa-Q.sto").exists())
        self.assertEqual((second / "26S3-Spa-Q.sto").read_bytes(), b"second car setup")

    def test_all_and_car_cannot_be_combined(self):
        before = self.snapshot()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_sync("--all")
        self.assertEqual(self.snapshot(), before)

    def test_apply_copies_flat_reuses_folders_and_overwrites_same_names(self):
        nested = self.nested()
        self.assertEqual(self.run_sync(), 0)
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
        self.assertEqual(self.run_sync(), 0)       # a second run finds nothing to change
        self.assertEqual(self.nested(), nested)

    def test_only_target_dirs_are_excluded_as_sources(self):
        self.write("Garage 61 - Radian Motorsport/Spa/26S3-W03-Radian-Spa-Q.sto", b"theirs")
        self.write(f"{ECLIPSE}/Spa/26S3-TargetOnly-Spa-Q.sto", b"target only")
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual((self.eclipse / "Spa/26S3-W03-Radian-Spa-Q.sto").read_bytes(), b"theirs")
        self.assertEqual(list(self.rasen.rglob("26S3-TargetOnly-Spa-Q.sto")), [])

    def test_target_dir_flag_replaces_the_configured_targets(self):
        self.assertEqual(self.run_sync("--target-dir", f"/{ECLIPSE}"), 0)
        self.assertTrue((self.eclipse / "Le Mans" / "26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())
        self.assertFalse((self.rasen / "LeMans" / "26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())
        (self.car / "Shared" / "Team A").mkdir(parents=True)
        self.assertEqual(self.run_sync("--target-dir", "/Shared/Team A"), 0)
        self.assertTrue((self.car / "Shared" / "Team A" / "Le Mans" / "26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())

    def test_target_cleanup_requires_enabled_or_flag(self):
        old = f"{ECLIPSE}/Imola/25S4-W04-GnG-Imola-Ferrari-Q.sto"
        self.write(old, b"old")
        disabled = self.config_file("  clean-target:\n    enabled: false\n    past-season-count: 2\n", "disabled.yaml")
        self.assertEqual(self.run_sync("--clean-target-seasons", "1", config=disabled), 0)
        self.assertTrue((self.car / old).exists())
        self.assertEqual(self.run_sync("--clean-target", config=disabled), 0)
        self.assertFalse((self.car / old).exists())
        self.write(old, b"old again")
        enabled = self.config_file("  clean-target:\n    enabled: true\n    past-season-count: 2\n", "enabled.yaml")
        self.assertEqual(self.run_sync(config=enabled), 0)
        self.assertFalse((self.car / old).exists())
        self.assertTrue((self.car / "P1Doks").is_dir())
        self.assertIn(f"{ECLIPSE}/Imola", self.discarded)  # empty folders also use the bin

    def test_target_seasons_flag_overrides_enabled_config(self):
        previous = self.write(f"{ECLIPSE}/Spa/26S2-Spa-Q.sto", b"old")
        config = self.config_file("  clean-target:\n    enabled: true\n    past-season-count: 3\n", "clean.yaml")
        self.assertEqual(self.run_sync(config=config), 0)
        self.assertTrue(previous.exists())
        self.assertEqual(self.run_sync("--clean-target-seasons", "1", config=config), 0)
        self.assertFalse(previous.exists())

    def test_same_name_in_different_tracks_is_copied_to_each(self):
        self.write("P1Doks/spa/2026-S3/fixed.sto", b"spa")
        self.write("P1Doks/bathurst/2026-S3/fixed.sto", b"bathurst")
        self.assertEqual(self.run_sync(), 0)
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
        self.assertEqual(self.run_sync("--clean-target", "--clean-target-seasons", "2"), 0)
        self.assertFalse((self.eclipse / "Imola" / "25S4-W04-GnG-Imola-Ferrari-Q.sto").exists())
        self.assertTrue((self.eclipse / "Imola" / "old" / "25S4-W03-GnG-Imola-Ferrari-R.sto").exists())  # subfolder
        self.assertFalse((self.rasen / "Sebring").exists())                                             # emptied
        self.assertTrue((self.eclipse / "P1Doks" / "26S1-W01-P1Doks.sto").exists())                     # not a track
        self.assertTrue((self.eclipse / "26S1-loose.sto").exists())                                     # target root
        self.assertTrue((self.eclipse / "Watkins Glen" / "Teammate_Ferrari296_WatkinsGlen_V1.sto").exists())
        self.assertTrue((self.eclipse / "Spa" / "HYMO_GTS_26S2_F296_Spa_CQ.sto").exists())
        self.assertTrue((self.eclipse / "Spa" / "P1Doks_296GT3_Spa_GTS_E_25S3W4.sto").exists())
        self.assertEqual(self.run_sync("--clean-target", "--clean-target-seasons", "1"), 0)
        self.assertFalse((self.eclipse / "Spa" / "HYMO_GTS_26S2_F296_Spa_CQ.sto").exists())
        self.assertTrue((self.eclipse / "Spa" / "P1Doks_296GT3_Spa_GTS_E_25S3W4.sto").exists())

    def test_clean_needs_at_least_one_season(self):
        for value in ("0", "-1", "two", "1.5"):
            with self.assertRaises(SystemExit):
                self.run_sync("--clean-target", "--clean-target-seasons", value)

    def test_removed_flags_are_rejected(self):
        for flags in (("--clean", "2"), ("--no-clean",), ("--apply",)):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.run_sync(*flags)

    def test_source_cleanup_is_opt_in_and_preserved_folders_are_still_sources(self):
        config = self.config_file('  clean-source:\n    enabled: false\n    exclude:\n      - "/Garage 61*"\n')
        self.write("Garage 61 - Radian Motorsport/Spa/26S3-W03-Radian-Spa-Q.sto", b"theirs")
        self.write("notes.txt", b"notes")
        self.write("misc/unlabelled.sto", b"unlabelled")
        (self.car / "empty").mkdir()
        old_target = self.write(f"{ECLIPSE}/Spa/25S1-Spa-Q.sto", b"old target")
        self.assertEqual(self.run_sync(config=config), 0)
        self.assertTrue((self.car / "notes.txt").exists())
        self.assertTrue((self.car / "P1Doks").exists())
        self.assertEqual(self.run_sync("--clean-source", config=config), 0)
        self.assertEqual({p.name for p in self.car.iterdir()},
                         {ECLIPSE, RASEN, "Garage 61", "Garage 61 - Radian Motorsport"})
        self.assertTrue(old_target.exists())
        self.assertEqual((self.eclipse / "Spa/26S3-W03-Radian-Spa-Q.sto").read_bytes(), b"theirs")
        self.assertEqual((self.eclipse / "Nurb VLN/P1Doks_FerrariGT3_NEC_Ev2_26S2.sto").read_bytes(), b"nec")
        self.assertTrue({"notes.txt", "misc", "empty", "P1Doks"}.issubset(self.discarded))
        self.assertTrue(any(p.read_bytes() == b"notes" for p in self.bin.iterdir() if p.is_file()))

    def test_source_exclude_flag_replaces_config_and_is_repeatable(self):
        config = self.config_file('  clean-source:\n    enabled: true\n    exclude:\n      - "/Garage 61*"\n')
        self.write("notes.txt", b"keep")
        self.assertEqual(self.run_sync("--clean-source-exclude", "/p1DOKS",
                                       "--clean-source-exclude", "/*.txt", config=config), 0)
        self.assertEqual({p.name for p in self.car.iterdir()}, {ECLIPSE, RASEN, "P1Doks", "notes.txt"})
        self.assertTrue((self.eclipse / "Le Mans/26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())

    def test_source_exclude_flag_alone_does_not_enable_cleanup(self):
        before = sorted(p.relative_to(self.car) for p in self.car.rglob("*"))
        self.assertEqual(self.run_sync("--dry-run", "--clean-source-exclude", "/P1Doks"), 0)
        self.assertEqual(sorted(p.relative_to(self.car) for p in self.car.rglob("*")), before)
        report = self.root / "report.json"
        self.assertEqual(self.run_sync("--clean-source-exclude", "/P1Doks", "--report", str(report)), 0)
        self.assertEqual(json.loads(report.read_text())[0]["clean_source"], [])
        self.assertTrue((self.car / "Garage 61").is_dir())

    def test_source_cleanup_preserves_nested_targets_and_glob_matches(self):
        nested = self.car / "Shared/Team [A]"
        nested.mkdir(parents=True)
        self.write("Shared/Team A/not-kept.txt", b"remove")  # target names are literal, not globs
        self.write("Shared/Team [A]/personal.txt", b"keep")
        self.write("Shared/keep-1.txt", b"keep")
        self.write("Shared/notes.txt", b"remove")
        self.write("Providers/archive/keep.sto", b"keep")
        self.write("Providers/other/remove.txt", b"remove")
        self.write("Providers/root.txt", b"remove")
        self.assertEqual(self.run_sync("--clean-source", "--target-dir", "/Shared/Team [A]",
                                       "--clean-source-exclude", "/Shared/keep-?.txt",
                                       "--clean-source-exclude", "/Providers/**/keep.[s]to"), 0)
        self.assertTrue((nested / "Le Mans/26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())
        self.assertTrue((nested / "personal.txt").exists())
        self.assertTrue((self.car / "Shared/keep-1.txt").exists())
        self.assertTrue((self.car / "Providers/archive/keep.sto").exists())
        for removed in (ECLIPSE, RASEN, "Shared/Team A", "Shared/notes.txt", "Providers/other", "Providers/root.txt"):
            self.assertFalse((self.car / removed).exists(), removed)

    def test_source_globs_are_anchored_and_recursive_star_matches_zero_directories(self):
        self.write("keep.txt", b"root")
        self.write("misc/keep.txt", b"nested")
        self.write("Providers/keep.sto", b"zero levels")
        self.assertEqual(self.run_sync("--clean-source", "--clean-source-exclude", "/*.txt",
                                       "--clean-source-exclude", "/Providers/**/keep.sto"), 0)
        self.assertTrue((self.car / "keep.txt").exists())
        self.assertFalse((self.car / "misc").exists())
        self.assertTrue((self.car / "Providers/keep.sto").exists())

    def test_source_cleanup_without_existing_targets_is_skipped(self):
        self.assertEqual(self.run_sync("--clean-source", "--target-dir", "/Missing"), 0)
        self.assertTrue((self.car / "P1Doks").exists())
        self.assertEqual(self.discarded, [])

    def test_copy_failure_prevents_both_cleanups(self):
        old = self.write(f"{ECLIPSE}/Spa/25S1-Spa-Q.sto", b"old")
        with patch.object(setup_sync.shutil, "copy2", side_effect=OSError("copy failed")):
            with self.assertRaisesRegex(OSError, "copy failed"):
                self.run_sync("--clean-source", "--clean-target")
        self.assertTrue(old.exists())
        self.assertTrue((self.car / "P1Doks").exists())
        self.assertEqual(self.discarded, [])

    def test_later_car_copy_failure_preserves_earlier_car_sources_and_old_targets(self):
        old = self.write(f"{ECLIPSE}/Spa/25S1-Spa-Q.sto", b"old")
        second = self.root / "second-car"
        (second / ECLIPSE).mkdir(parents=True)
        source = second / "26S3-Spa-Q.sto"
        source.write_bytes(b"second car")
        copy = setup_sync.shutil.copy2

        def fail_second_car(path, target):
            if path == source:
                raise OSError("second car copy failed")
            return copy(path, target)

        with patch.object(setup_sync.shutil, "copy2", side_effect=fail_second_car):
            with self.assertRaisesRegex(OSError, "second car copy failed"):
                self.run_sync("--car", "second-car", "--clean-source", "--clean-target")
        self.assertTrue((self.eclipse / "Le Mans/26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())
        self.assertTrue((self.car / "Garage 61").exists())
        self.assertTrue((self.car / "P1Doks").exists())
        self.assertTrue(old.exists())
        self.assertTrue(source.exists())

    def test_both_cleanups_apply_after_sync(self):
        old = self.write(f"{ECLIPSE}/Imola/25S1-Imola-Q.sto", b"old")
        self.assertEqual(self.run_sync("--clean-source", "--clean-target"), 0)
        self.assertEqual({p.name for p in self.car.iterdir()}, {ECLIPSE, RASEN})
        self.assertFalse(old.exists())
        self.assertTrue((self.eclipse / "Le Mans/26S3-W12-GnG-LeMans-Ferrari-Q.sto").exists())
        self.assertLess(self.discarded.index(f"{ECLIPSE}/Imola"), self.discarded.index("Garage 61"))

    def test_failed_copy_verification_prevents_source_cleanup(self):
        def corrupt_copy(source, target):
            target.write_bytes(b"bad copy")

        with patch.object(setup_sync.shutil, "copy2", side_effect=corrupt_copy):
            with self.assertRaisesRegex(RuntimeError, "copy verification failed"):
                self.run_sync("--clean-source")
        self.assertTrue((self.car / "P1Doks").exists())
        self.assertTrue((self.car / "Garage 61").exists())

    def test_failed_recycle_does_not_fall_back_to_deletion(self):
        self.assertEqual(self.run_sync(), 0)
        before = sorted(p.relative_to(self.car) for p in self.car.rglob("*"))
        with patch.object(setup_sync, "discard", side_effect=OSError("recycle failed")):
            with self.assertRaisesRegex(OSError, "recycle failed"):
                self.run_sync("--clean-source")
        self.assertEqual(sorted(p.relative_to(self.car) for p in self.car.rglob("*")), before)

    def test_cleanup_cannot_recycle_car_root_or_outside_paths(self):
        outside = self.root / "outside.txt"
        outside.write_bytes(b"keep")
        for path in (outside, self.car):
            with self.assertRaises(ConfigError):
                setup_sync.discard_inside_car(path, self.car)
        self.assertEqual(self.discarded, [])
        self.assertTrue(outside.exists())

    def test_cleanup_refuses_symlinks_before_copying_or_removing_anything(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_bytes(b"keep")
        link = self.car / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink creation unavailable: {error}")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_sync("--clean-source")
        self.assertEqual(self.discarded, [])
        self.assertFalse((self.eclipse / "Le Mans").exists())
        self.assertEqual((outside / "keep.txt").read_bytes(), b"keep")

    def test_unresolved_blocks_apply(self):
        self.write("MG/26S3.NOWHERE.296.R.sto", b"x")
        self.assertEqual(self.run_sync("--clean-source", "--clean-target"), 2)
        self.assertFalse((self.eclipse / "Le Mans").exists())
        self.assertTrue((self.car / "MG/26S3.NOWHERE.296.R.sto").exists())
        self.assertEqual(self.discarded, [])
        self.assertEqual(self.run_sync("--allow-unresolved"), 0)
        self.assertTrue((self.eclipse / "Le Mans").exists())


if __name__ == "__main__":
    unittest.main()
