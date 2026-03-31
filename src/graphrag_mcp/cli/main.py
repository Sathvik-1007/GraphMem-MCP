"""Click CLI group with management commands for graphrag-mcp.

Provides subcommands for starting the MCP server, initializing the
database, inspecting graph statistics, exporting/importing data,
validating integrity, and installing agent skills.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import click

from graphrag_mcp import __version__
from graphrag_mcp.utils import GraphRAGError, get_logger, load_config, setup_logging

log = get_logger("cli")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def _resolve_db_path(
    db_path: str | None = None,
    project_dir: str | None = None,
) -> None:
    """Set GRAPHRAG_DB_PATH from explicit flags.

    Priority (highest wins):
    1. ``--db`` — explicit database file path
    2. ``--project-dir`` — resolve ``<dir>/.graphrag/graph.db``
    3. ``GRAPHRAG_DB_PATH`` environment variable (left untouched)
    4. Config default: ``.graphrag/graph.db`` relative to CWD
    """
    if db_path:
        os.environ["GRAPHRAG_DB_PATH"] = str(Path(db_path).resolve())
    elif project_dir:
        resolved = Path(project_dir).resolve() / ".graphrag" / "graph.db"
        os.environ["GRAPHRAG_DB_PATH"] = str(resolved)


async def _open_db(
    db_path: str | None = None,
    project_dir: str | None = None,
) -> tuple[Any, Any]:
    """Open storage backend and return (StorageBackend, GraphEngine) for CLI commands.

    Lazily imports heavy modules so that lightweight commands (like
    ``--version``) stay fast. The returned "db" object is a StorageBackend
    instance (typically SQLiteBackend) that CLI commands can use for
    raw queries, and the GraphEngine is wired to the same backend.
    """
    from graphrag_mcp.graph import GraphEngine
    from graphrag_mcp.storage import create_backend

    _resolve_db_path(db_path, project_dir)

    config = load_config()
    storage = create_backend(config.backend_type, db_path=config.ensure_db_dir())
    await storage.initialize()
    graph = GraphEngine(storage)
    return storage, graph


def _print_error(message: str) -> None:
    click.secho(f"Error: {message}", fg="red", err=True)


# ── CLI Group ────────────────────────────────────────────────────────────────


@click.group()
@click.version_option(version=__version__, prog_name="graphrag-mcp")
def cli() -> None:
    """graphrag-mcp: Production-grade Graph-RAG MCP server with local embeddings."""
    setup_logging()


# ── server ───────────────────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--transport",
    default="stdio",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    help="MCP transport protocol.",
)
@click.option(
    "--db",
    "db_path",
    default=None,
    type=click.Path(),
    help="Path to the SQLite database file.",
)
@click.option(
    "--project-dir",
    "project_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Project root directory. Database stored at <dir>/.graphrag/graph.db.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Bind address for SSE / HTTP transports.",
)
@click.option(
    "--port",
    default=8080,
    type=int,
    show_default=True,
    help="Port for SSE / HTTP transports.",
)
def server(
    transport: str,
    db_path: str | None,
    project_dir: str | None,
    host: str,
    port: int,
) -> None:
    """Start the MCP server."""
    try:
        # Propagate CLI options into environment so that Config picks them up.
        os.environ["GRAPHRAG_TRANSPORT"] = transport
        _resolve_db_path(db_path, project_dir)

        from graphrag_mcp.server import run  # lazy import — heavy deps

        run(transport=transport, host=host, port=port)
    except GraphRAGError as exc:
        log.debug("Server command failed: %s", exc, exc_info=True)
        _print_error(str(exc))
        raise SystemExit(1) from exc


# ── install ──────────────────────────────────────────────────────────────────

_SUPPORTED_AGENTS = ("claude", "opencode", "codex", "gemini", "cursor", "windsurf", "amp")


@cli.command()
@click.argument("agent", type=click.Choice(_SUPPORTED_AGENTS))
@click.option(
    "--global",
    "scope",
    flag_value="global",
    help="Install the skill globally.",
)
@click.option(
    "--project",
    "scope",
    flag_value="project",
    default=True,
    help="Install the skill for the current project (default).",
)
@click.option(
    "--project-dir",
    "project_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Project root directory for skill installation.",
)
def install(agent: str, scope: str, project_dir: str | None) -> None:
    """Install agent skill configuration for AGENT.

    Writes the appropriate MCP configuration file so that the
    selected coding agent can discover graphrag-mcp.
    """
    try:
        from graphrag_mcp.cli.install import install_skill  # lazy import

        pd = Path(project_dir) if project_dir else None
        result_path: Path = install_skill(agent=agent, scope=scope, project_dir=pd)
        click.secho(f"Installed {agent} skill ({scope}):", fg="green")
        click.echo(f"  {result_path}")
    except GraphRAGError as exc:
        log.debug("Install command failed: %s", exc, exc_info=True)
        _print_error(str(exc))
        raise SystemExit(1) from exc


# ── init ─────────────────────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--db",
    "db_path",
    default=None,
    type=click.Path(),
    help="Custom database path (default: .graphrag/graph.db).",
)
@click.option(
    "--project-dir",
    "project_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Project root directory. Database stored at <dir>/.graphrag/graph.db.",
)
def init(db_path: str | None, project_dir: str | None) -> None:
    """Initialize a .graphrag directory and database."""

    async def _init() -> None:
        from graphrag_mcp.storage import create_backend

        _resolve_db_path(db_path, project_dir)

        config = load_config()
        resolved = config.ensure_db_dir()

        storage = create_backend(config.backend_type, db_path=resolved)
        await storage.initialize()
        version = await storage.get_schema_version()
        await storage.close()

        click.secho("Initialized graphrag-mcp database.", fg="green")
        click.echo(f"  Database: {resolved}")
        click.echo(f"  Backend: {config.backend_type}")
        click.echo(f"  Schema version: {version}")

    try:
        _run_async(_init())
    except GraphRAGError as exc:
        log.debug("Init command failed: %s", exc, exc_info=True)
        _print_error(str(exc))
        raise SystemExit(1) from exc


# ── status ───────────────────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--db",
    "db_path",
    default=None,
    type=click.Path(exists=True),
    help="Path to the SQLite database file.",
)
@click.option(
    "--project-dir",
    "project_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Project root directory containing .graphrag/graph.db.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Output raw JSON instead of formatted text.",
)
def status(db_path: str | None, project_dir: str | None, as_json: bool) -> None:
    """Show graph statistics."""

    async def _status() -> dict[str, Any]:
        storage, graph = await _open_db(db_path, project_dir)
        try:
            stats = await graph.get_stats()
            stats["schema_version"] = await storage.get_schema_version()
            return stats
        finally:
            await storage.close()

    try:
        stats = _run_async(_status())
    except GraphRAGError as exc:
        _print_error(str(exc))
        raise SystemExit(1) from exc

    if as_json:
        click.echo(json.dumps(stats, indent=2, default=str))
        return

    # Pretty-print
    separator = "\u2500" * 36
    click.echo(f"graphrag-mcp status\n{separator}")
    click.echo(f"{'Entities:':<20}{stats['entities']:>8}")
    click.echo(f"{'Relationships:':<20}{stats['relationships']:>8}")
    click.echo(f"{'Observations:':<20}{stats['observations']:>8}")
    click.echo(f"{'Schema version:':<20}{stats['schema_version']:>8}")

    entity_types: dict[str, int] = stats.get("entity_types", {})
    if entity_types:
        click.echo(f"\n{'Entity types:'}")
        for etype, count in entity_types.items():
            click.echo(f"  {etype:<20}{count:>6}")

    most_connected: list[dict[str, Any]] = stats.get("most_connected", [])
    if most_connected:
        click.echo(f"\n{'Most connected:'}")
        for node in most_connected:
            label = str(node["name"])
            degree = int(node["degree"])
            click.echo(f"  {label:<20}{degree:>6} connections")


# ── export ───────────────────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--db",
    "db_path",
    default=None,
    type=click.Path(exists=True),
    help="Path to the SQLite database file.",
)
@click.option(
    "--project-dir",
    "project_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Project root directory containing .graphrag/graph.db.",
)
@click.option(
    "--format",
    "fmt",
    default="json",
    type=click.Choice(["json"]),
    show_default=True,
    help="Export format.",
)
@click.option(
    "--output",
    "output_path",
    default=None,
    type=click.Path(),
    help="Output file path (defaults to stdout).",
)
def export(
    db_path: str | None,
    project_dir: str | None,
    fmt: str,
    output_path: str | None,
) -> None:
    """Export all graph data."""

    async def _export() -> dict[str, Any]:
        storage, graph = await _open_db(db_path, project_dir)
        try:
            entities = await graph.list_entities(limit=1_000_000)
            all_relationships: list[dict[str, Any]] = []
            all_observations: list[dict[str, Any]] = []

            # Fetch relationships and observations for each entity
            skipped: list[str] = []
            for entity in entities:
                try:
                    rels = await graph.get_relationships(entity.name)
                    all_relationships.extend(rels)
                except GraphRAGError as exc:
                    log.warning("Export: skipping relationships for %r: %s", entity.name, exc)
                    skipped.append(f"relationships:{entity.name}")
                try:
                    obs = await graph.get_observations(entity.name)
                    all_observations.extend([o.to_dict() for o in obs])
                except GraphRAGError as exc:
                    log.warning("Export: skipping observations for %r: %s", entity.name, exc)
                    skipped.append(f"observations:{entity.name}")

            # Deduplicate relationships by id
            seen_rel_ids: set[str] = set()
            unique_rels: list[dict[str, Any]] = []
            for rel in all_relationships:
                rid = str(rel["id"])
                if rid not in seen_rel_ids:
                    seen_rel_ids.add(rid)
                    unique_rels.append(rel)

            return {
                "version": __version__,
                "entities": [e.to_dict() for e in entities],
                "relationships": unique_rels,
                "observations": all_observations,
                "skipped": skipped,
            }
        finally:
            await storage.close()

    try:
        data = _run_async(_export())
    except GraphRAGError as exc:
        _print_error(str(exc))
        raise SystemExit(1) from exc

    payload = json.dumps(data, indent=2, ensure_ascii=False, default=str)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=out.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, out)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError as cleanup_exc:
                log.debug("Failed to clean up temp file %s: %s", tmp, cleanup_exc)
            raise
        click.secho(f"Exported to {output_path}", fg="green")
    else:
        click.echo(payload)

    skipped_items: list[str] = data.get("skipped", [])
    if skipped_items:
        click.secho(
            f"Warning: {len(skipped_items)} item(s) skipped during export:", fg="yellow", err=True
        )
        for item in skipped_items:
            click.echo(f"  - {item}", err=True)


# ── import ───────────────────────────────────────────────────────────────────


@cli.command("import")
@click.argument("input_file", type=click.Path(exists=True))
@click.option(
    "--db",
    "db_path",
    default=None,
    type=click.Path(),
    help="Path to the SQLite database file.",
)
@click.option(
    "--project-dir",
    "project_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Project root directory. Database stored at <dir>/.graphrag/graph.db.",
)
def import_data(input_file: str, db_path: str | None, project_dir: str | None) -> None:
    """Import graph data from a JSON file."""

    async def _import() -> dict[str, int]:
        from graphrag_mcp.models import Entity, Observation, Relationship

        raw = Path(input_file).read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GraphRAGError(f"Invalid JSON in {input_file}: {exc}") from exc

        storage, graph = await _open_db(db_path, project_dir)
        try:
            counts: dict[str, int] = {"entities": 0, "relationships": 0, "observations": 0}

            # Import entities
            raw_entities: list[dict[str, Any]] = data.get("entities", [])
            # Build a mapping from old entity IDs to new entity IDs
            old_to_new_id: dict[str, str] = {}
            if raw_entities:
                entities = [
                    Entity(
                        name=str(e["name"]),
                        entity_type=str(e["entity_type"]),
                        description=str(e.get("description", "")),
                        properties=e.get("properties", {}),
                    )
                    for e in raw_entities
                ]
                results = await graph.add_entities(entities)
                counts["entities"] = len(results)

                # Map old exported IDs → newly created IDs
                for raw_ent, result in zip(raw_entities, results):
                    old_id = str(raw_ent.get("id", ""))
                    new_id = str(result["id"])
                    if old_id:
                        old_to_new_id[old_id] = new_id

            # Import relationships
            raw_rels: list[dict[str, Any]] = data.get("relationships", [])
            if raw_rels:
                relationships: list[Relationship] = []
                skipped_rels: list[str] = []
                for r in raw_rels:
                    old_source = str(r["source_id"])
                    old_target = str(r["target_id"])
                    new_source = old_to_new_id.get(old_source)
                    new_target = old_to_new_id.get(old_target)
                    if not new_source or not new_target:
                        skipped_rels.append(
                            f"{old_source}->{old_target}:{r.get('relationship_type', '?')}"
                        )
                        continue
                    relationships.append(
                        Relationship(
                            source_id=new_source,
                            target_id=new_target,
                            relationship_type=str(r["relationship_type"]),
                            weight=float(r.get("weight", 1.0)),
                            properties=r.get("properties", {}),
                        )
                    )
                if relationships:
                    results = await graph.add_relationships(relationships)
                    counts["relationships"] = len(results)
                if skipped_rels:
                    log.warning(
                        "Import: skipped %d relationship(s) with unmapped entity IDs: %s",
                        len(skipped_rels),
                        ", ".join(skipped_rels[:5]),
                    )
                    counts["skipped_relationships"] = len(skipped_rels)

            # Import observations
            raw_obs: list[dict[str, Any]] = data.get("observations", [])
            if raw_obs:
                # Group observations by entity_id — we need the entity name for
                # add_observations, but the export format stores entity_id.
                # Build a mapping from old entity id -> entity name from imported entities.
                entity_id_to_name: dict[str, str] = {}
                for e in raw_entities:
                    eid = str(e.get("id", ""))
                    ename = str(e.get("name", ""))
                    if eid and ename:
                        entity_id_to_name[eid] = ename

                obs_by_entity: dict[str, list[Observation]] = {}
                for o in raw_obs:
                    entity_id = str(o["entity_id"])
                    entity_name = entity_id_to_name.get(entity_id)
                    if not entity_name:
                        continue
                    obs = Observation(
                        entity_id=entity_id,
                        content=str(o["content"]),
                        source=str(o.get("source", "")),
                    )
                    obs_by_entity.setdefault(entity_name, []).append(obs)

                skipped_obs: list[str] = []
                for entity_name, obs_list in obs_by_entity.items():
                    try:
                        results = await graph.add_observations(entity_name, obs_list)
                        counts["observations"] += len(results)
                    except GraphRAGError as exc:
                        log.warning("Import: skipping observations for %r: %s", entity_name, exc)
                        skipped_obs.append(entity_name)

                if skipped_obs:
                    counts["skipped_observations"] = len(skipped_obs)

            return counts
        finally:
            await storage.close()

    try:
        counts = _run_async(_import())
    except GraphRAGError as exc:
        _print_error(str(exc))
        raise SystemExit(1) from exc

    click.secho("Import complete.", fg="green")
    click.echo(f"  Entities:      {counts['entities']}")
    click.echo(f"  Relationships: {counts['relationships']}")
    click.echo(f"  Observations:  {counts['observations']}")
    skipped_rel_count = counts.get("skipped_relationships", 0)
    if skipped_rel_count:
        click.secho(
            f"  Skipped:       {skipped_rel_count} relationship(s) with unmapped entity IDs",
            fg="yellow",
        )
    skipped_count = counts.get("skipped_observations", 0)
    if skipped_count:
        click.secho(f"  Skipped:       {skipped_count} observation group(s) failed", fg="yellow")


# ── validate ─────────────────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--db",
    "db_path",
    default=None,
    type=click.Path(exists=True),
    help="Path to the SQLite database file.",
)
@click.option(
    "--project-dir",
    "project_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Project root directory containing .graphrag/graph.db.",
)
def validate(db_path: str | None, project_dir: str | None) -> None:
    """Validate database integrity."""

    async def _validate() -> list[str]:
        storage, _graph = await _open_db(db_path, project_dir)
        issues: list[str] = []
        try:
            # 1. SQLite integrity check
            result = await storage.fetch_one("PRAGMA integrity_check")
            integrity = str(result["integrity_check"]) if result else "unknown"
            if integrity != "ok":
                issues.append(f"SQLite integrity check failed: {integrity}")

            # 2. Schema version
            version = await storage.get_schema_version()
            if version == 0:
                issues.append("No migrations have been applied (schema version 0).")

            # 3. Orphaned relationships — source or target entity missing
            orphaned_rels = await storage.fetch_all(
                "SELECT r.id, r.source_id, r.target_id FROM relationships r "
                "LEFT JOIN entities s ON s.id = r.source_id "
                "LEFT JOIN entities t ON t.id = r.target_id "
                "WHERE s.id IS NULL OR t.id IS NULL"
            )
            if orphaned_rels:
                issues.append(
                    f"Found {len(orphaned_rels)} orphaned relationship(s) "
                    f"referencing missing entities."
                )

            # 4. Orphaned observations — entity missing
            orphaned_obs = await storage.fetch_all(
                "SELECT o.id FROM observations o "
                "LEFT JOIN entities e ON e.id = o.entity_id "
                "WHERE e.id IS NULL"
            )
            if orphaned_obs:
                issues.append(
                    f"Found {len(orphaned_obs)} orphaned observation(s) "
                    f"referencing missing entities."
                )

            if not issues:
                click.secho("All checks passed.", fg="green")
                click.echo(f"  Schema version: {version}")
            else:
                click.secho(f"Found {len(issues)} issue(s):", fg="yellow")
                for issue in issues:
                    click.echo(f"  - {issue}")

            return issues
        finally:
            await storage.close()

    try:
        issues = _run_async(_validate())
    except GraphRAGError as exc:
        _print_error(str(exc))
        raise SystemExit(1) from exc

    if issues:
        raise SystemExit(1)


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
