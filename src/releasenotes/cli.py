import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from importlib.metadata import entry_points
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .config import load_config, starter_yaml
from .pipeline.generator import ReleaseNotePipeline
from .schemas.change_event import ChangeEvent
from .schemas.change_group import ChangeGroup

console = Console()

_SINCE_RE = re.compile(r"^(\d+)(h|d)$", re.IGNORECASE)


def _parse_since(value: str) -> datetime:
    match = _SINCE_RE.match(value.strip())
    if not match:
        raise click.BadParameter(f"Expected format like '24h' or '2d', got: '{value}'")
    n, unit = int(match.group(1)), match.group(2).lower()
    delta = timedelta(hours=n) if unit == "h" else timedelta(days=n)
    return datetime.now(timezone.utc) - delta


def _print_fetched(events: list[ChangeEvent], groups: list[ChangeGroup]) -> None:
    # Map internal ce_ IDs → human-readable source IDs (e.g. "RN-1", "#267", "abc1234")
    id_to_source: dict[str, str] = {e.id: e.source_id for e in events}

    # Table 1: every raw event ingested
    ev_table = Table(title="Fetched events", show_lines=True)
    ev_table.add_column("Type", style="cyan", no_wrap=True)
    ev_table.add_column("System", style="dim")
    ev_table.add_column("ID", no_wrap=True)
    ev_table.add_column("Title")
    ev_table.add_column("Author", style="dim")
    ev_table.add_column("Links", style="dim")

    for e in events:
        ticket_ids = [id_to_source.get(link["id"], link["id"]) for link in e.linked_ids.get("tickets", [])]
        pr_ids = [id_to_source.get(link["id"], link["id"]) for link in e.linked_ids.get("prs", [])]
        tickets = ", ".join(ticket_ids)
        prs = ", ".join(pr_ids)
        links = " | ".join(filter(None, [f"tickets:{tickets}" if tickets else "", f"prs:{prs}" if prs else ""]))
        ev_table.add_row(e.source_type, e.source_system, e.source_id[:12], e.title[:80], e.author_name, links or "-")

    console.print(ev_table)

    # Table 2: deduplication groups — shows JIRA<->GitHub correlation
    grp_table = Table(title="Change groups (after correlation + deduplication)", show_lines=True)
    grp_table.add_column("Group ID", style="cyan", no_wrap=True)
    grp_table.add_column("Class", no_wrap=True)
    grp_table.add_column("JIRA Ticket", style="green")
    grp_table.add_column("GitHub PRs", style="blue")
    grp_table.add_column("Commits", style="dim")
    grp_table.add_column("Title")

    for g in groups:
        ticket = g.source_ticket.source_id if g.source_ticket else "-"
        prs = ", ".join(f"#{p.source_id}" for p in g.source_prs) or "-"
        commits = ", ".join(c.source_id[:7] for c in g.source_commits) or "-"
        cls = (g.classification or "?").replace("_", " ")
        grp_table.add_row(g.id, cls, ticket, prs, commits, g.canonical_title[:70])

    console.print(grp_table)


@click.group()
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@main.command()
@click.option("--from-tag", default=None, help="Start git tag or ISO datetime")
@click.option("--to-tag", default=None, help="End git tag or ISO datetime")
@click.option("--since", default=None, help="Fetch changes from the last N hours/days, e.g. 24h or 2d")
@click.option("--config", "config_path", default="releasenotes.yaml")
@click.option("--provider", default=None, help="Override LLM provider from config")
@click.option("--format", "formats", default=None, multiple=True)
@click.option("--dry-run", is_flag=True, help="Skip LLM call, show classified groups")
@click.option("--show-fetched", is_flag=True, help="Print all fetched events and their JIRA/GitHub links")
def generate(
    from_tag: str | None,
    to_tag: str | None,
    since: str | None,
    config_path: str,
    provider: str | None,
    formats: tuple[str, ...],
    dry_run: bool,
    show_fetched: bool,
):
    if since and (from_tag or to_tag):
        raise click.UsageError("Use either --since OR --from-tag/--to-tag, not both.")
    if not since and not (from_tag and to_tag):
        raise click.UsageError("Provide --since (e.g. --since 24h) or both --from-tag and --to-tag.")

    if since:
        since_dt = _parse_since(since)
        from_ref = since_dt.isoformat()
        to_ref = datetime.now(timezone.utc).date().isoformat()
    else:
        from_ref = from_tag
        to_ref = to_tag

    settings = load_config(config_path)
    if provider:
        settings.llm.provider = provider
    if formats:
        settings.output.formats = list(formats)

    pipeline = ReleaseNotePipeline(settings)
    notes = asyncio.run(pipeline.generate(from_ref, to_ref, dry_run=dry_run))

    if show_fetched:
        _print_fetched(pipeline.fetched_events, pipeline.change_groups)

    console.print(notes.summary())


@main.command()
def init():
    """Write a starter releasenotes.yaml to current directory."""
    path = Path("releasenotes.yaml")
    if path.exists():
        raise click.ClickException("releasenotes.yaml already exists")
    path.write_text(starter_yaml())
    console.print("Created releasenotes.yaml")


@main.command()
def providers():
    """List all installed LLM providers, ingestors, and formatters."""
    table = Table(title="Installed releasenotes plugins")
    table.add_column("Type")
    table.add_column("Name")
    table.add_column("Target")
    for group, label in [
        ("releasenotes.llm_providers", "LLM"),
        ("releasenotes.ingestors", "Ingestor"),
        ("releasenotes.formatters", "Formatter"),
    ]:
        for ep in entry_points(group=group):
            table.add_row(label, ep.name, ep.value)
    console.print(table)


@main.command()
@click.option("--config", "config_path", default="releasenotes.yaml")
def schedule(config_path: str):
    """Start the APScheduler background process."""
    from .scheduler.cron import run_scheduler

    asyncio.run(run_scheduler(load_config(config_path)))


if __name__ == "__main__":
    main()
