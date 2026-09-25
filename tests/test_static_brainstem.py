"""Offline tests for rapp-static-brainstem.

They build their own fixture brainstem (tests/fixtures/brainstem), so they pass whatever you put in src/.
Local HTTP servers stand in for GitHub raw and for upstream agents.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import functools
import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "brainstem"
CODE = ["build.py", "run.py", "index.html", ".nojekyll"]
RAW = "https://raw.githubusercontent.com/example/brainstem/main/"
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
UNLINKED = ("GITHUB_REPOSITORY", "GITHUB_WORKSPACE")

AGENT = '''from basic_agent import BasicAgent


class {cls}(BasicAgent):
    def __init__(self):
        self.name = "{tool}"
        self.metadata = {{"name": self.name, "description": "{desc}", "parameters": {{"type": "object", "properties": {{}}}}}}
        super().__init__(name=self.name, metadata=self.metadata)

    def perform(self, **kwargs):
        {body}
'''


def run(*args, env=None, drop=()):
    merged = dict(os.environ, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    merged.update(env or {})
    for key in drop:
        merged.pop(key, None)
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, env=merged, timeout=120)


def tree(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Server:
    """Serve a folder over HTTP on localhost, the way GitHub raw serves a repo."""

    def __init__(self, folder: Path):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(folder)))
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class StaticBrainstemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rsb-"))
        self.root = self.tmp / "brainstem"
        shutil.copytree(FIXTURE, self.root)
        for name in CODE:
            shutil.copy(REPO / name, self.root / name)
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -------------------------------------------------------------- helpers

    def build(self, *extra, env=None, drop=()):
        return run(self.root / "build.py", *extra, env=env, drop=drop)

    def built(self, *extra, env=None, drop=()):
        result = self.build(*extra, env=env, drop=drop)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def load(self, rel):
        return json.loads((self.root / rel).read_text())

    def manifest(self, **changes):
        data = self.load("manifest.json")
        data.update(changes)
        (self.root / "manifest.json").write_text(json.dumps(data, indent=2))

    def entry(self, name):
        return next(e for e in self.load("registry.json")["entries"] if e["name"] == name)

    def call(self, name, args="{}", *extra, runner=None):
        result = run(runner or self.root / "run.py", "call", name, args, *extra)
        return result.returncode, json.loads(result.stdout)

    def serve(self, folder):
        server = Server(folder)
        self.servers.append(server)
        return server

    def upstream(self, filename, **fields):
        folder = self.tmp / "upstream"
        folder.mkdir(exist_ok=True)
        (folder / filename).write_text(AGENT.format(**{"desc": "upstream", "body": "return 'up'", **fields}))
        return folder

    def edit_hello(self):
        path = self.root / "src/agents/hello_agent.py"
        path.write_text(path.read_text().replace("Hello, {name}.", "Hi again, {name}."))

    # -------------------------------------------------------------- rapp-static-api/1.0 conformance

    def test_conformance_shape(self):
        self.built()
        reg = self.load("registry.json")
        self.assertEqual(reg["schema"], "rapp-static-brainstem/1.0")
        self.assertEqual(reg["spec"], "rapp-static-api/1.0")
        self.assertRegex(reg["generated"], ISO_Z)
        self.assertEqual(reg["raw_base"], RAW)
        self.assertEqual(reg["summary"]["status"], "ok")
        for e in reg["entries"]:
            self.assertEqual(len(e["sha8"]), 12)
            self.assertEqual(e["sha8"], e["sha256"][:12])
            self.assertTrue(e["sources"][0]["url"].startswith(RAW))
            data = (self.root / e["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), e["sha256"])
            self.assertEqual(e["path"], f"versions/{e['name']}/{e['sha8']}{os.path.splitext(e['name'])[1]}")
        status = self.load("api/v1/status.json")
        self.assertTrue(status["schema"].endswith("-status/1.0"))
        self.assertRegex(status["generated"], ISO_Z)
        badge = self.load("api/v1/badge.json")
        self.assertTrue({"schemaVersion", "label", "message"} <= set(badge))
        self.assertTrue((self.root / ".nojekyll").exists())
        for rel in ("api/v1/health.json", "api/v1/version.json", "api/v1/agents.json", "api/v1/soul.json"):
            self.assertTrue(self.load(rel)["schema"].startswith("rapp-static-brainstem-"), rel)

    def test_health_mirrors_the_live_brainstem(self):
        self.built()
        health = self.load("api/v1/health.json")
        self.assertTrue({"status", "version", "model", "soul", "agents", "copilot", "endpoint"} <= set(health))
        self.assertEqual(health["status"], "static")
        self.assertEqual(health["agents"], ["ClockAgent", "HelloAgent"])
        self.assertEqual((self.root / "api/v1/health").read_bytes(), (self.root / "api/v1/health.json").read_bytes())

    def test_rebuild_is_byte_identical(self):
        self.built()
        before = tree(self.root)
        again = self.built()
        self.assertEqual(before, tree(self.root))
        self.assertIn("0 file(s) changed", again.stdout)

    def test_skill_and_llms_entry_points(self):
        self.built()
        skill = (self.root / "SKILL.md").read_text()
        self.assertTrue(skill.startswith('---\nname: test-brainstem\ndescription: "'))
        soul = (self.root / "src/soul.md").read_text().rstrip("\n")
        self.assertIn(f"<!-- soul:start -->\n{soul}\n<!-- soul:end -->", skill)
        self.assertIn("`Hello` (HelloAgent, sha8 `", skill)
        self.assertIn(f"({RAW}SKILL.md)", (self.root / "llms.txt").read_text())

    # -------------------------------------------------------------- raw_base "auto"

    def test_raw_base_auto_uses_the_github_repository(self):
        self.manifest(raw_base="auto", pages_base="auto")
        self.built(env={"GITHUB_REPOSITORY": "Example/brainstem"}, drop=("GITHUB_WORKSPACE",))
        reg = self.load("registry.json")
        self.assertEqual(reg["raw_base"], "https://raw.githubusercontent.com/Example/brainstem/main/")
        self.assertEqual(reg["pages_base"], "https://example.github.io/brainstem/")
        self.built("--repo", "other/place", env={"GITHUB_REPOSITORY": "Example/brainstem"}, drop=("GITHUB_WORKSPACE",))
        self.assertEqual(self.load("registry.json")["raw_base"], "https://raw.githubusercontent.com/other/place/main/")

    @unittest.skipUnless(shutil.which("git"), "git isn't installed")
    def test_raw_base_auto_reads_the_origin_remote_and_subfolder(self):
        repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/brainstem.git"],
                       check=True, capture_output=True)
        target = repo / "apis" / "brainstem"
        shutil.copytree(self.root, target)
        data = json.loads((target / "manifest.json").read_text())
        data["raw_base"] = "auto"
        (target / "manifest.json").write_text(json.dumps(data))
        result = run(target / "build.py", drop=UNLINKED)
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = json.loads((target / "registry.json").read_text())["raw_base"]
        self.assertEqual(raw, "https://raw.githubusercontent.com/example/brainstem/main/apis/brainstem/")

    def test_raw_base_auto_explains_what_to_do_when_unlinked(self):
        self.manifest(raw_base="auto")
        result = self.build(drop=UNLINKED)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--repo owner/name", result.stderr)
        self.assertFalse((self.root / "registry.json").exists())
        self.assertFalse((self.root / "versions").exists())

    # -------------------------------------------------------------- content store

    def test_change_appends_a_version_and_keeps_the_old_one(self):
        self.built()
        old = self.entry("agents/hello_agent.py")
        self.edit_hello()
        self.built()
        new = self.entry("agents/hello_agent.py")
        self.assertNotEqual(old["sha8"], new["sha8"])
        self.assertTrue((self.root / old["path"]).exists(), "old version must stay in the store")
        self.assertEqual([h["sha8"] for h in new["history"]], [old["sha8"], new["sha8"]])

    def test_removed_agent_is_retired_not_deleted(self):
        self.built()
        clock = self.entry("agents/clock_agent.py")
        self.manifest(agents=["src/agents/hello_agent.py"])
        self.built()
        reg = self.load("registry.json")
        self.assertEqual([r["name"] for r in reg["retired"]], ["agents/clock_agent.py"])
        self.assertTrue((self.root / clock["path"]).exists())
        self.assertEqual([t["function"]["name"] for t in self.load("api/v1/agents.json")["tools"]], ["Hello"])

    def test_store_is_append_only(self):
        self.built()
        blob = self.root / self.entry("agents/hello_agent.py")["path"]
        blob.write_text(blob.read_text() + "# edited\n")
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("append-only", result.stderr)

    def test_missing_published_version_is_an_error(self):
        self.built()
        old = self.entry("agents/hello_agent.py")
        self.edit_hello()
        self.built()
        (self.root / old["path"]).unlink()
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing", result.stderr)

    # -------------------------------------------------------------- fetched sources, drift, fallback

    def test_url_source_is_captured_and_falls_back_when_unreachable(self):
        server = self.serve(self.upstream("remote_agent.py", cls="RemoteAgent", tool="Remote"))
        self.manifest(agents=["src/agents/hello_agent.py", {"url": server.url + "remote_agent.py"}])
        self.built()
        remote = self.entry("agents/remote_agent.py")
        self.assertTrue(remote["fresh"])
        self.assertEqual(remote["sources"][0]["url"], server.url + "remote_agent.py")
        server.close()
        self.servers.remove(server)
        result = self.built()
        again = self.entry("agents/remote_agent.py")
        self.assertFalse(again["fresh"])
        self.assertEqual(again["sha8"], remote["sha8"])
        self.assertEqual(self.load("api/v1/status.json")["status"], "degraded")
        self.assertIn("last published version", result.stdout)
        code, reply = self.call("Remote")
        self.assertEqual((code, reply["result"]), (0, "up"))

    def test_unreachable_url_with_no_history_is_left_out(self):
        self.manifest(agents=["src/agents/hello_agent.py", {"url": "http://127.0.0.1:9/never_agent.py"}])
        self.built()
        status = self.load("api/v1/status.json")
        self.assertEqual(status["status"], "degraded")
        self.assertTrue(any("never_agent.py" in e for e in status["errors"]))
        self.assertEqual(self.load("api/v1/health.json")["agents"], ["HelloAgent"])

    def test_drift_is_observed_by_default_and_enforced_on_request(self):
        server = self.serve(self.upstream("hello_agent.py", cls="HelloAgent", tool="Hello"))
        item = {"path": "src/agents/hello_agent.py", "url": server.url + "hello_agent.py"}
        self.manifest(agents=[item])
        self.built()
        hello = self.entry("agents/hello_agent.py")
        self.assertTrue(hello["drift"])
        self.assertEqual(self.load("api/v1/status.json")["status"], "drift")
        self.assertEqual(len(hello["history"]), 2, "both the repo and upstream versions are captured")
        self.manifest(agents=[dict(item, policy="enforce")])
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("enforce", result.stderr)

    # -------------------------------------------------------------- refusals

    def test_refuses_secret_like_values_without_echoing_them(self):
        token = "ghp_" + "A1b2C3d4" * 5
        (self.root / "src/agents/leaky_agent.py").write_text(
            AGENT.format(cls="LeakyAgent", tool="Leaky", desc="x", body=f"return '{token}'"))
        self.manifest(agents=["src/agents/hello_agent.py", "src/agents/leaky_agent.py"])
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("leaky_agent.py", result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertFalse((self.root / "versions/agents/leaky_agent.py").exists())

    def test_refuses_agents_it_cannot_read_safely(self):
        cases = {
            "double_agent.py": (AGENT.format(cls="OneAgent", tool="One", desc="x", body="return 1") + "\n\n" +
                                AGENT.format(cls="TwoAgent", tool="Two", desc="x", body="return 2"), "exactly one class"),
            "dynamic_agent.py": ("from basic_agent import BasicAgent\n\n\nclass DynamicAgent(BasicAgent):\n"
                                 "    def __init__(self):\n        self.metadata = make()\n\n"
                                 "    def perform(self, **kwargs):\n        return ''\n", "literal dict"),
            "twin_agent.py": (AGENT.format(cls="TwinAgent", tool="Hello", desc="x", body="return 1"), "unique"),
        }
        for filename, (source, message) in cases.items():
            with self.subTest(filename):
                (self.root / "src/agents" / filename).write_text(source)
                self.manifest(agents=["src/agents/hello_agent.py", f"src/agents/{filename}"])
                result = self.build()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_refuses_paths_outside_the_folder(self):
        self.manifest(agents=["../outside_agent.py"])
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inside this folder", result.stderr)

    # -------------------------------------------------------------- runner

    def test_runner_verifies_then_runs(self):
        self.built()
        code, reply = self.call("Hello", '{"name": "Kody"}')
        self.assertEqual(code, 0, reply)
        self.assertTrue(reply["sha256_verified"])
        self.assertIn("Kody", reply["result"])
        code, reply = self.call("ClockAgent", '{"utc_offset_hours": -4}')
        self.assertEqual(code, 0, reply)
        self.assertIn("UTC-0400", reply["result"])

    def test_runner_reads_over_http_like_github_raw(self):
        self.built()
        server = self.serve(self.root)
        runner = self.tmp / "elsewhere" / "run.py"
        runner.parent.mkdir()
        shutil.copy(self.root / "run.py", runner)
        result = run(runner, "--base", server.url, "call", "hello", '{"name": "raw"}')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("raw", json.loads(result.stdout)["result"])
        self.assertEqual(json.loads(run(runner, "--base", server.url, "health").stdout)["status"], "static")

    def test_runner_pins_an_earlier_version(self):
        self.built()
        old = self.entry("agents/hello_agent.py")["sha8"]
        self.edit_hello()
        self.built()
        self.assertIn("Hi again", self.call("Hello", '{"name": "x"}')[1]["result"])
        code, reply = self.call("Hello", '{"name": "x"}', "--pin", old)
        self.assertEqual(code, 0, reply)
        self.assertEqual((reply["sha8"], reply["result"].split(",")[0]), (old, "Hello"))

    def test_runner_refuses_a_tampered_version(self):
        self.built()
        blob = self.root / self.entry("agents/hello_agent.py")["path"]
        blob.write_text(blob.read_text() + "# tampered\n")
        code, reply = self.call("Hello", '{"name": "x"}')
        self.assertNotEqual(code, 0)
        self.assertIn("SHA-256 mismatch", reply["error"])

    def test_runner_reports_bad_calls_plainly(self):
        (self.root / "src/agents/needs_agent.py").write_text(
            AGENT.format(cls="NeedsAgent", tool="Needs", desc="x", body="import not_a_real_package_xyz\n        return ''"))
        self.manifest(agents=["src/agents/hello_agent.py", "src/agents/needs_agent.py"])
        self.built()
        self.assertIn("name", self.call("Hello", "{}")[1]["error"])
        self.assertIn("not_a_real_package_xyz", self.call("Needs")[1]["error"])
        self.assertIn("available", self.call("Nope")[1])

    # -------------------------------------------------------------- skill install

    def test_skill_install_runs_and_keeps_foreign_files(self):
        skills = self.tmp / "skills"
        skills.mkdir()
        self.built("--install-skill", skills)
        copy = skills / "test-brainstem"
        self.assertTrue((copy / "SKILL.md").exists() and (copy / "run.py").exists())
        code, reply = self.call("Hello", '{"name": "copy"}', runner=copy / "run.py")
        self.assertEqual(code, 0, reply)
        old = self.entry("agents/hello_agent.py")["sha8"]
        (copy / "notes.txt").write_text("the host added this")
        self.edit_hello()
        self.manifest(agents=["src/agents/hello_agent.py"])
        self.built("--install-skill", skills)
        self.assertTrue((copy / "notes.txt").exists())
        self.assertFalse((copy / "versions/agents/clock_agent.py").exists())
        self.assertIn("Hi again", self.call("Hello", '{"name": "x"}', runner=copy / "run.py")[1]["result"])
        code, reply = self.call("Hello", '{"name": "x"}', "--pin", old, runner=copy / "run.py")
        self.assertNotEqual(code, 0)
        self.assertIn(f"--base {RAW}", reply["error"])

    def test_skill_install_shortcuts(self):
        home = self.tmp / "home"
        cowork = home / "OneDrive - Contoso" / "Documents" / "Cowork" / "skills"
        cowork.mkdir(parents=True)
        env = {"HOME": str(home), "USERPROFILE": str(home)}
        drop = ("OneDrive", "OneDriveCommercial")
        self.built("--install-skill", "copilot", env=env, drop=drop)
        self.assertTrue((home / ".copilot" / "skills" / "test-brainstem" / "SKILL.md").exists())
        self.built("--install-skill", "cowork", env=env, drop=drop)
        self.assertTrue((cowork / "test-brainstem" / "SKILL.md").exists())

    def test_skill_install_refuses_an_unrelated_folder(self):
        skills = self.tmp / "skills"
        (skills / "test-brainstem").mkdir(parents=True)
        (skills / "test-brainstem" / "SKILL.md").write_text("someone else's skill")
        result = self.build("--install-skill", skills)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((skills / "test-brainstem" / "SKILL.md").read_text(), "someone else's skill")


if __name__ == "__main__":
    unittest.main()
