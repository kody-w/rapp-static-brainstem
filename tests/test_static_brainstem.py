"""Offline tests for rapp-static-brainstem.

They build their own fixture brainstem (tests/fixtures/brainstem), so they pass whatever you put in src/.
Local HTTP servers stand in for GitHub raw and for upstream agents.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import functools
import hashlib
import zipfile
import io
import base64
import http.server
import json
import os
import platform
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

# Calls the runner in-process, so the test can see the caller's working folder before and after.
DRIVER = '''import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("runner", sys.argv[1])
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
before = os.getcwd()
reply = runner.call(sys.argv[2], "Where", "{}")
print(json.dumps({"before": before, "after": os.getcwd(), "reply": reply}))
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

    def test_skill_description_uses_this_brainstems_name_and_no_colliding_triggers(self):
        def description():
            skill = (self.root / "SKILL.md").read_text()
            return json.loads(re.search(r'^description: (".*")$', skill, re.M).group(1))

        self.built()
        self.assertTrue(description().startswith("Test Brainstem: Test’s static, read-only RAPP brainstem"),
                        description())
        self.manifest(title="Other Title", owner="")
        self.built()
        text = description()
        self.assertTrue(text.startswith("Other Title: a static, read-only RAPP brainstem"), text)
        for phrase in ("use my static brainstem", "run an agent from my static brainstem",
                       "what agents are in my static brainstem"):
            self.assertIn(f"“{phrase}”", text)
        page = (self.root / "index.html").read_text().lower()
        for clash in ("global brainstem", "ask my brainstem"):  # other brainstem skills answer to these
            self.assertNotIn(clash, text.lower())
            self.assertNotIn(clash, page)

    def test_llms_links_every_entry_point(self):
        for pages in (None, "https://example.github.io/brainstem/"):
            with self.subTest(pages_base=pages):
                self.manifest(pages_base=pages)
                self.built()
                reg = self.load("registry.json")
                llms = (self.root / "llms.txt").read_text()
                for key, rel in reg["endpoints"].items():
                    if key == "llms":
                        continue
                    if key == "dashboard":  # a dashboard link needs a Pages URL; raw would show its source
                        if pages:
                            self.assertIn(f"]({pages}{rel})", llms)
                        else:
                            self.assertNotIn(rel, llms)
                    else:
                        self.assertIn(f"]({RAW}{rel})", llms, key)

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

    def test_runner_runs_the_agent_in_a_temporary_folder(self):
        (self.root / "src/agents/where_agent.py").write_text(AGENT.format(
            cls="WhereAgent", tool="Where", desc="x",
            body="import os\n        open('relative-write.txt', 'w').close()\n        return os.getcwd()"))
        self.manifest(agents=["src/agents/hello_agent.py", "src/agents/where_agent.py"])
        self.built()
        caller = self.tmp / "caller"
        caller.mkdir()
        (self.tmp / "driver.py").write_text(DRIVER)
        result = subprocess.run([sys.executable, str(self.tmp / "driver.py"), str(self.root / "run.py"),
                                 str(self.root)], cwd=caller, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        seen = json.loads(result.stdout)
        self.assertTrue(seen["reply"]["ok"], seen["reply"])
        ran_in = Path(seen["reply"]["result"])
        self.assertTrue(ran_in.name.startswith("static-brainstem-"), ran_in)
        self.assertEqual(ran_in.parent.resolve(), Path(tempfile.gettempdir()).resolve())
        self.assertFalse(ran_in.exists(), "the temporary folder is removed afterwards")
        self.assertEqual(Path(seen["before"]).resolve(), caller.resolve())
        self.assertEqual(seen["after"], seen["before"], "the caller's working folder is restored")
        self.assertEqual(list(caller.iterdir()), [], "a relative write stays out of the caller's folder")

    def test_probe_proves_it_ran_and_reveals_nothing_else(self):
        self.manifest(agents=["src/agents/hello_agent.py", "src/agents/probe_agent.py"])
        self.built()
        code, reply = self.call("Probe", json.dumps({"nonce": "abc"}))
        self.assertEqual(code, 0, reply)
        probe = json.loads(reply["result"])
        self.assertEqual(list(probe), ["nonce_sha256", "python", "os", "ran_in_temp_folder"])
        self.assertEqual(probe["nonce_sha256"], hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(probe["python"], platform.python_version())
        self.assertEqual(probe["os"], sys.platform)
        self.assertIs(probe["ran_in_temp_folder"], True)
        self.assertNotIn(str(Path.home()), reply["result"], "no user folder in the proof")
        self.assertNotIn(os.getcwd(), reply["result"], "no caller folder in the proof")
        source = (self.root / "src/agents/probe_agent.py").read_text()
        self.assertNotIn("urllib", source, "the probe uses no network")

    def test_compare_documents_finds_every_change_in_word_files(self):
        self.manifest(agents=["src/agents/hello_agent.py", "src/agents/compare_documents_agent.py"])
        self.built()

        def docx(name, paragraphs):
            body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
            path = self.tmp / name
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml",
                                 '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                 f"<w:body>{body}</w:body></w:document>")
            return path

        same = ["Terms", "Payment is due in 30 days.", "Notice is 90 days.", "Governed by local law."]
        old = docx("old.docx", same + ["Planned work is agreed in advance."])
        new = docx("new.docx", ["Terms", "Late fees apply after 15 days.", "Payment is due in 15 days.",
                                "Notice is 90 days.", "Governed by local law."])
        code, reply = self.call("CompareDocuments", json.dumps({"old_path": str(old), "new_path": str(new)}))
        self.assertEqual(code, 0, reply)
        self.assertTrue(reply["sha256_verified"])
        result = json.loads(reply["result"])
        self.assertEqual(result["counts"], {"added": 1, "removed": 1, "changed": 1, "unchanged": 3})
        self.assertEqual([c["type"] for c in result["changes"]], ["added", "changed", "removed"])
        self.assertEqual(result["changes"][1]["words"], [{"from": "30", "to": "15"}])

        code, reply = self.call("CompareDocuments", json.dumps({"old_path": str(old), "new_path": str(old)}))
        self.assertTrue(json.loads(reply["result"])["identical"])
        code, reply = self.call("CompareDocuments", json.dumps({"old_path": str(old), "new_path": "missing.docx"}))
        self.assertIn("no file at", reply["result"])
        source = (self.root / "src/agents/compare_documents_agent.py").read_text()
        self.assertNotIn("urllib", source, "it reads local files only")

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

    # -------------------------------------------------------------- skill install stays in its folder (SEC-9)

    def install(self):
        skills = self.tmp / "skills"
        skills.mkdir(exist_ok=True)
        self.built("--install-skill", skills)
        return skills, skills / "test-brainstem"

    def sentinel(self) -> Path:
        path = self.tmp / "outside" / "sentinel.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text("not the skill's")
        return path

    def link(self, link: Path, to: Path):
        try:
            link.symlink_to(to, target_is_directory=to.is_dir())
        except (OSError, NotImplementedError):
            self.skipTest("this system can't make symbolic links")

    def reinstall_with_forged_marker(self, skills, copy, *entries):
        marker = copy / ".mirror.json"
        info = json.loads(marker.read_text())
        info["files"] += list(entries)
        marker.write_text(json.dumps(info))
        return self.build("--install-skill", skills)

    def assert_untouched(self, sentinel, result):
        self.assertTrue(sentinel.exists(), "a file outside the skill folder was deleted")
        self.assertEqual(sentinel.read_text(), "not the skill's")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_skill_install_ignores_an_absolute_marker_entry(self):
        sentinel = self.sentinel()
        skills, copy = self.install()
        self.assert_untouched(sentinel, self.reinstall_with_forged_marker(skills, copy, str(sentinel)))

    def test_skill_install_ignores_a_parent_marker_entry(self):
        sentinel = self.sentinel()
        skills, copy = self.install()
        self.assert_untouched(sentinel, self.reinstall_with_forged_marker(skills, copy, "../../outside/sentinel.txt"))

    def test_skill_install_ignores_a_marker_entry_through_a_link(self):
        sentinel = self.sentinel()
        skills, copy = self.install()
        self.link(copy / "linked", sentinel.parent)
        result = self.reinstall_with_forged_marker(skills, copy, "linked/sentinel.txt")
        self.assert_untouched(sentinel, result)
        self.assertTrue((copy / "linked").is_symlink(), "a link the host made is left alone")

    def test_skill_install_keeps_empty_folders_it_did_not_empty(self):
        skills, copy = self.install()
        (copy / "host-folder").mkdir()
        self.built("--install-skill", skills)
        self.assertTrue((copy / "host-folder").is_dir())

    def test_skill_install_refuses_to_write_through_a_link(self):
        sentinel = self.sentinel()
        skills, copy = self.install()
        (copy / "SKILL.md").unlink()
        self.link(copy / "SKILL.md", sentinel)
        result = self.build("--install-skill", skills)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("link", result.stderr)
        self.assertEqual(sentinel.read_text(), "not the skill's")

    def test_skill_install_removes_only_files_it_wrote_unchanged(self):
        skills = self.tmp / "skills"
        skills.mkdir()
        self.built()
        run(self.root / "build.py", "--install-skill", str(skills), drop=UNLINKED)
        target = skills / "test-brainstem"
        marker = json.loads((target / ".mirror.json").read_text())
        (target / "notes.txt").write_text("the host's own note")
        marker["files"].append("notes.txt")
        marker.setdefault("sha256", {})["notes.txt"] = "0" * 64
        (target / ".mirror.json").write_text(json.dumps(marker))
        result = run(self.root / "build.py", "--install-skill", str(skills), drop=UNLINKED)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((target / "notes.txt").read_text(), "the host's own note",
                         "a listed file whose bytes don't match what the install wrote is kept")

    def test_skill_install_never_writes_through_a_hard_link(self):
        skills = self.tmp / "skills"
        skills.mkdir()
        self.built()
        run(self.root / "build.py", "--install-skill", str(skills), drop=UNLINKED)
        target = skills / "test-brainstem"
        outside = self.tmp / "outside-run.py"
        (target / "run.py").unlink()
        outside.write_text("the host's own file")
        os.link(outside, target / "run.py")
        result = run(self.root / "build.py", "--install-skill", str(skills), drop=UNLINKED)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outside.read_text(), "the host's own file", "the other name for the file is untouched")
        self.assertEqual((target / "run.py").read_bytes(), (self.root / "run.py").read_bytes().replace(b"\r\n", b"\n"))

    def test_skill_lists_the_files_an_offline_host_needs_and_they_are_enough(self):
        self.manifest(agents=["src/agents/hello_agent.py", "src/agents/probe_agent.py"])
        self.built()
        skill = (self.root / "SKILL.md").read_text()
        raw_base = json.loads((self.root / "registry.json").read_text())["raw_base"]
        self.assertIn("can't reach the internet", skill)
        offline = self.tmp / "offline"
        offline.mkdir()
        for rel in ("run.py", "api/v1/agents.json"):
            self.assertIn(raw_base + rel, skill)
        agents = json.loads((self.root / "api/v1/agents.json").read_text())["agents"]
        for a in agents:
            self.assertIn(raw_base + a["path"], skill, a["tool"])
        needed = ["run.py", "api/v1/agents.json", next(a["path"] for a in agents if a["tool"] == "Probe")]
        for rel in needed:  # what a host's web tool would save, nothing more
            (offline / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.root / rel, offline / rel)
        result = run(offline / "run.py", "call", "Probe", '{"nonce": "offline"}')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        reply = json.loads(result.stdout)
        self.assertTrue(reply["ok"] and reply["sha256_verified"], reply)
        self.assertEqual(json.loads(reply["result"])["nonce_sha256"], hashlib.sha256(b"offline").hexdigest())

    def test_skill_install_refuses_a_folder_where_a_file_belongs(self):
        skills = self.tmp / "skills"
        skills.mkdir()
        self.built()
        run(self.root / "build.py", "--install-skill", str(skills), drop=UNLINKED)
        target = skills / "test-brainstem"
        (target / "run.py").unlink()
        (target / "run.py").mkdir()
        result = run(self.root / "build.py", "--install-skill", str(skills), drop=UNLINKED)
        self.assertEqual(result.returncode, 1)
        self.assertIn("is a folder", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(sorted(p.name for p in target.iterdir() if p.name.endswith(".tmp")), [])

    def test_skill_gives_the_runner_hash_an_offline_host_checks(self):
        self.built()
        skill = (self.root / "SKILL.md").read_text()
        self.assertIn(hashlib.sha256((self.root / "run.py").read_bytes().replace(b"\r\n", b"\n")).hexdigest(), skill)
        self.assertNotIn("<", skill.split("---")[1], "no angle brackets in the front matter")

    # -------------------------------------------------------------- one-file bundle

    def unpack_command(self, bundle_text: str) -> str:
        found = re.search(r"```bash\n(python3 - SKILL.md <<'PY'.*?\nPY)\n```", bundle_text, re.S)
        self.assertIsNotNone(found, "the bundle shows its own unpack command")
        return found.group(1)

    @unittest.skipUnless(shutil.which("bash"), "the documented command uses a bash heredoc")
    def test_bundle_unpacks_with_its_own_command_and_runs(self):
        self.manifest(agents=["src/agents/hello_agent.py", "src/agents/probe_agent.py"])
        self.built()
        bundle = (self.root / "bundle/SKILL.md").read_text()
        self.assertTrue(bundle.startswith("---\nname: test-brainstem\n"))
        host = self.tmp / "host"
        host.mkdir()
        (host / "SKILL.md").write_text(bundle)  # the only file the host was given
        result = subprocess.run(["bash", "-c", self.unpack_command(bundle)], cwd=host, capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        unpacked = host / "test-brainstem"
        skill = self.tmp / "skills"
        skill.mkdir()
        run(self.root / "build.py", "--install-skill", str(skill), drop=UNLINKED)
        expected = {k: v for k, v in tree(skill / "test-brainstem").items() if k != ".mirror.json"}
        self.assertEqual(tree(unpacked), expected, "the bundle carries exactly the skill copy")
        reply = run(unpacked / "run.py", "call", "Probe", '{"nonce": "one-file"}')
        self.assertEqual(reply.returncode, 0, reply.stdout + reply.stderr)
        data = json.loads(reply.stdout)
        self.assertTrue(data["ok"] and data["sha256_verified"], data)
        self.assertEqual(json.loads(data["result"])["nonce_sha256"], hashlib.sha256(b"one-file").hexdigest())

    @unittest.skipUnless(shutil.which("bash"), "the documented command uses a bash heredoc")
    def test_bundle_refuses_a_changed_payload(self):
        self.built()
        bundle = (self.root / "bundle/SKILL.md").read_text()
        start = re.search(r"<!-- payload:start sha256=[0-9a-f]{64} -->", bundle).start()  # the real one, not the command's
        line = bundle.index("\n", start) + 1
        changed = bundle[:line] + ("B" if bundle[line] != "B" else "C") + bundle[line + 1:]
        host = self.tmp / "host"
        host.mkdir()
        (host / "SKILL.md").write_text(changed)
        result = subprocess.run(["bash", "-c", self.unpack_command(bundle)], cwd=host, capture_output=True, text=True,
                                timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertFalse((host / "test-brainstem").exists(), "nothing is unpacked from a changed file")

    def test_bundle_is_byte_for_byte_reproducible(self):
        self.built()
        first = (self.root / "bundle/SKILL.md").read_bytes()
        for path in self.root.rglob("*"):
            if path.is_file():
                os.utime(path, (1_000_000_000, 1_000_000_000))  # file dates must not leak into the zip
        result = run(self.root / "build.py", drop=UNLINKED, env={"GITHUB_REPOSITORY": "example/test-brainstem"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "bundle/SKILL.md").read_bytes(), first)
        payload = re.search(r"<!-- payload:start sha256=([0-9a-f]{64}) -->(.*?)<!-- payload:end -->",
                            first.decode("utf-8"), re.S)
        data = base64.b64decode(re.sub(r"\s+", "", payload.group(2)))
        self.assertEqual(hashlib.sha256(data).hexdigest(), payload.group(1))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            self.assertEqual([i.filename for i in infos], sorted(i.filename for i in infos))
            for info in infos:
                self.assertEqual((info.date_time, info.compress_type), ((2020, 1, 1, 0, 0, 0), zipfile.ZIP_STORED))

    def test_skill_and_llms_point_to_the_bundle(self):
        self.built()
        raw_base = json.loads((self.root / "registry.json").read_text())["raw_base"]
        for name in ("SKILL.md", "llms.txt"):
            self.assertIn(raw_base + "bundle/SKILL.md", (self.root / name).read_text(), name)

    def test_an_oversized_bundle_is_skipped_and_the_rest_still_publishes(self):
        filler = "x" * 40_000
        names = []
        for n in range(12):  # about 480 KB of agents: the skill copy fits the per-file limit, the bundle can't
            name = f"src/agents/big{n}_agent.py"
            (self.root / name).write_text(AGENT.format(cls=f"Big{n}Agent", tool=f"Big{n}", desc="big",
                                                       body=f"return '{filler}'"))
            names.append(name)
        self.manifest(agents=["src/agents/hello_agent.py", *names])
        result = self.built()
        self.assertFalse((self.root / "bundle/SKILL.md").exists())
        status = self.load("api/v1/status.json")
        self.assertTrue(any("bundle/SKILL.md skipped" in n for n in status["notes"]), status["notes"])
        self.assertNotIn("bundle", self.load("registry.json")["endpoints"])
        for name in ("SKILL.md", "llms.txt"):
            self.assertNotIn("bundle/SKILL.md", (self.root / name).read_text(), name)
        self.manifest(agents=["src/agents/hello_agent.py"])  # back under the limit: the bundle returns
        self.built()
        self.assertTrue((self.root / "bundle/SKILL.md").exists())
        self.assertIn("bundle", self.load("registry.json")["endpoints"])

    @unittest.skipUnless(shutil.which("bash"), "the documented command uses a bash heredoc")
    def test_bundle_follows_an_edited_agent(self):
        self.built()
        hello = self.root / "src/agents/hello_agent.py"
        hello.write_text(hello.read_text().replace("Hello, ", "Hi, "))
        self.built()
        bundle = (self.root / "bundle/SKILL.md").read_text()
        host = self.tmp / "host"
        host.mkdir()
        (host / "SKILL.md").write_text(bundle)
        result = subprocess.run(["bash", "-c", self.unpack_command(bundle)], cwd=host, capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = json.loads(run(host / "test-brainstem" / "run.py", "call", "Hello", '{"name": "x"}').stdout)
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(reply["result"].startswith("Hi, x"), reply)

    def test_published_files_get_the_usual_permissions(self):
        self.built()
        umask = os.umask(0o022)
        os.umask(umask)
        for rel in ("SKILL.md", "registry.json", "bundle/SKILL.md", "api/v1/health"):
            self.assertEqual((self.root / rel).stat().st_mode & 0o777, 0o666 & ~umask, rel)

    @unittest.skipUnless(shutil.which("bash"), "the documented command uses a bash heredoc")
    def test_unpack_refuses_unsafe_paths_and_an_existing_folder(self):
        self.built()
        bundle = (self.root / "bundle/SKILL.md").read_text()
        command = self.unpack_command(bundle)
        crafted = io.BytesIO()
        with zipfile.ZipFile(crafted, "w") as z:
            z.writestr("../escaped.txt", "should never be written")
        data = crafted.getvalue()
        payload = base64.b64encode(data).decode("ascii")
        hostile = re.sub(r"<!-- payload:start sha256=[0-9a-f]{64} -->.*?<!-- payload:end -->",
                         lambda _: f"<!-- payload:start sha256={hashlib.sha256(data).hexdigest()} -->\n{payload}\n"
                                   "<!-- payload:end -->", bundle, flags=re.S)
        host = self.tmp / "host"
        host.mkdir()
        (host / "SKILL.md").write_text(hostile)
        result = subprocess.run(["bash", "-c", command], cwd=host, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe path", result.stderr)
        self.assertFalse((self.tmp / "escaped.txt").exists())
        (host / "SKILL.md").write_text(bundle)
        (host / "test-brainstem").mkdir()
        (host / "test-brainstem" / "mine.txt").write_text("the host's own file")
        result = subprocess.run(["bash", "-c", command], cwd=host, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertEqual((host / "test-brainstem" / "mine.txt").read_text(), "the host's own file")


if __name__ == "__main__":
    unittest.main()
