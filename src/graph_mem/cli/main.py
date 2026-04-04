"""Click CLI group with management commands for graph-mem.

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
from typing import Any, Literal, cast

import click

from graph_mem import __version__
from graph_mem.cli.install import SUPPORTED_AGENTS as _SUPPORTED_AGENTS
from graph_mem.utils import GraphMemError, get_logger, load_config, setup_logging

log = get_logger("cli")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def _resolve_db_path(
    db_path: str | None = None,
    project_dir: str | None = None,
    graph_name: str | None = None,
) -> None:
    """Set GRAPHMEM_DB_PATH from explicit flags.

    Priority (highest wins):
    1. ``--db`` — explicit database file path
    2. ``--project-dir`` + optional ``--graph`` — resolve ``<dir>/.graphmem/<name>.db``
    3. ``GRAPHMEM_DB_PATH`` environment variable (left untouched)
    4. Config default: ``.graphmem/graph.db`` relative to CWD
    """
    if db_path:
        os.environ["GRAPHMEM_DB_PATH"] = str(Path(db_path).resolve())
    elif project_dir:
        db_name = f"{graph_name}.db" if graph_name else "graph.db"
        resolved = Path(project_dir).resolve() / ".graphmem" / db_name
        os.environ["GRAPHMEM_DB_PATH"] = str(resolved)


async def _open_db(
    db_path: str | None = None,
    project_dir: str | None = None,
    graph_name: str | None = None,
) -> tuple[Any, Any]:
    """Open storage backend and return (StorageBackend, GraphEngine) for CLI commands.

    Lazily imports heavy modules so that lightweight commands (like
    ``--version``) stay fast. The returned "db" object is a StorageBackend
    instance (typically SQLiteBackend) that CLI commands can use for
    raw queries, and the GraphEngine is wired to the same backend.
    """
    from graph_mem.graph import GraphEngine
    from graph_mem.storage import create_backend

    _resolve_db_path(db_path, project_dir, graph_name)

    config = load_config()
    storage = create_backend(config.backend_type, db_path=config.ensure_db_dir())
    await storage.initialize()
    graph = GraphEngine(storage)
    return storage, graph


def _print_error(message: str) -> None:
    click.secho(f"Error: {message}", fg="red", err=True)


# ── CLI Group ────────────────────────────────────────────────────────────────


@click.group()
@click.version_option(version=__version__, prog_name="graph-mem")
def cli() -> None:
    """graph-mem: Production-grade Graph Memory MCP server with local embeddings."""
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
    help="Project root directory. Database stored at <dir>/.graphmem/graph.db.",
)
@click.option(
    "--graph",
    "graph_name",
    default=None,
    help="Named graph to use (resolves to <project-dir>/.graphmem/<name>.db).",
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
@click.option(
    "--embedding-model",
    "embedding_model",
    default=None,
    help="HuggingFace model ID for embeddings (default: all-MiniLM-L6-v2).",
)
@click.option(
    "--use-onnx/--no-onnx",
    "use_onnx",
    default=None,
    help="Force ONNX runtime on/off for embeddings (default: auto-detect).",
)
@click.option(
    "--embedding-device",
    "embedding_device",
    default=None,
    type=click.Choice(["cpu", "cuda"]),
    help="Device for embedding inference (default: cpu).",
)
@click.option(
    "--cache-size",
    "cache_size",
    default=None,
    type=int,
    help="Max entries in the embedding LRU cache (default: 10000).",
)
@click.option(
    "--search-limit",
    "search_limit",
    default=None,
    type=int,
    help="Default max results for search_nodes (default: 10).",
)
@click.option(
    "--max-hops",
    "max_hops",
    default=None,
    type=int,
    help="Default max traversal depth for find_connections (default: 4).",
)
@click.option(
    "--log-level",
    "log_level",
    default=None,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False),
    help="Logging verbosity (default: WARNING).",
)
def server(
    transport: str,
    db_path: str | None,
    project_dir: str | None,
    graph_name: str | None,
    host: str,
    port: int,
    embedding_model: str | None,
    use_onnx: bool | None,
    embedding_device: str | None,
    cache_size: int | None,
    search_limit: int | None,
    max_hops: int | None,
    log_level: str | None,
) -> None:
    """Start the MCP server.

    All options can also be set via environment variables (GRAPHMEM_*).
    CLI flags take precedence over environment variables.

    \b
    Examples:
      graph-mem server
      graph-mem server --project-dir /my/project
      graph-mem server --project-dir /my/project --graph harry-potter
      graph-mem server --embedding-model sentence-transformers/all-mpnet-base-v2
      graph-mem server --no-onnx --embedding-device cuda --log-level DEBUG
    """
    try:
        # Suppress noisy third-party output before importing heavy deps.
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        import logging as _logging
        import warnings

        warnings.filterwarnings("ignore", message=".*unauthenticated requests.*")
        warnings.filterwarnings("ignore", message=".*HF_TOKEN.*")
        for _name in ("huggingface_hub", "httpx", "httpcore", "filelock", "urllib3"):
            _logging.getLogger(_name).setLevel(_logging.ERROR)

        # Propagate CLI options into environment so that Config picks them up.
        # Only set env vars for options that were explicitly provided — this
        # preserves the precedence: CLI flag > env var > Config default.
        os.environ["GRAPHMEM_TRANSPORT"] = transport
        _resolve_db_path(db_path, project_dir, graph_name)

        if embedding_model is not None:
            os.environ["GRAPHMEM_EMBEDDING_MODEL"] = embedding_model
        if use_onnx is not None:
            os.environ["GRAPHMEM_USE_ONNX"] = "1" if use_onnx else "0"
        if embedding_device is not None:
            os.environ["GRAPHMEM_EMBEDDING_DEVICE"] = embedding_device
        if cache_size is not None:
            os.environ["GRAPHMEM_CACHE_SIZE"] = str(cache_size)
        if search_limit is not None:
            os.environ["GRAPHMEM_SEARCH_LIMIT"] = str(search_limit)
        if max_hops is not None:
            os.environ["GRAPHMEM_MAX_HOPS"] = str(max_hops)
        if log_level is not None:
            os.environ["GRAPHMEM_LOG_LEVEL"] = log_level.upper()

        from graph_mem.server import run  # lazy import — heavy deps

        run(
            transport=cast("Literal['stdio', 'sse', 'streamable-http']", transport),
            host=host,
            port=port,
        )
    except GraphMemError as exc:
        log.debug("Server command failed: %s", exc, exc_info=True)
        _print_error(str(exc))
        raise SystemExit(1) from exc


# ── install ──────────────────────────────────────────────────────────────────


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
@click.option(
    "--domain",
    type=click.Choice(["general", "code", "research"]),
    default="general",
    help="Domain overlay to include (general, code, or research).",
)
def install(agent: str, scope: str, project_dir: str | None, domain: str) -> None:
    """Install agent skill configuration for AGENT.

    Writes the appropriate MCP configuration file so that the
    selected coding agent can discover graph-mem.  Use --domain
    to include a domain-specific overlay (e.g. code for software
    engineering, research for academic papers).
    """
    try:
        from graph_mem.cli.install import install_skill  # lazy import

        pd = Path(project_dir) if project_dir else None
        result_path: Path = install_skill(agent=agent, scope=scope, project_dir=pd, domain=domain)
        click.secho(f"Installed {agent} skill ({scope}, domain={domain}):", fg="green")
        click.echo(f"  {result_path}")
    except GraphMemError as exc:
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
    help="Custom database path (default: .graphmem/graph.db).",
)
@click.option(
    "--project-dir",
    "project_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Project root directory. Database stored at <dir>/.graphmem/graph.db.",
)
@click.option(
    "--graph",
    "graph_name",
    default=None,
    help="Named graph to use (resolves to <project-dir>/.graphmem/<name>.db).",
)
def init(db_path: str | None, project_dir: str | None, graph_name: str | None) -> None:
    """Initialize a .graphmem directory and database."""

    async def _init() -> None:
        from graph_mem.storage import create_backend

        _resolve_db_path(db_path, project_dir, graph_name)

        config = load_config()
        resolved = config.ensure_db_dir()

        storage = create_backend(config.backend_type, db_path=resolved)
        await storage.initialize()
        version = await storage.get_schema_version()
        await storage.close()

        click.secho("Initialized graph-mem database.", fg="green")
        click.echo(f"  Database: {resolved}")
        click.echo(f"  Backend: {config.backend_type}")
        click.echo(f"  Schema version: {version}")

    try:
        _run_async(_init())
    except GraphMemError as exc:
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
    help="Project root directory containing .graphmem/graph.db.",
)
@click.option(
    "--graph",
    "graph_name",
    default=None,
    help="Named graph to use (resolves to <project-dir>/.graphmem/<name>.db).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Output raw JSON instead of formatted text.",
)
def status(
    db_path: str | None,
    project_dir: str | None,
    graph_name: str | None,
    as_json: bool,
) -> None:
    """Show graph statistics."""

    async def _status() -> dict[str, Any]:
        storage, graph = await _open_db(db_path, project_dir, graph_name)
        try:
            result: dict[str, Any] = await graph.get_stats()
            result["schema_version"] = await storage.get_schema_version()
            return result
        finally:
            await storage.close()

    try:
        stats = _run_async(_status())
    except GraphMemError as exc:
        _print_error(str(exc))
        raise SystemExit(1) from exc

    if as_json:
        click.echo(json.dumps(stats, indent=2, default=str))
        return

    # Pretty-print
    separator = "\u2500" * 36
    click.echo(f"graph-mem status\n{separator}")
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
    help="Project root directory containing .graphmem/graph.db.",
)
@click.option(
    "--graph",
    "graph_name",
    default=None,
    help="Named graph to use (resolves to <project-dir>/.graphmem/<name>.db).",
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
    graph_name: str | None,
    fmt: str,
    output_path: str | None,
) -> None:
    """Export all graph data."""

    async def _export() -> dict[str, Any]:
        storage, graph = await _open_db(db_path, project_dir, graph_name)
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
                except GraphMemError as exc:
                    log.warning("Export: skipping relationships for %r: %s", entity.name, exc)
                    skipped.append(f"relationships:{entity.name}")
                try:
                    obs = await graph.get_observations(entity.name)
                    all_observations.extend([o.to_dict() for o in obs])
                except GraphMemError as exc:
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
    except GraphMemError as exc:
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
    help="Project root directory. Database stored at <dir>/.graphmem/graph.db.",
)
@click.option(
    "--graph",
    "graph_name",
    default=None,
    help="Named graph to use (resolves to <project-dir>/.graphmem/<name>.db).",
)
def import_data(
    input_file: str, db_path: str | None, project_dir: str | None, graph_name: str | None
) -> None:
    """Import graph data from a JSON file."""

    async def _import() -> dict[str, int]:
        from graph_mem.models import Entity, Observation, Relationship

        raw = Path(input_file).read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GraphMemError(f"Invalid JSON in {input_file}: {exc}") from exc

        storage, graph = await _open_db(db_path, project_dir, graph_name)
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
                for raw_ent, result in zip(raw_entities, results, strict=True):
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
                    except GraphMemError as exc:
                        log.warning("Import: skipping observations for %r: %s", entity_name, exc)
                        skipped_obs.append(entity_name)

                if skipped_obs:
                    counts["skipped_observations"] = len(skipped_obs)

            return counts
        finally:
            await storage.close()

    try:
        counts = _run_async(_import())
    except GraphMemError as exc:
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
    help="Project root directory containing .graphmem/graph.db.",
)
@click.option(
    "--graph",
    "graph_name",
    default=None,
    help="Named graph to use (resolves to <project-dir>/.graphmem/<name>.db).",
)
def validate(db_path: str | None, project_dir: str | None, graph_name: str | None) -> None:
    """Validate database integrity."""

    async def _validate() -> list[str]:
        storage, _graph = await _open_db(db_path, project_dir, graph_name)
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
    except GraphMemError as exc:
        _print_error(str(exc))
        raise SystemExit(1) from exc

    if issues:
        raise SystemExit(1)


# ── ui ───────────────────────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--port",
    type=int,
    default=0,
    help="Port to serve the UI on (default: auto-select available port).",
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host to bind to (default: 127.0.0.1).",
)
@click.option(
    "--no-open",
    is_flag=True,
    default=False,
    help="Don't automatically open the browser.",
)
@click.option(
    "--db",
    "db_path",
    default=None,
    type=click.Path(),
    help="Path to graph.db (default: .graphmem/graph.db).",
)
@click.option(
    "--project-dir",
    default=None,
    type=click.Path(exists=True, file_okay=False),
    help="Project directory containing .graphmem/.",
)
@click.option(
    "--graph",
    "graph_name",
    default=None,
    help="Named graph to use (resolves to <project-dir>/.graphmem/<name>.db).",
)
def ui(
    port: int,
    host: str,
    no_open: bool,
    db_path: str | None,
    project_dir: str | None,
    graph_name: str | None,
) -> None:
    """Launch the graph visualisation UI in your browser."""
    _resolve_db_path(db_path, project_dir, graph_name)

    try:
        from graph_mem.ui.server import start_server
    except ImportError as exc:
        _print_error(
            f"UI dependencies not installed: {exc}\n  Install with: pip install graph-mem[ui]"
        )
        raise SystemExit(1) from exc

    try:
        _run_async(start_server(host=host, port=port, no_open=no_open))
    except KeyboardInterrupt:
        click.echo("\nStopped.")
    except GraphMemError as exc:
        _print_error(str(exc))
        raise SystemExit(1) from exc


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
