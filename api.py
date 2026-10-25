"""HTTP API for the SAR BAQ workspace UI. Wraps sar_baq.py; does not replace the CLI."""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

ROOT = os.path.abspath(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sar_baq  # noqa: E402

INPUT_DIR = os.path.join(ROOT, "data", "input")
OUTPUT_DIR = os.path.join(ROOT, "data", "output")
os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

TIFF_EXTS = {".tif", ".tiff", ".geotiff"}
BAQ_EXTS = {".baq"}

app = FastAPI(title="SAR BAQ API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _jsonable(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item") and callable(value.item):
        try:
            return _jsonable(value.item())
        except Exception:
            return str(value)
    return value


def _new_job(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    job = {
        "id": job_id,
        "kind": kind,
        "status": "queued",
        "pct": 0.0,
        "stage": "queued",
        "message": "Waiting to start",
        "chunk": None,
        "n_chunks": None,
        "logs": [],
        "error": None,
        "result": None,
        "created_at": time.time(),
        "dir": job_dir,
        "payload": payload,
        "seq": 0,
    }
    with _lock:
        _jobs[job_id] = job
    return job


def _update(job: dict[str, Any], **fields: Any) -> None:
    with _lock:
        job.update(fields)
        job["seq"] = int(job.get("seq", 0)) + 1


def _log(job: dict[str, Any], line: str) -> None:
    text = line.rstrip()
    if not text:
        return
    with _lock:
        logs = job.setdefault("logs", [])
        logs.append(text)
        if len(logs) > 400:
            del logs[: len(logs) - 400]
        job["seq"] = int(job.get("seq", 0)) + 1


def _progress(job: dict[str, Any], event: dict[str, Any]) -> None:
    pct = float(event.get("pct") or 0)
    stage = event.get("stage") or job.get("stage")
    # Map compress/decompress into an overall pipeline percentage.
    kind = job.get("kind")
    if kind == "pipeline":
        if stage == "compress":
            pct = 5 + pct * 0.45
        elif stage == "decompress":
            pct = 50 + pct * 0.30
        elif stage == "metrics":
            pct = 85
        elif stage == "preview":
            pct = 92
        elif stage == "experiments":
            pct = 96
    _update(
        job,
        pct=min(99.0, max(0.0, pct)),
        stage=stage,
        message=event.get("message") or job.get("message"),
        chunk=event.get("chunk"),
        n_chunks=event.get("n_chunks"),
    )
    _log(job, event.get("message") or "")


def _job_public(job: dict[str, Any]) -> dict[str, Any]:
    return _jsonable({
        "id": job["id"],
        "kind": job["kind"],
        "status": job["status"],
        "pct": job["pct"],
        "stage": job["stage"],
        "message": job["message"],
        "chunk": job.get("chunk"),
        "n_chunks": job.get("n_chunks"),
        "logs": job.get("logs", [])[-40:],
        "error": job.get("error"),
        "result": job.get("result"),
    })


def _resolve_existing(path: str, exts: Optional[set[str]] = None) -> str:
    if not path or not path.strip():
        raise HTTPException(400, "A file path is required.")
    raw = path.strip().strip('"')
    if not os.path.isabs(raw):
        raw = os.path.join(INPUT_DIR, raw)
    abs_path = os.path.abspath(raw)
    if not os.path.exists(abs_path):
        raise HTTPException(404, f"File not found: {abs_path}")
    if not os.path.isfile(abs_path):
        raise HTTPException(400, "Path is not a file.")
    if exts:
        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in exts:
            raise HTTPException(400, f"Expected one of {sorted(exts)}, got {ext or 'no extension'}.")
    return abs_path


class InspectBody(BaseModel):
    path: str
    band: int = 1


class RunBody(BaseModel):
    path: str
    block_size: int = 32
    bits: int = 4
    band: int = 1
    chunk_target_mb: int = Field(default=64, ge=8, le=512)
    run_experiments: bool = False
    experiment_block_sizes: list[int] = Field(default_factory=lambda: [16, 32, 64])
    experiment_bits: list[int] = Field(default_factory=lambda: [2, 3, 4, 5, 6])


class DecompressBody(BaseModel):
    path: str
    reference_tiff: Optional[str] = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "rasterio": bool(sar_baq.HAVE_RASTERIO),
        "matplotlib": bool(sar_baq.HAVE_MPL),
        "input_dir": INPUT_DIR,
        "output_dir": OUTPUT_DIR,
        "valid_bits": list(sar_baq.VALID_BITS),
        "valid_block_sizes": list(sar_baq.VALID_BLOCK_SIZES),
    }


@app.get("/api/files")
def list_files() -> dict[str, Any]:
    items = []
    for name in sorted(os.listdir(INPUT_DIR)):
        path = os.path.join(INPUT_DIR, name)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in TIFF_EXTS | BAQ_EXTS:
            continue
        items.append({
            "name": name,
            "path": path,
            "size": os.path.getsize(path),
            "kind": "baq" if ext in BAQ_EXTS else "tiff",
        })
    return {"input_dir": INPUT_DIR, "files": items}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(400, "Missing filename.")
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in TIFF_EXTS | BAQ_EXTS:
        raise HTTPException(400, "Upload a GeoTIFF (.tif/.tiff) or a .baq file.")
    dest = os.path.join(INPUT_DIR, os.path.basename(file.filename))
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return {
        "name": os.path.basename(dest),
        "path": dest,
        "size": os.path.getsize(dest),
        "kind": "baq" if ext in BAQ_EXTS else "tiff",
    }


@app.post("/api/inspect")
def inspect(body: InspectBody) -> dict[str, Any]:
    if not sar_baq.HAVE_RASTERIO:
        raise HTTPException(503, "rasterio is not installed on this machine. Install it, then restart the API.")
    path = _resolve_existing(body.path, TIFF_EXTS)
    info = sar_baq.inspect_tiff(path, band=body.band)
    if info is None:
        raise HTTPException(400, "Inspection failed. Check the path and band number.")
    return _jsonable(info)


def _start_thread(job: dict[str, Any], target) -> dict[str, Any]:
    thread = threading.Thread(target=target, args=(job,), daemon=True)
    thread.start()
    return _job_public(job)


def _run_pipeline(job: dict[str, Any]) -> None:
    p = job["payload"]
    job_dir = job["dir"]
    baq_path = os.path.join(job_dir, "compressed.baq")
    recon_path = os.path.join(job_dir, "reconstructed.tiff")
    preview_path = os.path.join(job_dir, "preview_comparison.png")
    exp_dir = os.path.join(job_dir, "experiments")
    cb = lambda ev: _progress(job, ev)
    try:
        _update(job, status="running", stage="inspect", message="Inspecting TIFF", pct=2)
        _log(job, f"Inspecting {p['path']}")
        info = sar_baq.inspect_tiff(p["path"], band=p["band"])
        if info is None:
            raise RuntimeError("Inspection failed.")

        _update(job, stage="compress", message="Compressing with BAQ", pct=5)
        header = sar_baq.compress_tiff(
            p["path"], baq_path,
            block_size=p["block_size"], bits=p["bits"],
            band=p["band"], chunk_target_mb=p["chunk_target_mb"],
            progress_cb=cb,
        )

        _update(job, stage="decompress", message="Reconstructing TIFF", pct=50)
        sar_baq.decompress_baq(baq_path, recon_path, reference_tiff_for_geo=p["path"], progress_cb=cb)

        _update(job, stage="metrics", message="Computing full-image metrics", pct=85)
        _log(job, "Computing metrics on the full raster")
        metrics = sar_baq.compute_metrics(p["path"], recon_path, band=p["band"], compressed_file=baq_path)

        preview_ok = False
        _update(job, stage="preview", message="Building comparison preview", pct=92)
        if sar_baq.HAVE_MPL:
            sar_baq.visualize_comparison(p["path"], recon_path, band=p["band"], out_path=preview_path)
            preview_ok = os.path.exists(preview_path)
        else:
            _log(job, "matplotlib is not available; skipping preview PNG")

        experiments = None
        graphs = []
        if p.get("run_experiments"):
            _update(job, stage="experiments", message="Running parameter sweep", pct=96)
            _log(job, "Experiment sweep started — this can take a while")
            experiments = sar_baq.run_experiments(
                p["path"], p["experiment_block_sizes"], p["experiment_bits"],
                work_dir=exp_dir, band=p["band"], chunk_target_mb=p["chunk_target_mb"],
            )
            if os.path.isdir(exp_dir):
                graphs = [name for name in sorted(os.listdir(exp_dir)) if name.lower().endswith(".png")]

        result = {
            "inspect": info,
            "header": header,
            "metrics": metrics,
            "paths": {
                "baq": baq_path,
                "reconstructed": recon_path,
                "preview": preview_path if preview_ok else None,
                "experiments": exp_dir if experiments is not None else None,
            },
            "preview_available": preview_ok,
            "experiments": experiments,
            "graphs": graphs,
            "block_size": p["block_size"],
            "bits": p["bits"],
            "band": p["band"],
        }
        _update(job, status="done", stage="done", message="Pipeline complete", pct=100, result=_jsonable(result))
        _log(job, "Pipeline complete")
    except Exception as exc:
        _log(job, traceback.format_exc())
        _update(job, status="error", stage="error", message=str(exc), error=str(exc), pct=job.get("pct") or 0)


def _run_compress(job: dict[str, Any]) -> None:
    p = job["payload"]
    baq_path = os.path.join(job["dir"], "compressed.baq")
    try:
        _update(job, status="running", stage="compress", message="Compressing", pct=1)
        header = sar_baq.compress_tiff(
            p["path"], baq_path,
            block_size=p["block_size"], bits=p["bits"],
            band=p["band"], chunk_target_mb=p["chunk_target_mb"],
            progress_cb=lambda ev: _progress(job, ev),
        )
        result = {"header": header, "paths": {"baq": baq_path}, "block_size": p["block_size"], "bits": p["bits"], "band": p["band"]}
        _update(job, status="done", stage="done", message="Compression complete", pct=100, result=_jsonable(result))
    except Exception as exc:
        _log(job, traceback.format_exc())
        _update(job, status="error", stage="error", message=str(exc), error=str(exc))


def _run_decompress(job: dict[str, Any]) -> None:
    p = job["payload"]
    recon_path = os.path.join(job["dir"], "reconstructed.tiff")
    try:
        _update(job, status="running", stage="decompress", message="Decompressing", pct=1)
        header = sar_baq.decompress_baq(
            p["path"], recon_path,
            reference_tiff_for_geo=p.get("reference_tiff"),
            progress_cb=lambda ev: _progress(job, ev),
        )
        result = {"header": header, "paths": {"reconstructed": recon_path}}
        _update(job, status="done", stage="done", message="Decompression complete", pct=100, result=_jsonable(result))
    except Exception as exc:
        _log(job, traceback.format_exc())
        _update(job, status="error", stage="error", message=str(exc), error=str(exc))


@app.post("/api/jobs/pipeline")
def start_pipeline(body: RunBody) -> dict[str, Any]:
    if not sar_baq.HAVE_RASTERIO:
        raise HTTPException(503, "rasterio is not installed on this machine.")
    if body.bits not in sar_baq.VALID_BITS:
        raise HTTPException(400, f"bits must be one of {list(sar_baq.VALID_BITS)}")
    if body.block_size not in sar_baq.VALID_BLOCK_SIZES:
        raise HTTPException(400, f"block_size must be one of {list(sar_baq.VALID_BLOCK_SIZES)}")
    path = _resolve_existing(body.path, TIFF_EXTS)
    job = _new_job("pipeline", {
        "path": path,
        "block_size": body.block_size,
        "bits": body.bits,
        "band": body.band,
        "chunk_target_mb": body.chunk_target_mb,
        "run_experiments": body.run_experiments,
        "experiment_block_sizes": body.experiment_block_sizes,
        "experiment_bits": body.experiment_bits,
    })
    return _start_thread(job, _run_pipeline)


@app.post("/api/jobs/compress")
def start_compress(body: RunBody) -> dict[str, Any]:
    if not sar_baq.HAVE_RASTERIO:
        raise HTTPException(503, "rasterio is not installed on this machine.")
    path = _resolve_existing(body.path, TIFF_EXTS)
    job = _new_job("compress", {
        "path": path,
        "block_size": body.block_size,
        "bits": body.bits,
        "band": body.band,
        "chunk_target_mb": body.chunk_target_mb,
    })
    return _start_thread(job, _run_compress)


@app.post("/api/jobs/decompress")
def start_decompress(body: DecompressBody) -> dict[str, Any]:
    if not sar_baq.HAVE_RASTERIO:
        raise HTTPException(503, "rasterio is not installed on this machine.")
    path = _resolve_existing(body.path, BAQ_EXTS)
    ref = None
    if body.reference_tiff:
        ref = _resolve_existing(body.reference_tiff, TIFF_EXTS)
    job = _new_job("decompress", {"path": path, "reference_tiff": ref})
    return _start_thread(job, _run_decompress)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")
    return _job_public(job)


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Unknown job.")

    async def gen():
        import asyncio
        last = -1
        while True:
            job = _jobs.get(job_id)
            if not job:
                break
            seq = job.get("seq", 0)
            if seq != last:
                yield f"data: {json.dumps(_job_public(job))}\n\n"
                last = seq
            if job["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.3)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/jobs/{job_id}/preview")
def job_preview(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")
    path = os.path.join(job["dir"], "preview_comparison.png")
    if not os.path.exists(path):
        raise HTTPException(404, "No preview yet. Run the full pipeline.")
    return FileResponse(path, media_type="image/png")


@app.get("/api/jobs/{job_id}/graphs/{name}")
def job_graph(job_id: str, name: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")
    safe = os.path.basename(name)
    path = os.path.join(job["dir"], "experiments", safe)
    if not os.path.exists(path):
        raise HTTPException(404, "Graph not found.")
    return FileResponse(path, media_type="image/png")
