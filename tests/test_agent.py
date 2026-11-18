"""agent.py with a fake web app and a fake bot process."""
import json
from pathlib import Path

import pytest

import agent as agent_mod
from agent import Agent, AgentConfigError, build_command, load_agent_config

SERVER = "https://app.test"
JOB = {
    "job_id": "11111111-2222-3333-4444-555555555555", "store_name": "BW-P&P",
    "vendors": [{"id": "v1", "name": "Vevor"}], "kinds": ["inventory", "price"], "dry_run": False,
    "include_unmapped": False,
    "status_url": "/api/v1/ingest/inventory-bot/jobs/1/", "file_url": "/api/v1/ingest/inventory-bot/jobs/1/file/",
    "progress_url": "/api/v1/ingest/inventory-bot/jobs/1/progress/",
    "complete_url": "/api/v1/ingest/inventory-bot/jobs/1/complete/",
}


class Resp:
    def __init__(self, status=200, data=None, content=b""):
        self.status_code = status
        self._data = data or {}
        self.content = content

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeHttp:
    def __init__(self, job=None, progress_status="running", file_status=200):
        self.job = job
        self.progress_status = progress_status
        self.file_status = file_status
        self.posts = []
        self.gets = []

    def get(self, url, headers=None, timeout=None):
        assert headers == {"Authorization": "Bearer tok"}
        self.gets.append(url)
        if url.endswith("/next-job/"):
            job, self.job = self.job, None
            return Resp(data=job or {"job_id": None})
        if url.endswith("/file/"):
            if self.file_status != 200:
                return Resp(self.file_status, {"error": "No files for the chosen vendors"})
            return Resp(content=b"PK-zip")
        raise AssertionError(url)

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append((url, json))
        status = self.progress_status if url.endswith("/progress/") else "finished"
        return Resp(data={"status": status})


class FakeProc:
    """Writes results.json and attention.txt like run_inventory.py, then exits."""

    def __init__(self, cmd, env, polls=3, code=0):
        self.cmd = cmd
        self.pid = 4242
        self._polls = polls
        self.returncode = None
        self._code = code
        results = Path(cmd[cmd.index("--results-json") + 1])
        results.write_text(json.dumps({"message": "Finished: 1 vendor(s)", "results": [
            {"vendor": "Vevor", "kind": "inventory", "dash_state": "completed"}]}), encoding="utf-8")
        Path(env["INVENTORY_BOT_ATTENTION_FILE"]).write_text("Type the verification code", encoding="utf-8")
        self.stdout = iter(["[11:00:00] === run ===\n", "[11:00:05] Inventory updated: Vevor\n"])

    def poll(self):
        if self._polls > 0:
            self._polls -= 1
            return None
        self.returncode = self._code
        return self._code

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = self._code
        return self.returncode


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make_agent(tmp_path, http, procs, **proc_kw):
    clock = Clock()

    def popen(cmd, cwd=None, env=None, **kw):
        p = FakeProc(cmd, env, **proc_kw)
        procs.append(p)
        return p

    out = []
    a = Agent({"server": SERVER, "token": "tok", "poll_seconds": 15, "progress_seconds": 20, "python": "py"},
              http=http, popen=popen, sleep=clock.sleep, clock=clock, out=out.append, work_root=tmp_path / "work")
    return a, out


def test_build_command():
    cmd = build_command({**JOB, "dry_run": True, "include_unmapped": True, "kinds": ["price"]}, "py",
                        Path("z.zip"), Path("r.json"))
    assert cmd[:2] == ["py", "-u"] and cmd[2].endswith("run_inventory.py")
    assert cmd[3:] == ["--zip", "z.zip", "--results-json", "r.json", "--kinds", "price", "--dry-run",
                       "--include-unmapped"]


def test_job_runs_bot_and_reports(tmp_path):
    http, procs = FakeHttp(job=JOB), []
    a, out = make_agent(tmp_path, http, procs)
    a.loop(once=True)
    assert http.gets[0] == f"{SERVER}/api/v1/ingest/inventory-bot/next-job/"
    assert procs[0].cmd[procs[0].cmd.index("--zip") + 1].endswith("job.zip")
    assert procs[0].cmd.count("--kinds") == 2
    progress = [p for u, p in http.posts if u.endswith("/progress/")]
    assert progress and progress[0]["attention"] == "Type the verification code"
    url, done = http.posts[-1]
    assert url == f"{SERVER}{JOB['complete_url']}"
    assert done["exit_code"] == 0 and done["message"] == "Finished: 1 vendor(s)"
    assert done["attention"] == "" and done["results"][0]["vendor"] == "Vevor"
    assert "Inventory updated: Vevor" in done["log"]
    assert not list((tmp_path / "work").glob("agent-*"))   # job files removed


def test_cancel_in_web_app_stops_the_bot(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(agent_mod, "kill_tree", lambda proc: killed.append(proc.pid))
    http, procs = FakeHttp(job=JOB, progress_status="cancelled"), []
    a, _ = make_agent(tmp_path, http, procs, polls=50)
    a.loop(once=True)
    assert killed == [4242]
    _, done = http.posts[-1]
    assert done["exit_code"] == 130 and done["message"] == "Cancelled from the web app."


def test_download_failure_reports_without_running(tmp_path):
    http, procs = FakeHttp(job=JOB, file_status=400), []
    a, _ = make_agent(tmp_path, http, procs)
    a.loop(once=True)
    assert procs == []
    _, done = http.posts[-1]
    assert done["exit_code"] == 2 and "No files for the chosen vendors" in done["message"]


def test_stop_message_taken_from_log_when_no_summary(tmp_path):
    http, procs = FakeHttp(job=JOB), []
    a, _ = make_agent(tmp_path, http, procs, code=2)

    real_init = FakeProc.__init__

    def init(self, cmd, env, **kw):
        real_init(self, cmd, env, **kw)
        Path(cmd[cmd.index("--results-json") + 1]).write_text('{"results": []}', encoding="utf-8")
        self.stdout = iter(["[11:00:01] STOPPED: Wrong IP: the IP lookup page shows 1.2.3.4\n"])

    FakeProc.__init__ = init
    try:
        a.loop(once=True)
    finally:
        FakeProc.__init__ = real_init
    _, done = http.posts[-1]
    assert done["exit_code"] == 2 and done["message"].startswith("STOPPED: Wrong IP")


def test_bad_token_and_config(tmp_path):
    class Refuse(FakeHttp):
        def get(self, url, headers=None, timeout=None):
            return Resp(403)

    a, _ = make_agent(tmp_path, Refuse(), [])
    with pytest.raises(AgentConfigError, match="refused the token"):
        a.loop(once=True)
    cfg = tmp_path / "agent.json"
    cfg.write_text(json.dumps({"server": "<web app address>", "token": "x"}), encoding="utf-8")
    with pytest.raises(AgentConfigError):
        load_agent_config(cfg)
    cfg.write_text(json.dumps({"server": "https://app.test/", "token": "abc"}), encoding="utf-8")
    assert load_agent_config(cfg)["server"] == "https://app.test"


def test_no_job_waits(tmp_path):
    http = FakeHttp(job=None)
    a, _ = make_agent(tmp_path, http, [])
    a.loop(once=True)
    assert http.posts == []
