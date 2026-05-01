"""CLI entry point for the AI Factory.

Commands:
    factory run --prompt "..." [--pipeline default]
    factory approve <run-id> <stage>
    factory reject <run-id> <stage> [--reason "..."]
    factory changes <run-id> <stage> --feedback "..."
    factory status [<run-id>]
    factory list
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

from factory.pipeline.engine import PipelineEngine

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)
logger = logging.getLogger("factory")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="AI Factory - Automated software development pipeline orchestration",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # -- run --
    run_parser = subparsers.add_parser("run", help="Run a pipeline")
    run_parser.add_argument("--prompt", required=True, help="Natural language project description")
    run_parser.add_argument("--pipeline", default="default", help="Pipeline config name")
    run_parser.add_argument("--workspace", default="workspace", help="Workspace directory")
    run_parser.add_argument("--run-id", default=None, help="Run ID (auto-generated if not set)")

    # -- approve --
    approve_parser = subparsers.add_parser("approve", help="Approve a stage gate")
    approve_parser.add_argument("run_id", help="Run ID")
    approve_parser.add_argument("stage", help="Stage name")
    approve_parser.add_argument("--workspace", default="workspace", help="Workspace directory")

    # -- reject --
    reject_parser = subparsers.add_parser("reject", help="Reject a stage gate")
    reject_parser.add_argument("run_id", help="Run ID")
    reject_parser.add_argument("stage", help="Stage name")
    reject_parser.add_argument("--reason", default="", help="Rejection reason")
    reject_parser.add_argument("--workspace", default="workspace", help="Workspace directory")

    # -- changes --
    changes_parser = subparsers.add_parser("changes", help="Request changes on a stage")
    changes_parser.add_argument("run_id", help="Run ID")
    changes_parser.add_argument("stage", help="Stage name")
    changes_parser.add_argument("--feedback", required=True, help="Change request feedback")
    changes_parser.add_argument("--workspace", default="workspace", help="Workspace directory")

    # -- status --
    status_parser = subparsers.add_parser("status", help="Show pipeline run status")
    status_parser.add_argument("run_id", nargs="?", help="Run ID (shows latest if omitted)")
    status_parser.add_argument("--workspace", default="workspace", help="Workspace directory")

    # -- list --
    list_parser = subparsers.add_parser("list", help="List recent pipeline runs")
    list_parser.add_argument("--workspace", default="workspace", help="Workspace directory")
    list_parser.add_argument("--limit", type=int, default=20, help="Number of runs to show")

    # -- dashboard --
    dash_parser = subparsers.add_parser("dashboard", help="Start the web dashboard")
    dash_parser.add_argument("--workspace", default="workspace", help="Workspace directory")
    dash_parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    dash_parser.add_argument("--port", type=int, default=8900, help="Port to listen on")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "approve":
        cmd_approve(args)
    elif args.command == "reject":
        cmd_reject(args)
    elif args.command == "changes":
        cmd_changes(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)
    else:
        parser.print_help()
        sys.exit(1)


def _get_engine(workspace: str) -> PipelineEngine:
    workspace_path = Path(workspace).resolve()
    configs_dir = Path(__file__).resolve().parent.parent / "configs"
    return PipelineEngine(workspace_path, configs_dir)


def cmd_run(args) -> None:
    run_id = args.run_id or uuid.uuid4().hex[:8]
    engine = _get_engine(args.workspace)

    pipeline_path = engine.configs_dir / "pipelines" / f"{args.pipeline}.yaml"
    if not pipeline_path.exists():
        print(f"Error: Pipeline config not found: {pipeline_path}")
        sys.exit(1)

    print(f"Starting pipeline '{args.pipeline}' (run: {run_id})")
    print(f"Prompt: {args.prompt[:100]}{'...' if len(args.prompt) > 100 else ''}")
    print()

    extra_context = {"user_prompt": args.prompt}
    success = engine.run(pipeline_path, run_id, extra_context)

    if success:
        print(f"\nPipeline completed successfully!")
        print(f"Run ID: {run_id}")
        print(f"Artifacts: {engine.workspace}/artifacts/{run_id}/")
    else:
        print(f"\nPipeline did not complete.")
        print(f"Run ID: {run_id}")
        print(f"Check status: factory status {run_id}")
        sys.exit(1)


def cmd_approve(args) -> None:
    engine = _get_engine(args.workspace)
    success = engine.approve_stage(args.run_id, args.stage)
    if success:
        print(f"Approved stage '{args.stage}' for run '{args.run_id}'")
    else:
        print(f"Failed to approve stage '{args.stage}' for run '{args.run_id}'")
        sys.exit(1)


def cmd_reject(args) -> None:
    engine = _get_engine(args.workspace)
    success = engine.reject_stage(args.run_id, args.stage, args.reason)
    if success:
        print(f"Rejected stage '{args.stage}' for run '{args.run_id}'")
        if args.reason:
            print(f"Reason: {args.reason}")
    else:
        print(f"Failed to reject stage '{args.stage}' for run '{args.run_id}'")
        sys.exit(1)


def cmd_changes(args) -> None:
    engine = _get_engine(args.workspace)
    success = engine.request_changes(args.run_id, args.stage, args.feedback)
    if success:
        print(f"Requested changes on stage '{args.stage}' for run '{args.run_id}'")
        print(f"Feedback: {args.feedback}")
    else:
        print(f"Failed to request changes on stage '{args.stage}'")
        sys.exit(1)


def cmd_status(args) -> None:
    engine = _get_engine(args.workspace)

    if args.run_id:
        _print_run_status(engine, args.run_id)
    else:
        runs = engine.state.list_runs(1)
        if not runs:
            print("No pipeline runs found.")
            return
        _print_run_status(engine, runs[0]["run_id"])


def cmd_list(args) -> None:
    engine = _get_engine(args.workspace)
    runs = engine.state.list_runs(args.limit)
    if not runs:
        print("No pipeline runs found.")
        return

    print(f"{'RUN ID':<10} {'PIPELINE':<20} {'STATUS':<15} {'CURRENT STAGE':<20} {'CREATED'}")
    print("-" * 80)
    for run in runs:
        print(f"{run['run_id']:<10} {run['pipeline_name']:<20} {run['status']:<15} "
              f"{run.get('current_stage', '') or '-':<20} {run['created_at'][:19]}")


def _print_run_status(engine: PipelineEngine, run_id: str) -> None:
    status = engine.get_status(run_id)
    if not status:
        print(f"No run found with ID: {run_id}")
        return

    run = status["run"]
    print(f"Run ID:      {run['run_id']}")
    print(f"Pipeline:    {run['pipeline_name']}")
    print(f"Status:      {run['status']}")
    print(f"Created:     {run['created_at'][:19]}")
    print()

    print(f"{'STAGE':<20} {'STATUS':<20} {'STARTED':<22} {'COMPLETED':<22} {'RETRIES'}")
    print("-" * 95)
    for s in status["stages"]:
        started = (s.get("started_at") or "-")[:19]
        completed = (s.get("completed_at") or "-")[:19]
        retries = s.get("retry_count", 0) or 0
        print(f"{s['stage_name']:<20} {s['status']:<20} {started:<22} {completed:<22} {retries}")

    if status["gates"]:
        print()
        print(f"{'GATE STAGE':<20} {'STATUS':<20} {'REVIEWED'}")
        print("-" * 55)
        for g in status["gates"]:
            reviewed = (g.get("reviewed_at") or "-")[:19]
            print(f"{g['stage_name']:<20} {g['status']:<20} {reviewed}")


def cmd_dashboard(args) -> None:
    """Start the web dashboard."""
    from factory.web.app import create_app
    import uvicorn

    print(f"Starting AI Factory Dashboard...")
    print(f"Open http://{args.host}:{args.port} in your browser")
    print(f"Workspace: {Path(args.workspace).resolve()}")
    print()

    app = create_app(workspace=args.workspace)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
