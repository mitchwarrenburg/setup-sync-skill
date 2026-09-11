#!/usr/bin/env python3
"""Install setup-sync as a personal skill for Claude Code and Codex.

Each agent gets a thin entry point in its user skill folder that points back at this checkout, so
there is one copy of the skill, its scripts, its config and the track vocabulary it learns:

    Claude Code  ~/.claude/skills/setup-sync/SKILL.md
    Codex        ~/.agents/skills/setup-sync/SKILL.md (+ agents/openai.yaml)

Run it again after moving this checkout. --uninstall removes the entry points it wrote.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
SKILL_NAME = "setup-sync"
LOCATIONS = {
    "Claude Code": Path.home() / ".claude" / "skills" / SKILL_NAME,
    "Codex": Path.home() / ".agents" / "skills" / SKILL_NAME,
}
MARKER = "<!-- Installed by setup-sync-skill/install.py; edit the checkout, not this file. -->"


def frontmatter() -> str:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise SystemExit(f"{SKILL_DIR / 'SKILL.md'} has no frontmatter")
    return text.split("\n---\n", 1)[0] + "\n---\n"


def entry_point() -> str:
    home = SKILL_DIR.as_posix()
    return (f"{frontmatter()}\n{MARKER}\n\n"
            f"This skill lives in `{home}`. Read and follow `{home}/SKILL.md`; its `<SKILL_DIR>` is `{home}`.\n"
            f"Run its helper from there (`python \"{home}/scripts/setup_sync.py\" ...`) and make every edit it\n"
            f"asks for (`tracks.json`, `setup-sync.yaml`) in `{home}`, never in this folder.\n")


def ours(folder: Path) -> bool:
    skill = folder / "SKILL.md"
    return skill.is_file() and MARKER in skill.read_text(encoding="utf-8")


def install() -> None:
    for agent, folder in LOCATIONS.items():
        if folder.exists() and any(folder.iterdir()) and not ours(folder):
            raise SystemExit(f"{folder} already holds a different skill; move it aside first")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(entry_point(), encoding="utf-8")
        if agent == "Codex":        # UI metadata read by the Codex app
            (folder / "agents").mkdir(exist_ok=True)
            shutil.copyfile(SKILL_DIR / "agents" / "openai.yaml", folder / "agents" / "openai.yaml")
        print(f"{agent}: {folder} -> {SKILL_DIR}")


def uninstall() -> None:
    for agent, folder in LOCATIONS.items():
        if not ours(folder):
            print(f"{agent}: nothing installed at {folder}")
            continue
        for path in (folder / "agents" / "openai.yaml", folder / "SKILL.md"):
            path.unlink(missing_ok=True)
        for path in (folder / "agents", folder):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        print(f"{agent}: removed {folder}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uninstall", action="store_true", help="remove the entry points")
    uninstall() if parser.parse_args().uninstall else install()


if __name__ == "__main__":
    main()
