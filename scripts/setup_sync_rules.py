"""Season and track inference for setup-sync: pure functions over names, no filesystem access.

Setup providers name things inconsistently (`26S3 W04 Spa24 Ferrari`, `P1Doks\\spa\\2026-S3`,
`spa 2024 up\\26s3`, `Track Titan\\2026\\Season 3\\Week 4\\Circuit de Spa-Francorchamps - GP Pits`).
This module turns a car-relative path into a season label and a destination track folder, and says
how it knew, so the caller can report every guess.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

# Longest alias in tracks.json spans five tokens ("streets of st petersburg"); one spare.
MAX_ALIAS_TOKENS = 6
# Providers publish the next season's week-1 setups during the previous week 13, so a file
# dated up to one week before the season opened can belong to it.
WEEK13_GRACE = timedelta(days=7)
# Two-digit season years outside [2010, current + 1] are part of some other number (296, 992...).
OLDEST_SEASON_YEAR = 2010
SEASONS_PER_YEAR = 4
MARKER_PRIORITY_LAYOUT = 2      # the word names a layout (VLN, 24h, Sprintstrecke)
MARKER_PRIORITY_WEAK = 1        # the word only suggests one (DTM, Creventic, a bare "Nordschleife")

_TOKEN = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def fold(text: str) -> str:
    """Strip accents (Nürburgring -> Nurburgring) without changing case."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    """Lowercase word tokens, splitting camelCase and letter/digit runs: RAmerica6H -> r america 6 h."""
    return [t.lower() for t in _TOKEN.findall(fold(text))]


def compact(text: str) -> str:
    return "".join(tokens(text))


# --------------------------------------------------------------------------- seasons

@dataclass(frozen=True, order=True)
class Season:
    year: int
    number: int

    def __str__(self) -> str:
        return f"{self.year % 100:02d}S{self.number}"

    def shifted(self, seasons: int) -> "Season":
        """The season `seasons` later (negative: earlier), across year boundaries."""
        index = self.year * SEASONS_PER_YEAR + self.number - 1 + seasons
        return Season(index // SEASONS_PER_YEAR, index % SEASONS_PER_YEAR + 1)


_FULL_LABELS = (
    re.compile(r"(?<![0-9])(?P<y>\d{2})\s?[Ss](?P<s>[1-4])(?![0-9])"),              # 26S3, 26s3, 26S3W12
    re.compile(r"(?<![0-9])(?P<y>20\d{2})\s*[-_ ]?\s*[Ss](?:eason)?\s*[-_ ]?\s*(?P<s>[1-4])(?![0-9])",
               re.IGNORECASE),                                                       # 2026-S3, 2026 Season 3
    re.compile(r"(?<![A-Za-z0-9])VRS_(?P<y>\d{2})(?P<s>[1-4])(?=[A-Za-z_])", re.IGNORECASE),  # VRS_263_, VRS_263RB_
)
_SEASON_ONLY = re.compile(r"^(?:season|s)\s*-?\s*([1-4])$", re.IGNORECASE)          # "Season 3" under "2026"
_YEAR_WHOLE = re.compile(r"^(20\d{2})$")
_YEAR_LEAD = re.compile(r"^(20\d{2})(?![0-9])\s*\S")                                # 2026 season, 2026 iRacing SPA 24h
_YEAR_TRAIL = re.compile(r"\s(20\d{2})$")                                           # iRacing DTM Series 2026
_YY_SERIES = re.compile(r"^(\d{2})\s?[A-Za-z]{2,}$")                                # 26 dtm, 26nec (under a track folder)


def parse_season(text: str) -> Season:
    """Parse a --season argument: 26S3, 2026S3, 2026-S3 or '2026 Season 3'."""
    label = full_label(text.strip(), max_year=9999)
    if label is None:
        raise ValueError(f"not a season label: {text!r} (expected e.g. 26S3)")
    return label


def full_label(text: str, max_year: int) -> Season | None:
    """The newest year+season label in a name, or None."""
    found = []
    for pattern in _FULL_LABELS:
        for match in pattern.finditer(text):
            year = int(match["y"])
            year += 2000 if year < 100 else 0
            if OLDEST_SEASON_YEAR <= year <= max_year:
                found.append(Season(year, int(match["s"])))
    return max(found, default=None)


def year_label(text: str, under_track_folder: bool) -> int | None:
    """A year-only label (no season number) on a folder name, or None."""
    for pattern in (_YEAR_WHOLE, _YEAR_LEAD, _YEAR_TRAIL):
        match = pattern.search(text)
        if match:
            return int(match[1])
    if under_track_folder:
        match = _YY_SERIES.match(text)
        if match:
            return 2000 + int(match[1])
    return None


@dataclass(frozen=True)
class SeasonEvidence:
    season: Season | None = None    # full label, when one applies
    year: int | None = None         # year-only label, when no full label applies
    source: str = ""                # the name the label came from


def season_evidence(folders: list[str], stem: str, track_folder: list[bool], max_year: int) -> SeasonEvidence:
    """Decide which label governs a file.

    The deepest folder carrying a full label wins over the file name: providers file carry-overs
    under the season they are offered for (P1Doks ships a 25S3W8 Le Mans setup in its 2026-S3
    folder). A file-name label refines a year-only folder (`2026 season\\VRS_26S2...`). Track folder
    names are never read for years (`spa 2024 up`, `watkinsglen 2021 fullcourse`).
    """
    deepest_full: SeasonEvidence | None = None
    deepest_year: SeasonEvidence | None = None
    ancestor_year: int | None = None
    for index, name in enumerate(folders):
        if track_folder[index]:
            continue
        label = full_label(name, max_year)
        only = _SEASON_ONLY.match(name.strip())
        if label is None and only and ancestor_year is not None:
            label = Season(ancestor_year, int(only[1]))
        if label is not None:
            deepest_full = SeasonEvidence(season=label, source=name)
            continue
        under_track = index > 0 and track_folder[index - 1]
        year = year_label(name.strip(), under_track)
        if year is not None and OLDEST_SEASON_YEAR <= year <= max_year:
            if _YEAR_WHOLE.match(name.strip()):
                ancestor_year = year
            deepest_year = SeasonEvidence(year=year, source=name)
    if deepest_full is not None:
        return deepest_full
    label = full_label(stem, max_year)
    if label is not None:
        return SeasonEvidence(season=label, source=stem)
    return deepest_year or SeasonEvidence()


@dataclass(frozen=True)
class SeasonDecision:
    include: bool
    reason: str
    inferred: bool = False


def decide_season(evidence: SeasonEvidence, current: Season, season_start: date | None,
                  modified: date) -> SeasonDecision:
    """Include the current season or newer; late-season files are often labelled for the next one."""
    if evidence.season is not None:
        if evidence.season >= current:
            return SeasonDecision(True, f"{evidence.season} from '{evidence.source}'")
        return SeasonDecision(False, f"{evidence.season} < {current}")
    if evidence.year is not None:
        if evidence.year > current.year:
            return SeasonDecision(True, f"year {evidence.year} from '{evidence.source}'")
        if evidence.year < current.year:
            return SeasonDecision(False, f"year {evidence.year} < {current.year}")
        if season_start is None:
            return SeasonDecision(False, f"year-only '{evidence.source}' and no --season-start to date it")
        cutoff = season_start - WEEK13_GRACE
        if modified >= cutoff:
            return SeasonDecision(True, f"year-only '{evidence.source}', file dated {modified} >= {cutoff}",
                                  inferred=True)
        return SeasonDecision(False, f"year-only '{evidence.source}', file dated {modified} < {cutoff}")
    return SeasonDecision(False, "no season label")


# --------------------------------------------------------------------------- tracks

@dataclass(frozen=True)
class TrackMatch:
    family: str
    variant: str | None     # None: the family's base layout, or undetermined when the family has no base
    explicit: bool          # the name states the layout (exact dirname, variant alias or layout marker)
    full: bool              # every token is accounted for; required to reuse a destination folder


@dataclass(frozen=True)
class Resolution:
    family: str | None
    variant: str | None = None
    how: str = ""
    note: str = ""


@dataclass
class Family:
    name: str
    base: bool = True
    variants: dict[str, str] = field(default_factory=dict)   # variant name -> split policy


class TrackTable:
    """Loaded tracks.json: alias/exact/marker indexes plus learned path rules."""

    def __init__(self, data: dict):
        self.families: dict[str, Family] = {}
        self.aliases: dict[str, tuple[str, str | None]] = {}
        self.exact: dict[str, tuple[str, str | None]] = {}
        self.markers: dict[str, dict[str, tuple[str, int]]] = {}
        self.destinations: dict[str, tuple[str, str | None]] = {}
        self.ignore = {compact(t) for t in data.get("ignore_tokens", [])}
        for name, spec in data["tracks"].items():
            family = Family(name, base=spec.get("base", True))
            self.families[name] = family
            self._index(spec, name, None)
            if family.base:
                self.destinations[name] = (name, None)
            for variant, vspec in spec.get("variants", {}).items():
                family.variants[variant] = vspec.get("split", "always")
                self._index(vspec, name, variant)
                self.destinations[variant] = (name, variant)
                table = self.markers.setdefault(name, {})
                for marker in vspec.get("markers", []):
                    table[compact(marker)] = (variant, MARKER_PRIORITY_LAYOUT)
                for marker in vspec.get("weak_markers", []):
                    table[compact(marker)] = (variant, MARKER_PRIORITY_WEAK)
        self.rules = [(re.compile(rule["pattern"], re.IGNORECASE), rule) for rule in data.get("rules", [])]

    def _index(self, spec: dict, family: str, variant: str | None) -> None:
        for alias in spec.get("aliases", []):
            key = compact(alias)
            if key in self.aliases and self.aliases[key] != (family, variant):
                raise ValueError(f"alias {alias!r} maps to both {self.aliases[key]} and {(family, variant)}")
            self.aliases[key] = (family, variant)
        for exact in spec.get("exact", []):
            self.exact[compact(exact)] = (family, variant)

    @classmethod
    def load(cls, path: Path) -> "TrackTable":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def resolve(self, name: str) -> TrackMatch | None:
        """Match one folder or file name. Ambiguous (two families) and unknown names return None."""
        whole = compact(name)
        if whole in self.exact:
            family, variant = self.exact[whole]
            return TrackMatch(family, variant, explicit=True, full=True)
        words = tokens(name)
        spans = []
        for start in range(len(words)):
            for length in range(min(MAX_ALIAS_TOKENS, len(words) - start), 0, -1):
                key = "".join(words[start:start + length])
                if key in self.aliases:
                    spans.append((start, start + length, key))
        spans.sort(key=lambda span: (-len(span[2]), span[0]))
        covered: set[int] = set()
        chosen = []
        for start, end, key in spans:
            if covered.isdisjoint(range(start, end)):
                chosen.append(key)
                covered.update(range(start, end))
        families = {self.aliases[key][0] for key in chosen}
        if len(families) != 1:
            return None
        family = families.pop()
        variants = {self.aliases[key][1] for key in chosen} - {None}
        hits, marker_covered = self._marker_hits(family, words, covered)
        covered |= marker_covered
        full = all(i in covered or words[i] in self.ignore or words[i].isdigit() for i in range(len(words)))
        if len(variants) == 1:
            return TrackMatch(family, variants.pop(), explicit=True, full=full)
        marker_variant = self._strongest(hits)
        if not variants and marker_variant is not None and hits[marker_variant] >= MARKER_PRIORITY_LAYOUT:
            return TrackMatch(family, marker_variant, explicit=True, full=full)
        # Weak markers only suggest a layout: resolve_file weighs them against the whole path.
        return TrackMatch(family, None, explicit=False, full=full and not variants)

    def _marker_hits(self, family: str, words: list[str], skip: set[int]) -> tuple[dict[str, int], set[int]]:
        """Variants named by markers in one name, each at the strongest priority actually matched."""
        table = self.markers.get(family, {})
        hits: dict[str, int] = {}
        covered: set[int] = set()
        for start in range(len(words)):
            for length in range(min(MAX_ALIAS_TOKENS, len(words) - start), 0, -1):
                span = range(start, start + length)
                key = "".join(words[start:start + length])
                if key in table and skip.isdisjoint(span):
                    variant, priority = table[key]
                    hits[variant] = max(priority, hits.get(variant, 0))
                    covered.update(span)
        return hits, covered

    @staticmethod
    def _strongest(hits: dict[str, int]) -> str | None:
        """The single variant at the top priority; a tie is no answer."""
        if not hits:
            return None
        top = max(hits.values())
        winners = [variant for variant, priority in hits.items() if priority == top]
        return winners[0] if len(winners) == 1 else None

    def vote_markers(self, family: str, names: list[str]) -> str | None:
        """The one variant that layout markers (or, failing those, weak markers) across names agree on."""
        pooled: dict[str, int] = {}
        for name in names:
            hits, _ = self._marker_hits(family, tokens(name), set())
            for variant, priority in hits.items():
                pooled[variant] = max(priority, pooled.get(variant, 0))
        return self._strongest(pooled)

    def rule_for(self, relative: str) -> dict | None:
        for pattern, rule in self.rules:
            if pattern.search(relative):
                return rule
        return None

    def resolve_file(self, folders: list[str], stem: str) -> Resolution:
        """Resolve a file from its car-relative folders and file stem.

        The deepest folder naming a track carries the family. Its layout is kept when the folder
        states one; otherwise markers anywhere in the path or file name decide
        (`P1Doks\\nurburgring\\...\\P1Doks_FerrariGT3_NEC_...` -> Nurb VLN). A file name alone carries
        the track only when no folder does (flat MG/CDA folders).
        """
        rule = self.rule_for("/".join([*folders, stem]))
        if rule and rule.get("track"):
            family, variant = self.destinations[rule["track"]]
            return Resolution(family, variant, how="rule", note=rule.get("why", ""))
        carriers = [(i, m) for i, m in ((i, self.resolve(f)) for i, f in enumerate(folders)) if m]
        note = ""
        if carriers:
            family = carriers[-1][1].family
            if len({m.family for _, m in carriers}) > 1:
                note = "folders name different tracks; deepest wins"
            explicit = [m for _, m in carriers if m.family == family and m.explicit]
            if explicit:
                return Resolution(family, explicit[-1].variant, how="folder", note=note)
            variant = self.vote_markers(family, [*folders, stem]) if family in self.markers else None
            return Resolution(family, variant, how="folder+markers" if variant else "folder", note=note)
        match = self.resolve(stem)
        if match is None:
            return Resolution(None, how="unresolved", note="no folder or file name names a known track")
        if match.explicit:
            return Resolution(match.family, match.variant, how="file name")
        variant = self.vote_markers(match.family, [stem, *folders]) if match.family in self.markers else None
        return Resolution(match.family, variant, how="file name")

    def is_track_folder(self, name: str) -> bool:
        match = self.resolve(name)
        return match is not None and match.full

    def canonical(self, family: str, variant: str | None) -> str | None:
        """Destination folder name for a layout, or None when the family needs a layout and has none."""
        if variant is not None:
            return variant
        return family if self.families[family].base else None
