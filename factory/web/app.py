"""FastAPI web dashboard for AI Factory.

Routes:
    GET  /                    — run list
    GET  /run/<run_id>        — run detail (stages, gates, artifacts)
    GET  /run/<run_id>/<stage> — artifact detail
    POST /approve/<run_id>/<stage>    — approve gate
    POST /reject/<run_id>/<stage>     — reject gate
    POST /changes/<run_id>/<stage>    — request changes
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader

from factory.pipeline.engine import PipelineEngine

# --- Init ---

templates_dir = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)


def _render(template_name: str, context: dict[str, Any]) -> str:
    """Render a Jinja2 template without Starlette's wrapper (avoids version conflicts)."""
    tmpl = _jinja_env.get_template(template_name)
    return tmpl.render(**context)


def create_app(workspace: str = "workspace") -> FastAPI:
    app = FastAPI(title="AI Factory", version="0.1.0")

    configs_dir = Path(__file__).resolve().parent.parent.parent / "configs"
    engine = PipelineEngine(Path(workspace).resolve(), configs_dir)

    # Stage ordering for visualization
    STAGE_ORDER = ["requirements", "design", "development", "testing", "deployment"]
    STAGE_LABELS = {
        "requirements": "需求分析",
        "design": "产品设计",
        "development": "开发",
        "testing": "测试验证",
        "deployment": "部署运维",
    }
    STAGE_ICONS = {
        "pending": "○",
        "in_progress": "◉",
        "awaiting_approval": "⏸",
        "completed": "✓",
        "rejected": "✗",
        "failed": "✗",
    }
    STATUS_COLORS = {
        "pending": "#9ca3af",
        "in_progress": "#3b82f6",
        "awaiting_approval": "#f59e0b",
        "completed": "#22c55e",
        "rejected": "#ef4444",
        "failed": "#ef4444",
    }

    # --- Preview Manager ---
    project_dir = Path(workspace).resolve() / "project"

    class PreviewManager:
        def __init__(self):
            self._processes: dict[str, subprocess.Popen] = {}

        def _read_ports(self) -> tuple[int, int]:
            config_path = project_dir / "deploy-config.json"
            if config_path.exists():
                try:
                    with open(config_path) as f:
                        cfg = json.load(f)
                    services = cfg.get("services", [])
                    fe_port = 3000
                    be_port = 3001
                    for svc in services:
                        ports = svc.get("ports", [])
                        for p in ports:
                            if isinstance(p, str):
                                p = p.split(":")[0] if ":" in p else p
                                try:
                                    p = int(p)
                                except ValueError:
                                    continue
                            if svc.get("name") == "frontend":
                                fe_port = p
                            elif svc.get("name") == "backend":
                                be_port = p
                    return fe_port, be_port
                except Exception:
                    pass
            return 3000, 3001

        def start(self, run_id: str) -> dict:
            if run_id in self._processes:
                proc = self._processes[run_id]
                if proc.poll() is None:
                    fe_port, be_port = self._read_ports()
                    return {"running": True, "frontend_url": f"http://localhost:{fe_port}",
                            "backend_url": f"http://localhost:{be_port}"}
                del self._processes[run_id]

            if not project_dir.exists():
                return {"running": False, "error": "Project directory not found"}

            proc = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=str(project_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._processes[run_id] = proc
            fe_port, be_port = self._read_ports()
            return {"running": True, "frontend_url": f"http://localhost:{fe_port}",
                    "backend_url": f"http://localhost:{be_port}"}

        def stop(self, run_id: str) -> dict:
            proc = self._processes.pop(run_id, None)
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:
                    proc.terminate()
            return {"running": False}

        def is_running(self, run_id: str) -> bool:
            proc = self._processes.get(run_id)
            if proc and proc.poll() is None:
                return True
            if proc:
                del self._processes[run_id]
            return False

    preview_mgr = PreviewManager()

    # --- Routes ---

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, limit: int = Query(default=20)):
        runs = engine.state.list_runs(limit)
        return HTMLResponse(_render("index.html", {
            "request": request,
            "runs": runs,
        }))

    @app.get("/run/{run_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, run_id: str):
        status = engine.get_status(run_id)
        if not status:
            return HTMLResponse(f"<h1>Run not found: {run_id}</h1>", status_code=404)

        # Read all artifacts
        artifacts = {}
        for stage_name in STAGE_ORDER:
            try:
                art = engine.bus.read(stage_name, run_id)
                if art:
                    artifacts[stage_name] = art.model_dump()
            except Exception:
                pass

        # Gate statuses
        gates_map = {g["stage_name"]: g for g in status["gates"]}

        # Enhance stage list with ordering and labels
        enhanced_stages = []
        stages_map = {s["stage_name"]: s for s in status["stages"]}
        for stage_name in STAGE_ORDER:
            s = stages_map.get(stage_name, {"stage_name": stage_name, "status": "pending"})
            s["label"] = STAGE_LABELS.get(stage_name, stage_name)
            s["icon"] = STAGE_ICONS.get(s["status"], "?")
            s["color"] = STATUS_COLORS.get(s["status"], "#9ca3af")
            s["has_gate"] = s["status"] == "awaiting_approval"
            s["gate_info"] = gates_map.get(stage_name)
            s["has_artifact"] = stage_name in artifacts
            enhanced_stages.append(s)

        deployment_done = any(
            s["stage_name"] == "deployment" and s["status"] == "awaiting_approval"
            for s in status["stages"]
        ) or status["run"]["status"] == "completed"

        return HTMLResponse(_render("run.html", {
            "request": request,
            "run": status["run"],
            "stages": enhanced_stages,
            "artifacts": artifacts,
            "stage_labels": STAGE_LABELS,
            "preview_running": preview_mgr.is_running(run_id),
            "deployment_done": deployment_done,
        }))

    @app.get("/run/{run_id}/{stage_name}", response_class=HTMLResponse)
    async def artifact_detail(request: Request, run_id: str, stage_name: str):
        status = engine.get_status(run_id)
        if not status:
            return HTMLResponse(f"<h1>Run not found: {run_id}</h1>", status_code=404)

        artifact = engine.bus.read(stage_name, run_id)
        if not artifact:
            return HTMLResponse(f"<h1>Artifact not found: {stage_name}</h1>", status_code=404)

        return HTMLResponse(_render("artifact.html", {
            "request": request,
            "run": status["run"],
            "stage_name": stage_name,
            "stage_label": STAGE_LABELS.get(stage_name, stage_name),
            "artifact": artifact.model_dump(),
        }))

    @app.post("/approve/{run_id}/{stage_name}")
    async def approve_gate(run_id: str, stage_name: str):
        engine.approve_stage(run_id, stage_name)
        return RedirectResponse(url=f"/run/{run_id}", status_code=303)

    @app.post("/reject/{run_id}/{stage_name}")
    async def reject_gate(run_id: str, stage_name: str, reason: str = Form(default="")):
        engine.reject_stage(run_id, stage_name, reason)
        return RedirectResponse(url=f"/run/{run_id}", status_code=303)

    @app.post("/changes/{run_id}/{stage_name}")
    async def request_changes(run_id: str, stage_name: str, feedback: str = Form(...)):
        engine.request_changes(run_id, stage_name, feedback)
        return RedirectResponse(url=f"/run/{run_id}", status_code=303)

    @app.post("/run")
    async def create_run(prompt: str = Form(...)):
        import uuid
        run_id = uuid.uuid4().hex[:8]
        pipeline_path = engine.configs_dir / "pipelines" / "default.yaml"
        extra_context = {"user_prompt": prompt}
        # Run in background thread so the request returns immediately
        import threading
        t = threading.Thread(
            target=engine.run,
            args=(pipeline_path, run_id),
            kwargs={"extra_context": extra_context},
            daemon=True,
        )
        t.start()
        return RedirectResponse(url=f"/run/{run_id}", status_code=303)

    @app.post("/preview/{run_id}/start")
    async def preview_start(run_id: str):
        result = preview_mgr.start(run_id)
        return JSONResponse(result)

    @app.post("/preview/{run_id}/stop")
    async def preview_stop(run_id: str):
        result = preview_mgr.stop(run_id)
        return JSONResponse(result)

    @app.get("/preview/{run_id}")
    async def preview_status(run_id: str):
        running = preview_mgr.is_running(run_id)
        fe_port, be_port = preview_mgr._read_ports()
        return JSONResponse({
            "running": running,
            "frontend_url": f"http://localhost:{fe_port}",
            "backend_url": f"http://localhost:{be_port}",
        })

    return app


app = create_app()
