from __future__ import annotations

import argparse
import json
from pathlib import Path

from .authority import build_chapter_authority, build_member_authority
from .llm import OpenAIModel, StubModel
from .pipeline import Run


def main() -> None:
    p = argparse.ArgumentParser(prog="gpm")
    p.add_argument("--pdfs", type=Path, default=Path("data/input"))
    p.add_argument("--members", type=Path, default=Path("data/authority/members.xlsx"))
    p.add_argument("--chapters", type=Path, default=Path("data/authority/chapters.xlsx"))
    p.add_argument("--nicknames", type=Path, default=None)
    p.add_argument("--taxonomy", type=Path, default=None)
    p.add_argument("--prompt", type=Path, default=Path("prompts/page_metadata.md"))
    p.add_argument("--out", type=Path, default=Path("data/output"))
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--dry-run", action="store_true",
                   help="use the offline stub model; no API key needed")
    a = p.parse_args()

    nicknames = json.loads(a.nicknames.read_text()) if a.nicknames else None
    taxonomy = json.loads(a.taxonomy.read_text()) if a.taxonomy else None

    run = Run(
        members=build_member_authority(a.members, nicknames),
        chapters=build_chapter_authority(a.chapters),
        model=StubModel() if a.dry_run else OpenAIModel(a.model),
        prompt=a.prompt.read_text(),
        taxonomy=taxonomy,
        out_dir=a.out,
    )
    df = run.process_folder(a.pdfs)
    print(df.to_string(index=False))
    print(f"\nwrote {a.out.resolve()}")


if __name__ == "__main__":
    main()
