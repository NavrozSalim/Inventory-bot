"""Laptop agent: takes "Upload the file on the Marketplace" jobs from the web app and runs the bot here.

Leave run_agent.bat open. Every few seconds it asks the web app for a job. For each job it downloads the
ZIP of the chosen vendors' files, runs run_inventory.py on it, and sends the log, the per-file results and
any "action needed" message (e.g. type the verification code) back while it runs. Cancel in the web app
stops the bot and its Chrome.

Settings: agent.json (copy agent.example.json): server (web app address) and token (ingest token with scope
"inventory-bot"). Environment INVENTORYBOT_SERVER / INVENTORYBOT_TOKEN override them.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
API = "/api/v1/ingest/inventory-bot"
LOG_LIMIT = 200_000
CANCELLED_EXIT = 130


class AgentConfigError(Exception):
    pass


def load_agent_config(path=ROOT / "agent.json"):
    cfg = {"server": "", "token": "", "poll_seconds": 15, "progress_seconds": 20, "python": sys.executable}
    path = Path(path)
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8-sig")))
        except ValueError as e:
            raise AgentConfigError(f"{path.name} is not valid JSON: {e}") from None
    cfg["server"] = (os.environ.get("INVENTORYBOT_SERVER") or cfg["server"] or "").strip().rstrip("/")
    cfg["token"] = (os.environ.get("INVENTORYBOT_TOKEN") or cfg["token"] or "").strip()
    if not cfg["server"] or cfg["server"].startswith("<") or not cfg["token"] or cfg["token"].startswith("<"):
        raise AgentConfigError("Set server and token in agent.json (copy agent.example.json).")
    return cfg


def build_command(job, python, zip_path, results_path):
    cmd = [python, "-u", str(ROOT / "run_inventory.py"), "--zip", str(zip_path),
           "--results-json", str(results_path)]
    for kind in job.get("kinds") or []:
        cmd += ["--kinds", kind]
    if job.get("dry_run"):
        cmd.append("--dry-run")
    if job.get("include_unmapped"):
        cmd.append("--include-unmapped")
    return cmd


def kill_tree(proc):
    """Stop the bot and the Chrome it started (Windows: the whole process tree)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
        else:
            proc.terminate()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class Agent:
    def __init__(self, cfg, *, http=requests, popen=subprocess.Popen, sleep=time.sleep, clock=time.monotonic,
                 out=print, work_root=ROOT / "work"):
        self.cfg = cfg
        self.http = http
        self.popen = popen
        self.sleep = sleep
        self.clock = clock
        self.out = out
        self.work_root = Path(work_root)
        self.headers = {"Authorization": f"Bearer {cfg['token']}"}

    def url(self, path):
        return path if path.startswith("http") else f"{self.cfg['server']}{path}"

    # ---------- server calls ----------
    def next_job(self):
        r = self.http.get(self.url(f"{API}/next-job/"), headers=self.headers, timeout=60)
        if r.status_code in (401, 403):
            raise AgentConfigError(f"The web app refused the token ({r.status_code}). Check token in agent.json.")
        r.raise_for_status()
        data = r.json()
        return data if data.get("job_id") else None

    def download(self, job, dest):
        r = self.http.get(self.url(job["file_url"]), headers=self.headers, timeout=300)
        if r.status_code != 200:
            try:
                msg = r.json().get("error")
            except Exception:
                msg = ""
            raise RuntimeError(msg or f"download failed ({r.status_code})")
        Path(dest).write_bytes(r.content)

    def post(self, path, payload):
        try:
            r = self.http.post(self.url(path), headers=self.headers, json=payload, timeout=60)
            return r.json().get("status", "") if r.status_code == 200 else ""
        except Exception as e:
            self.out(f"[agent] could not reach the web app: {type(e).__name__}")
            return ""

    # ---------- one job ----------
    def run_job(self, job):
        job_id = job["job_id"]
        workdir = self.work_root / f"agent-{job_id[:8]}"
        shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True, exist_ok=True)
        zip_path, results_path, attention_path = workdir / "job.zip", workdir / "results.json", workdir / "attention.txt"
        vendors = ", ".join(v.get("name", "") for v in job.get("vendors") or [])
        self.out(f"[agent] job {job_id[:8]} for {job.get('store_name', '')}: {vendors} "
                 f"({' + '.join(job.get('kinds') or [])}{', dry run' if job.get('dry_run') else ''})")
        lines = []
        try:
            try:
                self.download(job, zip_path)
            except Exception as e:
                msg = f"STOPPED: could not download the files from the web app: {e}"
                self.out(f"[agent] {msg}")
                self.post(job["complete_url"], {"exit_code": 2, "log": msg, "message": msg})
                return 2

            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                       INVENTORY_BOT_ATTENTION_FILE=str(attention_path))
            proc = self.popen(build_command(job, self.cfg.get("python") or sys.executable, zip_path, results_path),
                              cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace")

            def pump():
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    lines.append(line)
                    self.out(line)

            reader = threading.Thread(target=pump, daemon=True)
            reader.start()

            def payload():
                data = _read_json(results_path)
                return {"log": "\n".join(lines)[-LOG_LIMIT:], "attention": _read_text(attention_path),
                        "results": data.get("results", [])}, data.get("message", "")

            cancelled = False
            every = float(self.cfg.get("progress_seconds", 20))
            next_report = self.clock()
            try:
                while proc.poll() is None:
                    if self.clock() >= next_report:
                        status = self.post(job["progress_url"], payload()[0])
                        next_report = self.clock() + every
                        if status == "cancelled":
                            self.out("[agent] cancelled in the web app; stopping the bot.")
                            kill_tree(proc)
                            cancelled = True
                            break
                    self.sleep(1)
            except KeyboardInterrupt:   # Ctrl+C on the laptop: end the job in the web app too
                kill_tree(proc)
                data, _ = payload()
                data.update({"exit_code": CANCELLED_EXIT, "message": "Stopped on the laptop (Ctrl+C).",
                             "attention": ""})
                self.post(job["complete_url"], data)
                raise
            try:
                proc.wait(timeout=60)
            except Exception:
                kill_tree(proc)
            reader.join(timeout=10)

            code = CANCELLED_EXIT if cancelled else proc.returncode
            data, message = payload()
            if cancelled:
                message = "Cancelled from the web app."
            elif not message:
                stops = [ln for ln in lines if "STOPPED:" in ln]
                message = stops[-1].split("] ", 1)[-1] if stops else ""
            data.update({"exit_code": code, "message": message, "attention": ""})
            self.post(job["complete_url"], data)
            self.out(f"[agent] job {job_id[:8]} finished (exit code {code}). {message}")
            return code
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    # ---------- loop ----------
    def loop(self, once=False):
        self.out(f"[agent] waiting for MyDeal upload jobs from {self.cfg['server']} "
                 f"(every {self.cfg.get('poll_seconds', 15)} s). Leave this window open; Ctrl+C to stop.")
        delay = float(self.cfg.get("poll_seconds", 15))
        failures = 0
        while True:
            try:
                job = self.next_job()
                failures = 0
            except AgentConfigError:
                raise
            except Exception as e:
                failures += 1
                if failures in (1, 5) or failures % 20 == 0:
                    self.out(f"[agent] web app not reachable ({type(e).__name__}); retrying.")
                job = None
            if job:
                self.run_job(job)
                if once:
                    return
                continue
            if once:
                return
            self.sleep(delay if not failures else min(120, delay * (1 + failures)))


def main():
    try:
        Agent(load_agent_config()).loop()
    except AgentConfigError as e:
        print(f"[agent] {e}")
        return 2
    except KeyboardInterrupt:
        print("[agent] stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
