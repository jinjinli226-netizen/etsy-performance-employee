from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import httpx

from app.core.config import get_settings
from app.db.init_db import init_db
from app.db.session import create_engine_for_url, create_session_factory
from app.employee.adapter import SubprocessHermesAdapter
from app.knowledge.service import KnowledgeService
from app.training.browser import VisibleEtsyBrowser, resolve_visible_browser
from app.training.model import TrainingModel
from app.training.repository import TrainingRepository
from app.training.service import TrainingRunSummary, VisionTrainingService


@dataclass(frozen=True)
class CliOptions:
    workbook: Path
    data_directory: Path | None
    limit: int | None
    delay: float
    shop_substr: str | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.training.cli",
        description="Train abstract Etsy Listing knowledge from text plus the first main image.",
    )
    parser.add_argument("--workbook", required=True, help="Existing .xlsx containing Etsy shop URLs.")
    parser.add_argument("--data-directory", help="Application data directory; defaults to runtime settings.")
    parser.add_argument("--limit", type=int, help="Maximum samples; defaults to 1.")
    parser.add_argument("--batch", action="store_true", help="Process all eligible shops in this run.")
    parser.add_argument("--delay", type=float, default=20, help="Delay after each Etsy page fetch (15-25 seconds).")
    parser.add_argument("--shop", dest="shop_substr", help="Optional case-insensitive shop URL filter.")
    return parser


def parse_options(argv: Sequence[str] | None = None) -> CliOptions:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        workbook = Path(args.workbook).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        parser.error("--workbook must be an existing file")
    if not workbook.is_file() or workbook.suffix.casefold() != ".xlsx":
        parser.error("--workbook must be an existing .xlsx file")
    if not 15 <= args.delay <= 25:
        parser.error("--delay must be between 15 and 25")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if args.batch and args.limit is not None:
        parser.error("--batch and --limit cannot be used together")
    limit = None if args.batch else (args.limit if args.limit is not None else 1)
    data_directory = (
        Path(args.data_directory).expanduser().resolve() if args.data_directory else None
    )
    shop_substr = " ".join(args.shop_substr.split())[:255] if args.shop_substr else None
    return CliOptions(
        workbook=workbook,
        data_directory=data_directory,
        limit=limit,
        delay=args.delay,
        shop_substr=shop_substr,
    )


def make_http_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=False,
        timeout=httpx.Timeout(30, connect=15),
        headers={"User-Agent": "EtsyPerformanceEmployee/vision-training"},
    )


async def run_options(options: CliOptions) -> TrainingRunSummary:
    settings = get_settings()
    if options.data_directory is not None:
        settings = settings.model_copy(
            update={"data_dir": options.data_directory, "database_url": None}
        )
    settings.ensure_runtime_dirs()
    engine = create_engine_for_url(settings.resolved_database_url)
    try:
        init_db(engine)
        session_factory = create_session_factory(engine)
        employee = SubprocessHermesAdapter(
            executable=settings.hermes_executable,
            profile=settings.hermes_profile,
            timeout_seconds=settings.hermes_timeout_seconds,
            data_root=settings.data_dir,
            max_turns=settings.hermes_max_turns,
        )
        knowledge = KnowledgeService(
            session_factory,
            export_dir=settings.data_dir / "trust",
            originality_threshold=settings.originality_threshold,
        )

        employee.check_available()
        browser_path = resolve_visible_browser()
        knowledge.require_capacity_ready()

        repository = TrainingRepository(session_factory)
        model = TrainingModel(employee)
        with VisibleEtsyBrowser(
            settings.data_dir,
            browser_path=browser_path,
        ) as browser, make_http_client() as client:
            service = VisionTrainingService(
                repository=repository,
                knowledge=knowledge,
                model=model,
                browser=browser,
                image_client=client,
                evidence_root=settings.data_dir / "training-evidence",
                sleeper=asyncio.sleep,
            )
            return await service.run(
                options.workbook,
                limit=options.limit,
                delay=options.delay,
                shop_substr=options.shop_substr,
            )
    finally:
        engine.dispose()


def _safe_summary(summary: TrainingRunSummary) -> dict[str, object]:
    allowed = {"completed", "failed", "skipped"}
    counts = {
        key: int(value)
        for key, value in sorted(summary.counts.items())
        if key in allowed and isinstance(value, int) and not isinstance(value, bool) and value >= 0
    }
    return {
        "run_id": summary.public_id[:64],
        "status": summary.status[:32],
        "counts": counts,
    }


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_options(argv)
    try:
        summary = asyncio.run(run_options(options))
    except KeyboardInterrupt:
        print(json.dumps({"status": "failed", "code": "training_interrupted"}), file=sys.stderr)
        return 130
    except Exception:
        print(json.dumps({"status": "failed", "code": "training_failed"}), file=sys.stderr)
        return 1
    print(json.dumps(_safe_summary(summary), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
