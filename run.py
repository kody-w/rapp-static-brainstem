#!/usr/bin/env python3
"""rapp-static-brainstem runner: a rapp-static-api/1.0 client that verifies before it executes.

    python3 run.py health                               mirrors GET /health
    python3 run.py tools                                agents, tool names, descriptions, arguments
    python3 run.py call NAME '{"k": "v"}'               verify the current version's SHA-256, then run perform()
    python3 run.py call NAME '{"k": "v"}' --pin SHA8    run an exact earlier version from the content store
    python3 run.py call NAME -                          read the JSON arguments from standard input

--base defaults to the folder that holds this file. It can also be the raw base URL, e.g.
https://raw.githubusercontent.com/<owner>/<repo>/main/

Standard library only. Prints one JSON object. Installs nothing; the agent runs from a temp folder.
Hashes are SHA-256 over the bytes with CRLF replaced by LF, as the build computes them.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import posixpath
import sys
import tempfile
import types
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TIMEOUT_SECONDS = 20


class RunnerError(Exception):
    pass


def is_url(base: str) -> bool:
    return base.startswith(("https://", "http://"))


def read(base: str, rel: str) -> bytes:
    if is_url(base):
        url = base.rstrip("/") + "/" + rel
        request = urllib.request.Request(url, headers={"User-Agent": "rapp-static-brainstem-runner/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise RunnerError(f"couldn't read {url}: {exc}") from None
    path = Path(base).expanduser() / rel
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RunnerError(f"couldn't read {path}: {exc.strerror or exc}") from None


def read_json(base: str, rel: str):
    try:
        return json.loads(read(base, rel).decode("utf-8"))
    except ValueError as exc:
        raise RunnerError(f"{rel} isn't valid JSON: {exc}") from None


class BasicAgent:
    """Stand-in for the brainstem's base class: name, metadata, perform()."""

    def __init__(self, name=None, metadata=None, *args, **kwargs):
        if name is not None:
            self.name = name
        if metadata is not None:
            self.metadata = metadata

    def perform(self, **kwargs):
        raise NotImplementedError("this agent doesn't implement perform()")


def install_base_class() -> None:
    """Make both `from basic_agent import BasicAgent` and `from agents.basic_agent import BasicAgent` work."""
    module = types.ModuleType("basic_agent")
    module.BasicAgent = BasicAgent
    package = types.ModuleType("agents")
    package.__path__ = []
    package.basic_agent = module
    sys.modules["basic_agent"] = module
    sys.modules["agents"] = package
    sys.modules["agents.basic_agent"] = module


def function_for(registry: dict, tool: str) -> dict:
    for item in registry.get("tools", []):
        fn = item.get("function") or {}
        if fn.get("name") == tool:
            return fn
    return {}


def find(registry: dict, name: str):
    key = name.strip().lower()
    for entry in registry.get("agents", []):
        if key in (entry["class"].lower(), entry["tool"].lower()):
            return entry
    return None


def health(base: str) -> dict:
    return read_json(base, "api/v1/health.json")


def tools(base: str) -> dict:
    registry = read_json(base, "api/v1/agents.json")
    listing = []
    for entry in registry.get("agents", []):
        fn = function_for(registry, entry["tool"])
        params = fn.get("parameters") or {}
        listing.append({
            "agent": entry["class"], "tool": entry["tool"], "sha8": entry["sha8"],
            "description": fn.get("description", ""),
            "arguments": sorted((params.get("properties") or {}).keys()),
            "required": params.get("required") or [],
        })
    return {"ok": True, "agents": listing}


def resolve_pin(base: str, entry: dict, pin: str):
    """Find an exact stored version in registry.json history. Returns (path, sha256, raw_base)."""
    registry = read_json(base, "registry.json")
    for item in registry.get("entries", []) + registry.get("retired", []):
        if item.get("name") != entry["name"]:
            continue
        for version in item.get("history", []):
            if version.get("sha8") == pin.lower():
                ext = posixpath.splitext(entry["name"])[1]
                return f"versions/{entry['name']}/{version['sha8']}{ext}", version["sha256"], registry.get("raw_base")
    raise RunnerError(f"{entry['name']} has no stored version {pin}")


def call(base: str, name: str, raw_args: str, pin: str | None = None) -> dict:
    registry = read_json(base, "api/v1/agents.json")
    entry = find(registry, name)
    if entry is None:
        return {"ok": False, "error": f"no agent named {name!r}",
                "available": [e["tool"] for e in registry.get("agents", [])]}
    try:
        args = json.loads(raw_args) if raw_args.strip() else {}
    except ValueError as exc:
        return {"ok": False, "agent": entry["class"], "error": f"arguments must be a JSON object ({exc})"}
    if not isinstance(args, dict):
        return {"ok": False, "agent": entry["class"], "error": "arguments must be a JSON object"}
    params = function_for(registry, entry["tool"]).get("parameters") or {}
    missing = [key for key in params.get("required") or [] if key not in args]
    if missing:
        return {"ok": False, "agent": entry["class"], "error": f"missing required argument(s): {', '.join(missing)}"}

    if pin:
        path, expected, raw_base = resolve_pin(base, entry, pin)
    else:
        path, expected, raw_base = entry["path"], entry["sha256"], None
    try:
        source = read(base, path).replace(b"\r\n", b"\n")
    except RunnerError:
        if pin and raw_base and not is_url(base):
            return {"ok": False, "agent": entry["class"],
                    "error": f"version {pin} isn't in this copy. Add --base {raw_base} to fetch it."}
        raise
    digest = hashlib.sha256(source).hexdigest()
    if digest != expected:
        return {"ok": False, "agent": entry["class"], "error": "SHA-256 mismatch, refusing to run",
                "expected": expected, "actual": digest}

    install_base_class()
    printed = io.StringIO()
    with tempfile.TemporaryDirectory(prefix="static-brainstem-") as tmp:
        file = Path(tmp) / "agent.py"
        file.write_bytes(source)
        spec = importlib.util.spec_from_file_location(f"static_agent_{uuid.uuid4().hex}", file)
        module = importlib.util.module_from_spec(spec)
        try:
            caller = os.getcwd()
        except OSError:  # the caller's folder is gone; there is nothing to return to
            caller = None
        os.chdir(tmp)  # the agent runs, and writes relative paths, in the temporary folder
        try:
            with contextlib.redirect_stdout(printed):
                spec.loader.exec_module(module)
                agent = getattr(module, entry["class"])()
                result = agent.perform(**args)
        except ModuleNotFoundError as exc:
            return {"ok": False, "agent": entry["class"],
                    "error": f"needs the module {exc.name!r}, which isn't available here (this runner installs nothing)"}
        except SystemExit as exc:
            return {"ok": False, "agent": entry["class"], "error": f"agent exited early (code {exc.code})"}
        except Exception as exc:  # the agent's own failure, reported rather than raised
            return {"ok": False, "agent": entry["class"], "error": f"{type(exc).__name__}: {exc}"}
        finally:
            if caller is not None:
                with contextlib.suppress(OSError):
                    os.chdir(caller)

    if not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False, default=str)
    reply = {"ok": True, "agent": entry["class"], "tool": entry["tool"], "sha8": digest[:12],
             "sha256_verified": True, "result": result}
    logs = printed.getvalue().strip()
    if logs:
        reply["agent_logs"] = logs[-4000:]
    return reply


def main(argv=None) -> int:
    top = argparse.ArgumentParser(add_help=False)
    top.add_argument("--base", dest="base_top", help="folder or raw URL of the API (default: this file's folder)")
    each = argparse.ArgumentParser(add_help=False)
    each.add_argument("--base", dest="base_sub", help=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(description="Run a static RAPP brainstem.", parents=[top])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health", parents=[each], help="mirrors GET /health")
    sub.add_parser("tools", parents=[each], help="list agents and their tools")
    one = sub.add_parser("call", parents=[each], help="verify, then run one agent")
    one.add_argument("name", help="agent class name or tool name")
    one.add_argument("arguments", nargs="?", default="{}", help="JSON object of arguments, or - to read stdin")
    one.add_argument("--pin", metavar="SHA8", help="run this exact stored version instead of the current one")
    args = parser.parse_args(argv)
    base = args.base_sub or args.base_top or str(HERE)

    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.command == "health":
            result = health(base)
        elif args.command == "tools":
            result = tools(base)
        else:
            raw = sys.stdin.read() if args.arguments == "-" else args.arguments
            result = call(base, args.name, raw, args.pin)
    except RunnerError as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
