"""GPU 控制面板 — 可视化控制 GPU1 模型组的启停 + idle-watcher。

一个单文件 FastAPI 服务，端口 8099，只绑 127.0.0.1（通过外层代理/隧道暴露）。
提供：
  GET  /            控制面板页面（内联 HTML，无前端构建）
  GET  /api/state   当前状态 JSON（两卡 + 模型组 + watcher + 活动时间）
  POST /api/model/{start|stop}    启停模型组（调 scripts/model-servers.sh）
  POST /api/watcher/{start|stop|enable|disable}  控制 idle-watcher

设计原则：
  - 面板只是 model-servers.sh / idle-watcher.sh 的薄壳，所有副作用都走那两个脚本，
    这样面板重启/崩溃不会丢状态（状态在 pidfile + last_activity 文件里）。
  - 不做鉴权（只绑 127.0.0.1；外层代理负责鉴权）。挂到公网前必须加代理鉴权。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import httpx
import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

PROJ = Path("/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/ai-clinical-copilot")
SCRIPTS = PROJ / "scripts"
MODEL_SCRIPT = str(SCRIPTS / "model-servers.sh")
WATCHER_SCRIPT = str(SCRIPTS / "idle-watcher.sh")
COMFY_LOCAL_SCRIPT = str(SCRIPTS / "comfy-local.sh")
GATEWAY_HEALTH = "http://127.0.0.1:8100/health"
COMFY_LOCAL_PORT = 8190  # LOCAL GPU1 second ComfyUI. :8189 is reserved for the
                        # remote reverse-SSH tunnel (read-only detection only).
LOGDIR = PROJ / "data" / "logs"
ACT_FILE = LOGDIR / "last_activity"
DISABLED_FILE = LOGDIR / "idle-watcher.disabled"
WATCHER_PIDFILE = LOGDIR / "idle-watcher.pid"
MODEL_PIDFILES = {
    "clinical_main": "clinical-main-server.pid",
    "tcm": "tcm-server.pid",
    "asr": "asr-server.pid",
    "embedding": "embedding-server.pid",
    "reranker": "reranker-server.pid",
    "ocr": "ocr-server.pid",
}
# pgrep needle per key — port-specific so clinical_main / tcm don't collide.
PGREP_NEEDLES = {
    "clinical_main": "llm_server.py --port 7870",
    "tcm": "llm_server.py --port 7871",
    "asr": "asr_server.py --port 7872",
    "embedding": "embedding_server.py --port 7873",
    "reranker": "reranker_server.py --port 7874",
    "ocr": "ocr_server.py --port 7875",
}
# (key, port, display_name, role note)
SERVICES = [
    ("clinical_main", 7870, "临床主模型", "Qwen3.8-27B · VLM+LLM"),
    ("tcm", 7871, "中医模型", "ShizhenGPT-7B-Omni"),
    ("asr", 7872, "语音识别", "Qwen3-ASR-1.7B gguf"),
    ("embedding", 7873, "向量化", "Qwen3-Embedding-8B"),
    ("reranker", 7874, "重排序", "Qwen3-Reranker-8B"),
    ("ocr", 7875, "OCR", "PaddleOCR-VL-1.6 gguf"),
]

app = FastAPI(title="GPU 控制面板", docs_url=None, redoc_url=None)


# --------------------------------------------------------------------------- helpers
def _read_pid(p: Path) -> int | None:
    try:
        v = p.read_text().strip()
        return int(v) if v else None
    except Exception:
        return None


def _pid_alive(pid: int | None) -> bool:
    return bool(pid and pid > 0 and Path(f"/proc/{pid}").exists())


def _last_activity() -> int:
    try:
        return int(ACT_FILE.read_text().strip()) if ACT_FILE.exists() else 0
    except Exception:
        return 0


def _gpu_status() -> list[dict]:
    """nvidia-smi per-GPU snapshot. Always returns 2 slots (None if smi fails)."""
    out = []
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        for line in r.stdout.strip().splitlines():
            idx, name, tot, used, free, util, temp = [x.strip() for x in line.split(",")]
            out.append({
                "index": int(idx), "name": name,
                "mem_total_mb": int(tot), "mem_used_mb": int(used), "mem_free_mb": int(free),
                "util_pct": int(util), "temp_c": int(temp),
            })
    except Exception:
        pass
    return out


def _model_services_status() -> list[dict]:
    """Per-service up/down + pid + port health probe (cheap, parallelized by caller)."""
    rows = []
    for key, port, name, note in SERVICES:
        pidfile = LOGDIR / MODEL_PIDFILES[key]
        pid = _read_pid(pidfile)
        alive = _pid_alive(pid)
        # fall back to pgrep (port-specific needle) if pidfile stale/missing
        if not alive:
            try:
                r = subprocess.run(
                    ["pgrep", "-f", PGREP_NEEDLES[key]],
                    capture_output=True, text=True, timeout=3,
                )
                if r.stdout.strip():
                    pid = int(r.stdout.splitlines()[0])
                    alive = True
            except Exception:
                pass
        rows.append({
            "key": key, "name": name, "note": note, "port": port,
            "pid": pid if alive else None, "state": "UP" if alive else "DOWN",
        })
    return rows


def _watcher_status() -> dict:
    pid = _read_pid(WATCHER_PIDFILE)
    alive = _pid_alive(pid)
    return {
        "pid": pid if alive else None,
        "state": "UP" if alive else "DOWN",
        "auto_stop": "DISABLED" if DISABLED_FILE.exists() else "ENABLED",
    }


def _idle_minutes() -> tuple[int, int]:
    """Return (idle_minutes, threshold_minutes)."""
    last = _last_activity()
    now = int(time.time())
    idle = (now - last) // 60 if last else -1
    thr = int(os.environ.get("IDLE_MINUTES", "120"))
    return idle, thr


async def _probe_ports(services: list[dict]) -> None:
    """Best-effort /health probe on each up service port (enriches state)."""
    async def probe(svc):
        if svc["state"] != "UP":
            return
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"http://127.0.0.1:{svc['port']}/health")
                svc["health"] = "ok" if r.status_code < 400 else f"HTTP {r.status_code}"
        except Exception:
            svc["health"] = "unreachable"
    await asyncio.gather(*[probe(s) for s in services], return_exceptions=True)


def _port_listener_kind(port: int) -> tuple[str, int | None]:
    """Use lsof (authoritative) to find what's listening on a TCP port.
    Returns (kind, pid) where kind ∈ local-python | ssh-tunnel | unknown.

    Why lsof not pgrep: pgrep -f matches the needle against the FULL cmdline,
    so an ssh session whose remote-command arg happens to contain
    'main.py --listen 0.0.0.0 --port 8189' gets false-matched as a local comfy.
    lsof instead reports the COMMAND that actually owns the listening socket:
    'python' => our local GPU1 ComfyUI, 'sshd'/'sshd-sess' => the reverse-SSH
    tunnel from the remote box. This is READ-ONLY detection; we never operate
    on the remote.
    """
    try:
        r = subprocess.run(["lsof", "-iTCP:%d" % port, "-sTCP:LISTEN", "-n", "-P"],
                           capture_output=True, text=True, timeout=3)
    except Exception:
        return "unknown", None
    out = r.stdout.strip()
    if not out:
        return "unknown", None
    # last non-empty line is the listener
    line = [l for l in out.splitlines() if l.strip()][-1]
    cmd = line.split()[0].lower() if line.split() else ""
    # parse PID (2nd column)
    pid = None
    try:
        pid = int(line.split()[1])
    except (IndexError, ValueError):
        pass
    if "ssh" in cmd:
        return "ssh-tunnel", pid
    if "python" in cmd:
        return "local-python", pid
    return "unknown", pid


def _comfy_port_status(port: int, pidfile: Path | None = None) -> dict:
    """Status of one ComfyUI listener port: up/down + pid + kind.
    kind is determined from the actual listening socket (lsof), NOT from pgrep,
    because a reverse-SSH tunnel and our local python can both end up bound to a
    port and pgrep false-matches the ssh session's remote-command arg.

    kind=local-python  -> a local ComfyUI process (may be ours on GPU1, or the
                          primary :8188 on GPU0)
    kind=ssh-tunnel    -> a remote box's ComfyUI reverse-SSH-forwarded here
                          (read-only; does NOT consume local GPU1)
    kind=unknown       -> nothing listening, or unrecognized listener

    If pidfile is given and its process is alive but lsof sees no listener yet,
    state is STARTING (weights still loading).
    """
    own_pid = _read_pid(pidfile) if pidfile else None
    own_alive = _pid_alive(own_pid)
    kind, lsof_pid = _port_listener_kind(port)
    if kind in ("local-python", "ssh-tunnel"):
        state = "UP"
        if kind == "local-python" and own_alive:
            pid = own_pid
        else:
            pid = lsof_pid
    else:
        state = "DOWN"
        pid = None
        if own_alive:
            state = "STARTING"
            pid = own_pid
            kind = "local-python"
    return {"port": port, "pid": pid, "state": state, "kind": kind}


# Port range we scan for ComfyUI listeners. 8188 is the primary (GPU0); 8189 is
# reserved for the remote reverse-SSH tunnel; 8190+ are local extra-GPU instances
# (8190 = our GPU1 second comfy). Scanning the whole range lets the panel detect
# NEW cards / new tunnels the user wires up without code changes.
COMFY_SCAN_PORTS = list(range(8188, 8196))


def _scan_comfy_listeners() -> list[dict]:
    """One lsof sweep over all TCP listeners, return comfy-port entries that are
    actually listening. Each: {port, pid, kind}. Cheaper than N per-port calls."""
    try:
        r = subprocess.run(["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P"],
                           capture_output=True, text=True, timeout=4)
    except Exception:
        return []
    out = []
    seen = set()
    for line in r.stdout.splitlines()[1:]:  # skip header
        cols = line.split()
        if len(cols) < 9:
            continue
        cmd = cols[0].lower()
        try:
            pid = int(cols[1])
        except (IndexError, ValueError):
            pid = None
        # name col like "*:8188 (LISTEN)" or "127.0.0.1:8189 (LISTEN)"
        name = cols[8]
        port_s = name.rsplit(":", 1)[-1].split()[0] if ":" in name else ""
        try:
            port = int(port_s)
        except ValueError:
            continue
        if port not in COMFY_SCAN_PORTS or port in seen:
            continue
        seen.add(port)
        if "ssh" in cmd:
            kind = "ssh-tunnel"
        elif "python" in cmd:
            kind = "local-python"
        else:
            kind = "unknown"
        out.append({"port": port, "pid": pid, "kind": kind})
    return out


async def _comfy_system_info(port: int) -> dict:
    """Probe a ComfyUI instance's /system_stats for version/gpu/ram info.
    Works for any port — local :8188, local :8190, or a remote-forwarded :8189.
    READ-ONLY."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"http://127.0.0.1:{port}/system_stats")
            d = r.json()
        sysd = d.get("system", {})
        # ComfyUI's devices[] usually empty until a model loads; pytorch_version
        # and comfyui_version are the reliable identifiers. argv shows the port
        # the instance itself thinks it's on (remote's own :8188 forwarded here).
        return {
            "reachable": True,
            "comfyui_version": sysd.get("comfyui_version"),
            "pytorch_version": sysd.get("pytorch_version"),
            "python_version": sysd.get("python_version"),
            "os": sysd.get("os"),
            "ram_total": sysd.get("ram_total"),
            "ram_free": sysd.get("ram_free"),
            "argv": sysd.get("argv"),
            "devices": sysd.get("devices", []),
        }
    except Exception as e:
        return {"reachable": False, "error": str(e)[:120]}


async def _gateway_status() -> dict:
    """AIComic gateway backend health + raw backends. The gateway may be
    configured with any subset of [8188, 8189(remote), 8190, ...]; we no longer
    hardcode the 8188/8189 pair — we surface exactly whatever backends the
    gateway was launched with (so new cards/ports are detected automatically)."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(GATEWAY_HEALTH)
            d = r.json()
        backends = d.get("backends", [])
        bmap = {b["url"]: b["ok"] for b in backends}
        return {
            "up": True,
            "comfyui_all_ok": d.get("comfyui"),
            "workers": d.get("workers"),
            "queued": d.get("queued"),
            "_backends_raw": backends,
            "_bmap": bmap,
            "backends": [],
        }
    except Exception:
        return {"up": False, "backends": []}


# --------------------------------------------------------------------------- routes
@app.get("/api/state")
async def state():
    svcs = _model_services_status()
    await _probe_ports(svcs)
    idle, thr = _idle_minutes()
    up_count = sum(1 for s in svcs if s["state"] == "UP")
    gpus = _gpu_status()
    gpu1 = next((g for g in gpus if g["index"] == 1), None)
    # Scan the 8188-8195 port range for ComfyUI listeners (local python and/or
    # remote SSH tunnels). This is how we detect: the primary :8188, the remote
    # tunnel on :8189, OUR local second-GPU comfy on :8190, AND any future extra
    # card/tunnel the user wires onto :8191+ — all without code changes.
    listeners = _scan_comfy_listeners()
    listener_by_port = {l["port"]: l for l in listeners}
    # Per-port status: combine lsof kind detection with our pidfile (for our own
    # :8190, which may be STARTING with no listener yet) + /system_stats probe.
    own_local_pidfile = LOGDIR / "comfy-local.pid"
    comfy_ports: list[dict] = []
    # always include our own :8190 slot (even if DOWN) so the panel shows the
    # local second-GPU comfy row; plus every port that has a live listener.
    ports_to_report = {COMFY_LOCAL_PORT} | set(listener_by_port)
    for port in sorted(ports_to_report):
        pidfile = own_local_pidfile if port == COMFY_LOCAL_PORT else None
        st = _comfy_port_status(port, pidfile)
        # If the port is listening but lsof didn't classify it (kind=unknown but
        # state=UP via pidfile), use the listener's kind from the sweep.
        if st["kind"] == "unknown" and port in listener_by_port:
            st["kind"] = listener_by_port[port]["kind"]
        # health + system_info probe (works for local AND remote-tunneled).
        # Non-ComfyUI listeners (e.g. a stray socket) will be unreachable —
        # that's fine, the row still shows the port + kind so the operator sees
        # something IS on that port.
        health = "down"
        if st["state"] in ("UP", "STARTING"):
            try:
                async with httpx.AsyncClient(timeout=2.0) as c:
                    r = await c.get(f"http://127.0.0.1:{port}/system_stats")
                    health = "up" if r.status_code < 400 else f"HTTP {r.status_code}"
            except Exception:
                health = "unreachable"
        st["health"] = health
        st["system_info"] = await _comfy_system_info(port)
        st["ours"] = (port == COMFY_LOCAL_PORT)  # the local GPU1 comfy we manage
        comfy_ports.append(st)
    gateway = await _gateway_status()
    # gateway per-backend detail: enrich each backend with its own system_info
    # (so a remote-tunneled backend's version/gpu shows up in the panel)
    if gateway.get("up"):
        enriched = []
        for b in (gateway.get("_backends_raw") or []):
            url = b.get("url", "")
            port = url.rstrip("/").rsplit(":", 1)[-1]
            try:
                port_i = int(port)
                b["system_info"] = await _comfy_system_info(port_i) if b.get("ok") else {"reachable": False}
            except Exception:
                b["system_info"] = {"reachable": False}
            enriched.append(b)
        gateway["backends"] = enriched
    # mode reflects what's consuming LOCAL GPU1:
    #   copilot — HisCopilot model group up
    #   render  — OUR local ComfyUI :8190 up (kind=local-python, ours=true)
    #   free    — GPU1 empty
    #   partial — mixed/transitioning
    # A remote reverse-SSH tunnel (any port, kind=ssh-tunnel) does NOT consume
    # local GPU1 — it's just a forwarded backend the gateway fans out to — so it
    # does NOT flip mode to 'render'. The model group can run alongside it.
    own_local = next((c for c in comfy_ports if c["ours"]), None)
    local_render_up = own_local and own_local["state"] in ("UP", "STARTING") and own_local["kind"] == "local-python"
    if local_render_up:
        mode = "render"
    elif up_count >= 3:
        mode = "copilot"
    elif gpu1 and gpu1["mem_free_mb"] > 60000 and up_count == 0:
        mode = "free"
    else:
        mode = "partial"
    return {
        "ts": int(time.time()),
        "gpus": gpus,
        "model_services": svcs,
        "model_up_count": up_count,
        "watcher": _watcher_status(),
        "idle_minutes": idle,
        "idle_threshold_minutes": thr,
        "mode": mode,
        "last_activity_epoch": _last_activity(),
        "comfy_ports": comfy_ports,
        "gateway": gateway,
    }


@app.post("/api/model/{action}")
async def model_action(action: str, request: Request):
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, f"unknown action: {action}")
    # run the script; capture output for the UI
    proc = await asyncio.create_subprocess_exec(
        "bash", MODEL_SCRIPT, action,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out = (await proc.communicate())[0].decode(errors="replace")
    return {"action": action, "rc": proc.returncode, "output": out[-2000:]}


@app.post("/api/watcher/{action}")
async def watcher_action(action: str, request: Request):
    if action not in ("start", "stop", "enable", "disable", "restart"):
        raise HTTPException(400, f"unknown action: {action}")
    proc = await asyncio.create_subprocess_exec(
        "bash", WATCHER_SCRIPT, action,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out = (await proc.communicate())[0].decode(errors="replace")
    return {"action": action, "rc": proc.returncode, "output": out[-2000:]}


@app.post("/api/comfy-local/{action}")
async def comfy_local_action(action: str, request: Request):
    """Direct start/stop of the second LOCAL ComfyUI on GPU1 (:8190). Does NOT
    touch HisCopilot, and never touches the remote :8189 tunnel."""
    if action not in ("start", "stop", "restart", "prereq"):
        raise HTTPException(400, f"unknown action: {action}")
    proc = await asyncio.create_subprocess_exec(
        "bash", COMFY_LOCAL_SCRIPT, action,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out = (await proc.communicate())[0].decode(errors="replace")
    return {"action": action, "rc": proc.returncode, "output": out[-3000:]}


@app.post("/api/mode/{target}")
async def mode_switch(target: str, request: Request):
    """Switch GPU1 between 'copilot' (HisCopilot model group) and 'render'
    (ComfyUI :8190 for AIComic dual-GPU). Mutually exclusive: switching to one
    stops the other first, with proper wait for GPU memory to free.

    target: copilot | render
    """
    if target not in ("copilot", "render"):
        raise HTTPException(400, f"unknown mode: {target}")
    steps: list[str] = []
    # 1. Stop the OTHER side first (free GPU1). Disable watcher during the switch
    #    so it doesn't fight us mid-transition.
    if target == "render":
        steps.append("disable watcher (防切换中误停)")
        await _run_script(WATCHER_SCRIPT, "disable")
        steps.append("stop hiscopilot model group")
        await _run_script(MODEL_SCRIPT, "stop")
        steps.append("start comfy-local :8190 (GPU1)")
        rc, out = await _run_script(COMFY_LOCAL_SCRIPT, "start")
        steps.append(f"comfy-local rc={rc}")
        # render 模式不自动恢复 watcher：出片期间应保持 disable，避免中途误停 8190
        return {"target": target, "steps": steps, "rc": rc, "output": out[-3000:]}
    else:  # copilot
        steps.append("stop comfy-local :8190")
        await _run_script(COMFY_LOCAL_SCRIPT, "stop")
        steps.append("start hiscopilot model group (GPU1)")
        rc, out = await _run_script(MODEL_SCRIPT, "start")
        steps.append(f"model-start rc={rc}")
        # 回到门诊模式后自动恢复 watcher 自动停（医生不用时会再释放）
        steps.append("enable watcher")
        await _run_script(WATCHER_SCRIPT, "enable")
        return {"target": target, "steps": steps, "rc": rc, "output": out[-3000:]}


async def _run_script(script: str, *args) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "bash", script, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out = (await proc.communicate())[0].decode(errors="replace")
    return proc.returncode or 0, out


@app.get("/api/logs/{which}")
async def logs(which: str):
    """Tail last 80 lines of a log file. which: watcher | model | api"""
    mapping = {
        "watcher": LOGDIR / "idle-watcher.log",
        "api": LOGDIR / "api.log",
        "clinical_main": LOGDIR / "clinical-main-server.log",
        "tcm": LOGDIR / "tcm-server.log",
        "asr": LOGDIR / "asr-server.log",
        "ocr": LOGDIR / "ocr-server.log",
        "comfy-local": LOGDIR / "comfy-local.log",
    }
    path = mapping.get(which)
    if not path or not path.exists():
        raise HTTPException(404, f"no log for {which}")
    try:
        lines = path.read_text(errors="replace").splitlines()[-80:]
        return {"which": which, "lines": lines}
    except Exception as e:
        raise HTTPException(500, str(e))


PAGE = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GPU 控制面板</title>
<style>
  :root {
    --bg:#0f1420; --card:#171e2e; --card2:#1e2740; --border:#2a3450;
    --fg:#e6ecf5; --muted:#8a97b3; --accent:#5b9dff; --accent2:#7c5bff;
    --ok:#34d399; --warn:#fbbf24; --err:#f87171; --idle:#60a5fa;
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
         background:var(--bg); color:var(--fg); -webkit-font-smoothing:antialiased; }
  .wrap { max-width:1180px; margin:0 auto; padding:24px 20px 60px; }
  header { display:flex; align-items:center; justify-content:space-between; margin-bottom:22px; flex-wrap:wrap; gap:12px; }
  h1 { margin:0; font-size:20px; font-weight:600; letter-spacing:.3px; }
  h1 .sub { color:var(--muted); font-weight:400; font-size:13px; margin-left:10px; }
  .live { display:inline-flex; align-items:center; gap:6px; font-size:12px; color:var(--muted); }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--ok); animation:pulse 1.6s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
  .grid { display:grid; gap:16px; }
  .row2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  @media(max-width:820px){ .row2{ grid-template-columns:1fr; } }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:18px; }
  .card h2 { margin:0 0 14px; font-size:14px; color:var(--muted); text-transform:uppercase; letter-spacing:.8px; font-weight:600; }
  .mode-badge { display:inline-block; padding:3px 10px; border-radius:20px; font-size:12px; font-weight:600; }
  .mode-copilot { background:rgba(52,211,153,.15); color:var(--ok); }
  .mode-free { background:rgba(96,165,250,.15); color:var(--idle); }
  .mode-partial { background:rgba(251,191,36,.15); color:var(--warn); }
  .mode-render { background:rgba(124,91,255,.18); color:var(--accent2); }
  /* mode switch hero */
  .hero { display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
  .mode-switch { display:flex; gap:10px; }
  .mode-btn { font-size:14px; padding:10px 18px; border-radius:10px; border:1px solid var(--border);
              background:var(--card2); color:var(--muted); cursor:pointer; font-weight:600; transition:all .15s;
              display:flex; flex-direction:column; align-items:center; gap:2px; }
  .mode-btn .mb-sub { font-size:10px; font-weight:400; color:var(--muted); }
  .mode-btn:hover:not(:disabled) { border-color:var(--accent); }
  .mode-btn.active.copilot { background:rgba(52,211,153,.18); border-color:var(--ok); color:var(--ok); }
  .mode-btn.active.render { background:rgba(124,91,255,.18); border-color:var(--accent2); color:var(--accent2); }
  .mode-btn:disabled { opacity:.5; cursor:not-allowed; }
  .switching { font-size:12px; color:var(--warn); }
  /* GPU cards */
  .gpu { display:flex; flex-direction:column; gap:10px; }
  .gpu-head { display:flex; justify-content:space-between; align-items:baseline; }
  .gpu-name { font-size:14px; font-weight:600; }
  .gpu-sub { font-size:12px; color:var(--muted); }
  .bar { height:8px; background:#0a0f1c; border-radius:6px; overflow:hidden; }
  .bar-fill { height:100%; border-radius:6px; transition:width .4s, background .4s; }
  .gpu-stats { display:flex; gap:16px; font-size:12px; color:var(--muted); flex-wrap:wrap; }
  .gpu-stats b { color:var(--fg); font-weight:600; }
  /* services */
  .svc-list { display:flex; flex-direction:column; gap:8px; }
  .svc { display:grid; grid-template-columns:auto 1fr auto auto; align-items:center; gap:12px;
         padding:10px 12px; background:var(--card2); border-radius:8px; border:1px solid transparent; }
  .svc.up { border-color:rgba(52,211,153,.3); }
  .svc.down { border-color:rgba(248,113,113,.15); opacity:.85; }
  .state-pill { font-size:11px; padding:2px 8px; border-radius:10px; font-weight:600; }
  .state-pill.UP { background:rgba(52,211,153,.18); color:var(--ok); }
  .state-pill.DOWN { background:rgba(248,113,113,.15); color:var(--err); }
  .svc-name { font-size:13px; font-weight:500; }
  .svc-note { font-size:11px; color:var(--muted); }
  .svc-meta { font-size:11px; color:var(--muted); text-align:right; }
  /* watcher */
  .watcher-row { display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid var(--border); }
  .watcher-row:last-child { border:none; }
  .idle-big { font-size:32px; font-weight:700; }
  .idle-big.warn { color:var(--warn); }
  .idle-big.ok { color:var(--ok); }
  .idle-label { font-size:12px; color:var(--muted); }
  /* buttons */
  .btns { display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; }
  button { font-family:inherit; font-size:13px; padding:8px 14px; border-radius:8px; border:1px solid var(--border);
           background:var(--card2); color:var(--fg); cursor:pointer; transition:all .15s; font-weight:500; }
  button:hover:not(:disabled) { border-color:var(--accent); background:#26314f; }
  button:disabled { opacity:.45; cursor:not-allowed; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  button.primary:hover:not(:disabled) { background:#4a8aef; }
  button.danger { background:rgba(248,113,113,.15); border-color:rgba(248,113,113,.4); color:var(--err); }
  button.danger:hover:not(:disabled) { background:rgba(248,113,113,.25); }
  button.ok-btn { background:rgba(52,211,153,.15); border-color:rgba(52,211,153,.4); color:var(--ok); }
  /* log */
  .log-box { background:#0a0f1c; border:1px solid var(--border); border-radius:8px; padding:12px;
             font-family:"SF Mono",Menlo,Consolas,monospace; font-size:11.5px; line-height:1.5;
             color:#a8b3cc; max-height:280px; overflow:auto; white-space:pre-wrap; word-break:break-all; }
  .log-tabs { display:flex; gap:6px; margin-bottom:10px; }
  .log-tab { font-size:12px; padding:5px 10px; border-radius:6px; border:1px solid var(--border); background:transparent; color:var(--muted); cursor:pointer; }
  .log-tab.active { background:var(--card2); color:var(--fg); border-color:var(--accent); }
  /* toast */
  .toast { position:fixed; bottom:24px; left:50%; transform:translateX(-50%); padding:12px 20px;
           background:var(--card2); border:1px solid var(--accent); border-radius:10px; font-size:13px;
           box-shadow:0 8px 24px rgba(0,0,0,.4); opacity:0; transition:opacity .3s, transform .3s; z-index:99; }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(-4px); }
  .toast.err { border-color:var(--err); }
  .footer { margin-top:24px; font-size:11px; color:var(--muted); text-align:center; }
</style></head>
<body>
<div class="wrap">
  <header>
    <h1>GPU 控制面板<span class="sub">HisCopilot 模型组 · GPU1</span></h1>
    <span class="live"><span class="dot"></span>实时 <span id="ts"></span></span>
  </header>

  <div class="grid">
    <!-- 模式 + 一键切换 -->
    <div class="card">
      <h2>GPU1 模式</h2>
      <div class="hero">
        <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
          <span id="modeBadge" class="mode-badge mode-partial">检测中…</span>
          <span style="font-size:13px; color:var(--muted)" id="modeDesc"></span>
          <span style="font-size:12px; color:var(--muted)" id="modelUpCount"></span>
        </div>
        <div class="mode-switch">
          <button class="mode-btn copilot" id="btnCopilot" onclick="switchMode('copilot')">
            门诊模式<span class="mb-sub">HisCopilot 模型组</span>
          </button>
          <button class="mode-btn render" id="btnRender" onclick="switchMode('render')">
            双卡出片模式<span class="mb-sub">ComfyUI :8190</span>
          </button>
        </div>
      </div>
      <div id="switchStatus" style="margin-top:10px; font-size:12px; color:var(--muted); min-height:16px"></div>
      <div id="gatewayStatus" style="margin-top:6px; font-size:11px; color:var(--muted)"></div>
    </div>

    <!-- 两卡 -->
    <div class="row2">
      <div class="card gpu" id="gpu0"></div>
      <div class="card gpu" id="gpu1"></div>
    </div>

    <div class="row2">
      <!-- 模型组 -->
      <div class="card">
        <h2>GPU1 模型服务</h2>
        <div class="svc-list" id="svcList"></div>
        <div class="btns">
          <button class="primary" onclick="act('model','start')">启动全部</button>
          <button class="danger" onclick="act('model','stop')">停止全部</button>
          <button onclick="act('model','restart')">重启全部</button>
        </div>
      </div>

      <!-- idle-watcher -->
      <div class="card">
        <h2>空闲自动停</h2>
        <div class="watcher-row">
          <div>
            <div class="idle-big ok" id="idleBig">—</div>
            <div class="idle-label" id="idleLabel">距上次活动</div>
          </div>
          <div style="text-align:right">
            <div style="font-size:13px; font-weight:600" id="watcherState">守护进程 —</div>
            <div style="font-size:12px; color:var(--muted)" id="autoStopState">自动停 —</div>
          </div>
        </div>
        <div class="btns">
          <button class="ok-btn" onclick="act('watcher','enable')" id="btnEnable">启用自动停</button>
          <button class="warn" style="background:rgba(251,191,36,.15);border-color:rgba(251,191,36,.4);color:var(--warn)" onclick="act('watcher','disable')">临时禁用</button>
          <button onclick="act('watcher','start')" id="btnWStart">启动守护</button>
          <button class="danger" onclick="act('watcher','stop')">停止守护</button>
        </div>
        <div style="margin-top:10px; font-size:11px; color:var(--muted); line-height:1.6">
          空闲 <b id="thrMin" style="color:var(--fg)">120</b> 分钟自动停模型组，释放 GPU1 给 AIComic 双卡出片。<br>
          停后<b style="color:var(--fg)">不自动恢复</b>，需手动「启动全部」或医生端触发会写入活动心跳。
        </div>
      </div>
    </div>

    <!-- AIComic 双后端 / 远程检测 (只读) -->
    <div class="card">
      <h2>AIComic ComfyUI 后端（只读检测）</h2>
      <div id="backendList" style="display:flex; flex-direction:column; gap:10px"></div>
      <div style="margin-top:8px; font-size:11px; color:var(--muted); line-height:1.5">
        后端列表来自 gateway :8100 /health。<b style="color:var(--fg)">本地 python</b> = 本机 ComfyUI 进程；
        <b style="color:var(--accent2)">SSH 隧道</b> = 远程服务器经反向 SSH 转发进来。
        显示版本/Python/PyTorch/GPU 来自各后端 /system_stats，<b>只读</b>，不会操作远程服务。
      </div>
    </div>

    <!-- 日志 -->
    <div class="card">
      <h2>日志</h2>
      <div class="log-tabs" id="logTabs">
        <span class="log-tab active" data-l="watcher">idle-watcher</span>
        <span class="log-tab" data-l="api">api</span>
        <span class="log-tab" data-l="clinical_main">临床主模型</span>
        <span class="log-tab" data-l="asr">ASR</span>
        <span class="log-tab" data-l="tcm">中医</span>
        <span class="log-tab" data-l="ocr">OCR</span>
        <span class="log-tab" data-l="comfy-local">ComfyUI 8190</span>
      </div>
      <div class="log-box" id="logBox">加载中…</div>
    </div>
  </div>

  <div class="footer">面板只绑 127.0.0.1:8099 · 副作用走 model-servers.sh / idle-watcher.sh · 状态在 pidfile + last_activity</div>
</div>
<div class="toast" id="toast"></div>

<script>
let curLog='watcher', pollTimer=null, logTimer=null;
const $ = id => document.getElementById(id);

function fmt(n){ return (n||0).toLocaleString(); }
function mbstr(mb){ return fmt(mb)+' MiB'; }
function pct(used,total){ return total? Math.round(used/total*100):0; }

function gpuCard(g){
  if(!g) return '<div class="gpu-name">GPU 信息不可用（nvidia-smi 失败）</div>';
  const p = pct(g.mem_used_mb, g.mem_total_mb);
  const color = p>90?'var(--err)':p>70?'var(--warn)':'var(--ok)';
  const tag = g.index===0 ? 'ComfyUI :8188 · AIComic' : (g.mem_free_mb>60000 ? 'GPU1 空闲' : 'GPU1 占用中');
  return `
    <div class="gpu-head">
      <div><div class="gpu-name">GPU${g.index} · ${g.name}</div><div class="gpu-sub">${tag}</div></div>
      <div style="text-align:right"><div style="font-size:18px;font-weight:700;color:${color}">${p}%</div><div class="gpu-sub">占用</div></div>
    </div>
    <div class="bar"><div class="bar-fill" style="width:${p}%;background:${color}"></div></div>
    <div class="gpu-stats">
      <span>已用 <b>${mbstr(g.mem_used_mb)}</b></span>
      <span>空闲 <b>${mbstr(g.mem_free_mb)}</b></span>
      <span>总量 <b>${mbstr(g.mem_total_mb)}</b></span>
      <span>利用率 <b>${g.util_pct}%</b></span>
      <span>温度 <b>${g.temp_c}°C</b></span>
    </div>`;
}

function render(s){
  $('gpu0').innerHTML = gpuCard((s.gpus||[]).find(g=>g.index===0));
  $('gpu1').innerHTML = gpuCard((s.gpus||[]).find(g=>g.index===1));
  // services
  $('svcList').innerHTML = s.model_services.map(sv=>`
    <div class="svc ${sv.state.toLowerCase()}">
      <span class="state-pill ${sv.state}">${sv.state}</span>
      <div><div class="svc-name">${sv.name}</div><div class="svc-note">${sv.note}</div></div>
      <span class="svc-meta">:${sv.port}${sv.health?' · '+sv.health:''}</span>
      <span class="svc-meta">${sv.pid?('pid '+sv.pid):'—'}</span>
    </div>`).join('');
  // our local second-GPU comfy (:8190) summary for the mode card
  const ours = (s.comfy_ports||[]).find(c=>c.ours);
  const oursState = ours ? ours.state : 'DOWN';
  $('modelUpCount').textContent = `模型组 ${s.model_up_count}/6 · 本地8190 ${oursState}`;
  // mode
  const mb=$('modeBadge'), md=$('modeDesc');
  const labels={
    copilot:['门诊模式','医生端可用 · GPU1 跑模型组'],
    render:['双卡出片','GPU1 跑 ComfyUI :8190 · gateway 多后端 fan-out'],
    free:['空闲','GPU1 已释放 · 可切换到任一模式'],
    partial:['部分','模型组/8190 未完全启动'],
  };
  const [t,d]=labels[s.mode]||['未知',''];
  mb.textContent=t; mb.className='mode-badge mode-'+s.mode;
  md.textContent=d;
  // mode switch buttons
  const bc=$('btnCopilot'), br=$('btnRender');
  bc.classList.toggle('active', s.mode==='copilot');
  br.classList.toggle('active', s.mode==='render');
  // gateway summary — list exactly the backends the gateway was launched with
  const gw=s.gateway||{};
  let gwHtml = 'AIComic gateway :8100 未运行';
  if(gw.up){
    const bs=(gw.backends&&gw.backends.length)?gw.backends:(gw._backends_raw||[]);
    const parts = bs.map(b=>{
      const port=(b.url||'').split(':').pop();
      const ok=b.ok;
      return `:${port}=<b style="color:${ok?'var(--ok)':'var(--err)'}">${ok?'up':'down'}</b>`;
    }).join(' · ');
    gwHtml = `AIComic gateway :8100 · workers=${gw.workers??'-'} · ${parts}`;
  }
  $('gatewayStatus').innerHTML = gwHtml;
  // backend list (with remote detection)
  renderBackends(s);
  // watcher
  const idle=s.idle_minutes, thr=s.idle_threshold_minutes;
  $('idleBig').textContent = idle<0?'无记录':idle+'m';
  $('idleBig').className = 'idle-big '+(idle<0?'':(idle>=thr?'warn':'ok'));
  $('idleLabel').textContent = idle<0?'尚无活动记录':('距上次活动 · 阈值 '+thr+'m');
  $('thrMin').textContent = thr;
  $('watcherState').textContent = '守护进程 '+(s.watcher.state==='UP'?'运行中 (pid '+s.watcher.pid+')':'已停止');
  $('autoStopState').textContent = '自动停 '+s.watcher.auto_stop;
  $('btnEnable').disabled = s.watcher.auto_stop==='ENABLED';
  $('btnWStart').disabled = s.watcher.state==='UP';
  $('ts').textContent = new Date(s.ts*1000).toLocaleTimeString('zh-CN');
}

async function refresh(){
  try{
    const r = await fetch('/api/state');
    if(!r.ok) throw 0;
    render(await r.json());
  }catch(e){ $('ts').textContent='(获取失败)'; }
}
async function act(kind,action){
  $('toast').className='toast';
  try{
    const r = await fetch(`/api/${kind}/${action}`,{method:'POST'});
    const j = await r.json();
    toast(action+' 完成 (rc='+j.rc+')', false);
    setTimeout(refresh, 400); setTimeout(refresh, 2500); // re-poll after process settle
  }catch(e){ toast('操作失败', true); }
}
function fmtBytes(b){ if(b==null) return '-'; const u=['B','KB','MB','GB','TB']; let i=0,v=b; while(v>=1024&&i<u.length-1){v/=1024;i++;} return v.toFixed(v<10?1:0)+' '+u[i]; }

// Build a map port->detected-listener {kind,pid,state,health} from our lsof sweep,
// so each gateway backend row is labeled local-python / ssh-tunnel correctly.
function listenerMap(s){
  const m={};
  (s.comfy_ports||[]).forEach(c=>{ m[String(c.port)] = c; });
  return m;
}

function renderBackends(s){
  const el=$('backendList'); if(!el) return;
  const gw=s.gateway||{};
  const lmap = listenerMap(s);
  // Show the backends the gateway actually fans out to. If gateway is down,
  // still show whatever ComfyUI listeners our lsof sweep found (8188/8189/8190...)
  // so the read-only detection is useful even without a gateway.
  let rows=[];
  if(gw.up && gw.backends && gw.backends.length){
    rows = gw.backends.map(b=>{
      const port=(b.url||'').split(':').pop();
      const det = lmap[port]||{};
      return {port, ok:b.ok, system_info:b.system_info||{}, kind:det.kind, lanes:b.lanes||[], from:'gateway'};
    });
  } else {
    // gateway down — render from the lsof sweep directly
    rows = (s.comfy_ports||[]).map(c=>({port:String(c.port), ok:(c.state==='UP'), system_info:c.system_info||{}, kind:c.kind, lanes:[], from:'listener'}));
  }
  el.innerHTML = rows.map(r=>{
    const port=r.port; const ok=r.ok; const si=r.system_info||{};
    const kind = r.kind || (String(port)==='8188' ? 'local-python' : 'unknown');
    const kindLabel = {
      'local-python':'<span style="color:var(--ok)">本地 python</span>',
      'ssh-tunnel':'<span style="color:var(--accent2)">SSH 隧道（远程）</span>',
      'unknown':'<span style="color:var(--muted)">未知</span>',
    }[kind] || '<span style="color:var(--muted)">未知</span>';
    const gpuInfo = (si.devices&&si.devices.length)
      ? si.devices.map(d=>`${d.name||'GPU'} ${d.vram_total?('VRAM '+fmtBytes(d.vram_total)):''}`).join(', ')
      : '(未加载模型时无 GPU 信息)';
    const argvPort = si.argv ? (si.argv.join(' ').match(/--port\\s+(\\d+)/)||[])[1] : null;
    const argvHint = argvPort && String(argvPort)!==String(port) ? ` · 自身端口 :${argvPort}（经隧道转发到本地 :${port}）` : '';
    const lanesTxt = r.lanes && r.lanes.length ? ('lanes: '+r.lanes.join(',')) : '';
    return `<div class="svc ${ok?'up':'down'}" style="grid-template-columns:auto 1fr auto">
      <span class="state-pill ${ok?'UP':'DOWN'}">${ok?'UP':'DOWN'}</span>
      <div>
        <div class="svc-name">:${port} ${kindLabel}${argvHint}</div>
        <div class="svc-note">${si.reachable?('ComfyUI '+si.comfyui_version+' · py '+(si.python_version||'').split(' ')[0]+' · pt '+si.pytorch_version):('不可达'+(si.error?(' · '+si.error):''))}</div>
        <div class="svc-note">GPU: ${gpuInfo}</div>
      </div>
      <span class="svc-meta">${lanesTxt}</span>
    </div>`;
  }).join('') || '<div style="color:var(--muted);font-size:12px">无 ComfyUI 监听（8188-8195 全无）</div>';
}
async function switchMode(target){
  const other = target==='copilot'?'门诊模式（停 8190 起模型组）':'双卡出片（停模型组起 8190）';
  if(!confirm('切换到 '+other+'？\\n这个过程会停掉当前 GPU1 上的服务并启动另一组，约需 1-2 分钟。')) return;
  $('btnCopilot').disabled=true; $('btnRender').disabled=true;
  $('switchStatus').innerHTML = '<span class="switching">切换中… 停止当前服务 + 启动目标（请勿离开）</span>';
  try{
    const r = await fetch(`/api/mode/${target}`,{method:'POST'});
    const j = await r.json();
    $('switchStatus').innerHTML = `切换完成 rc=${j.rc}：<br>${j.steps.join(' → ')}`;
    toast('已切换到 '+(target==='copilot'?'门诊模式':'双卡出片'), false);
  }catch(e){
    $('switchStatus').textContent='切换失败: '+e;
    toast('切换失败', true);
  }finally{
    $('btnCopilot').disabled=false; $('btnRender').disabled=false;
    setTimeout(refresh,500); setTimeout(refresh,3000); setTimeout(refresh,8000);
  }
}
function toast(msg,err){
  const t=$('toast'); t.textContent=msg; t.className='toast show'+(err?' err':'');
  setTimeout(()=>t.className='toast', 2400);
}
async function loadLog(which){
  curLog=which;
  document.querySelectorAll('.log-tab').forEach(t=>t.classList.toggle('active', t.dataset.l===which));
  $('logBox').textContent='加载中…';
  try{
    const r=await fetch('/api/logs/'+which);
    const j=await r.json();
    $('logBox').textContent=j.lines.join('\\n')||'(空)';
    $('logBox').scrollTop=$('logBox').scrollHeight;
  }catch(e){ $('logBox').textContent='(读取失败)'; }
}

document.querySelectorAll('.log-tab').forEach(t=>t.onclick=()=>loadLog(t.dataset.l));
pollTimer=setInterval(refresh, 3000);
logTimer=setInterval(()=>loadLog(curLog), 8000);
refresh(); loadLog('watcher');
</script>
</body></html>
"""


@app.get("/", response_class=HTMLResponse)
async def page():
    return PAGE


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("GPU_PANEL_PORT", "8099"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
