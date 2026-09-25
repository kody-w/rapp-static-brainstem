# rapp-static-brainstem: Product Requirements

| | |
|---|---|
| Status | Draft 1.0, ready to build |
| Owner | Kody Wildfeuer |
| Last updated | 2026-09-24 |
| Conforms to | [rapp-static-api/1.0](https://github.com/kody-w/rapp-static-apis/blob/main/SPEC.md) |
| Reference implementation | This repository: `build.py`, `run.py`, `index.html`, `tests/` |
| License | MIT |

## 1. Summary

rapp-static-brainstem publishes a RAPP brainstem, meaning its soul and its single-file agents, as a static, read-only API on GitHub raw. Any AI that can read one link can act as that brainstem. Hosts that can't fetch links install the same files as a skill. There's no server, no API key, and no hosting bill.

It ships as a GitHub template. Create a repository from it, replace the soul and agents, and push. The workflow tests, builds, and publishes the API.

## 2. Problem

- A brainstem runs on its owner's machine (`http://localhost:7071`). Cloud assistants, phones, and sandboxes can't reach it.
- Hosting it as a service costs money, needs authentication, and needs someone to keep it running.
- Many AI hosts can read a web page or a skills folder but can't call a server. Some can't fetch arbitrary URLs at all.
- So an owner's soul and agents stay stuck on one machine.

## 3. Goals and non-goals

| ID | Goal |
|---|---|
| G1 | Publish the soul, agents, and tool schemas as static files served from `raw.githubusercontent.com`. |
| G2 | One link is enough: `<raw_base>SKILL.md` gives an AI everything it needs to act as the brainstem. |
| G3 | The same files install as a skill for hosts without web access. |
| G4 | Conform to rapp-static-api/1.0: one build step, stable writes, an append-only store, and versioned endpoints. |
| G5 | Verify before running. Every artifact is hashed, and the runner refuses mismatches. |
| G6 | No setup for template users: no server, no secrets, no URLs to edit. |

Not in 1.0:

- Serving `POST /chat`, or running anything on a server. The reading AI runs the turn.
- Private brainstems. A public repository is world-readable.
- Proving authorship. Signed RAPP/1 frames and registries remain the authority.
- Sandboxing agents. Running an agent runs the publisher's Python.
- Replacing the local brainstem. This is a read-only mirror of one.

## 4. Users

| User | Needs |
|---|---|
| Owner | Publish, update, and roll back a brainstem without running a server |
| Reading AI | One entry point with the soul, the tools, and how to run them |
| Client developer | Stable JSON endpoints shaped like the live brainstem's |
| Auditor | Every version, hash, and source, kept permanently |

## 5. User stories

| ID | Story | Accepted when |
|---|---|---|
| US-1 | As an owner, I publish by pushing. | After I edit `src/`, list my agents in `manifest.json`, and push, the workflow commits the API and `<raw_base>SKILL.md` lists my agents. |
| US-2 | As an owner, I use my brainstem from any AI. | An AI given the `SKILL.md` link can read the soul, list the tools, and run an agent with `run.py`. |
| US-3 | As an owner on a host without web access, I install it locally. | `python3 build.py --install-skill cowork` (or `copilot`, or a folder) writes a working skill copy. |
| US-4 | As a consumer, I only run what was published. | `run.py` refuses an agent whose bytes don't match the published SHA-256. |
| US-5 | As an owner, I can roll back. | `run.py call <agent> --pin <sha8>` runs any stored version. |
| US-6 | As an owner, I can publish an agent that lives elsewhere. | An agent listed by URL is re-captured daily. Drift is recorded, or fails the build under `enforce`. An outage serves the last published version. |
| US-7 | As a client developer, I reuse code written for the live brainstem. | `GET <raw_base>api/v1/health` returns the live `/health` keys with `status: "static"`. |

## 6. Functional requirements

### 6.1 Input: `manifest.json` (FR-1)

This is the only hand-authored file, apart from the soul and agents it lists.

| Field | Default | Meaning |
|---|---|---|
| `name` | required | API name, used in `registry.json` |
| `soul` | required | The soul, as a path or an entry object (below) |
| `agents` | `[]` | The agents, as paths or entry objects |
| `title` | `name` | Display title |
| `description` | `""` | One-line description |
| `owner` | `""` | Shown in the skill description |
| `raw_base` | `"auto"` | `"auto"`, or the https URL of this folder on GitHub raw |
| `ref` | `"main"` | Branch, tag, or commit used when `raw_base` is `"auto"` |
| `pages_base` | `null` | `null`, `"auto"`, or the dashboard's https URL |
| `skill_name` | `name` | Skill folder name: lowercase letters, digits, and hyphens |
| `model` | `"gpt-4o"` | Echoed in health, for shape compatibility |
| `version` | `"unknown"` | Echoed in health and version |

Entries:

| Form | Behavior |
|---|---|
| `"src/agents/x_agent.py"` | A file in this repository |
| `{"url": "https://…"}` | A file elsewhere, fetched again on every build |
| `{"path": "…", "url": "…"}` | Serve the repository file and watch the URL for drift |
| `"name": "agents/y_agent.py"` | Optional published name. The default is `soul.md` or `agents/<file name>` |
| `"policy": "observe"` or `"enforce"` | Optional: what drift does. The default is `observe` |

- FR-1.1 Agent names end in `_agent.py` and aren't `basic_agent.py`.
- FR-1.2 Paths stay inside the repository folder. Anything outside must be listed by URL.
- FR-1.3 Each published name appears once.

### 6.2 Build step (FR-2)

- FR-2.1 One command, `python3 build.py`, regenerates every generated file, using only the Python standard library.
- FR-2.2 The build is idempotent and stable-write. A rebuild with no changes writes nothing. If only `generated` would change, the old timestamp is kept.
- FR-2.3 When `raw_base` is `"auto"`, the build takes the repository from `--repo`, then `GITHUB_REPOSITORY`, then the git `origin` remote. It then appends this folder's path inside the repository, so the build also works from a subfolder. If no repository is found, the build stops and says how to fix it. `pages_base: "auto"` derives the GitHub Pages URL the same way.
- FR-2.4 Every refusal exits with a non-zero code and a message that names the file and line. It never prints a secret value.
- FR-2.5 Hashes are SHA-256 over UTF-8 bytes with CRLF replaced by LF (`sha256-lf-v1`). `sha8` is the first 12 hex characters.
- FR-2.6 Published files are UTF-8 text of 512 KB or less.

### 6.3 Agents (FR-3)

- FR-3.1 Each agent follows the single-file contract: one class that subclasses `BasicAgent`, a `perform()` method, and `metadata` in OpenAI function-calling schema.
- FR-3.2 The build reads agents with Python's `ast` module. It never imports or runs them.
- FR-3.3 `metadata` must be plain data: literals, module constants, `self.name`, simple f-strings, and `+`. Anything else is refused, with the reason.
- FR-3.4 Tool names and class names are unique, ignoring case.
- FR-3.5 Both `from basic_agent import BasicAgent` and `from agents.basic_agent import BasicAgent` work at run time.

### 6.4 Content store (FR-4)

- FR-4.1 Every captured version is written once, to `versions/<name>/<sha8><ext>`.
- FR-4.2 Stored versions are never changed or deleted. The build stops if a version was edited, if a file's name doesn't match its hash, or if a recorded version is missing.
- FR-4.3 An entry removed from the manifest moves to `retired`, with its history intact.

### 6.5 Sources, drift, and fallback (FR-5)

- FR-5.1 A path in the repository is authoritative. A missing path is an authoring error.
- FR-5.2 A URL source is fetched on every build, with a 20-second timeout. If it's unreachable, invalid, or looks like it contains a secret, the last published version is served and status becomes `degraded`. If nothing was ever published, the entry is left out and listed under `errors`.
- FR-5.3 When an entry has both a path and a URL, the path is served. A different upstream hash is drift. Under `observe` the status becomes `drift` and the build passes; under `enforce` the build fails.
- FR-5.4 Every captured version is stored, including an upstream version that differs, so either one can be pinned.

### 6.6 Generated documents (FR-6)

- FR-6.1 Every generated JSON document carries `schema: "<name>/<major>.<minor>"`. The shields.io badge is the exception and uses shields' own format. Timestamps are ISO-8601 UTC ending in `Z`.
- FR-6.2 `registry.json` names its `raw_base`. Every entry lists its current hash, its sources with their URLs, and its full history.
- FR-6.3 `SKILL.md` contains:
  - skill front matter
  - a safety preamble
  - the soul verbatim, between `<!-- soul:start -->` and `<!-- soul:end -->`
  - each agent with its arguments and sha8
  - how to run and pin agents
  - the endpoints and the limits
- FR-6.4 `api/v1/health` and `api/v1/version` are also written without the `.json` extension, so a client that calls `GET {base}health` works unchanged.
- FR-6.5 `llms.txt` links every entry point.

### 6.7 Runner: `run.py` (FR-7)

- FR-7.1 Commands are `health`, `tools`, and `call NAME [JSON] [--pin SHA8]`. `--base` takes a folder or URL and defaults to the runner's own folder. Passing `-` as the JSON reads the arguments from standard input.
- FR-7.2 Before running an agent, it checks the agent's SHA-256 against `api/v1/agents.json`, or against `registry.json` history for `--pin`. It refuses a mismatch.
- FR-7.3 It checks that the arguments are a JSON object with every required parameter.
- FR-7.4 It runs `perform(**args)` from a temporary folder with a `BasicAgent` stand-in. It installs nothing, and it captures printed output as `agent_logs`.
- FR-7.5 It prints exactly one JSON object and exits with 0 on success or 1 on failure. Missing modules, exceptions, and early exits are reported plainly.
- FR-7.6 A skill copy holds only current versions. If a pinned version isn't in the copy, the error gives the `--base` to use.

### 6.8 Skill install (FR-8)

- FR-8.1 `--install-skill cowork|copilot|DIR` copies the skill view into `<skills folder>/<skill_name>/`. The skill view is `SKILL.md`, `run.py`, `registry.json`, `api/v1/*`, and the current versions.
- FR-8.2 `cowork` finds the OneDrive `Documents/Cowork/skills` folder. `copilot` uses `~/.copilot/skills`.
- FR-8.3 It removes only files that an earlier install wrote, which it tracks in `.mirror.json`. It refuses a non-empty folder it didn't create.

### 6.9 Dashboard: `index.html` (FR-9)

- FR-9.1 A single file with no dependencies. It loads `registry.json`, recomputes every SHA-256 in the browser, and shows the status, agents, soul, and a copyable "act as my brainstem" prompt.
- FR-9.2 It's served from GitHub Pages. It adapts to narrow screens, follows dark mode, and escapes all registry text.

### 6.10 CI (FR-10)

- FR-10.1 The workflow runs on every push to `main`, daily, and on demand. It runs the tests on Python 3.10 and the latest 3.x, then the build, and commits only if something changed.
- FR-10.2 The tests build their own fixture brainstem, so they pass whatever the owner puts in `src/`. The build step checks the owner's content.

## 7. API contract (v1)

`raw_base` is `https://raw.githubusercontent.com/<owner>/<repo>/<ref>/`, plus the folder path when the API lives in a subfolder. Every path below is relative to it.

| Path | Schema | Contents |
|---|---|---|
| `SKILL.md` | none | The AI entry point |
| `llms.txt` | none | Links for AIs and crawlers |
| `registry.json` | `rapp-static-brainstem/1.0` | The index |
| `api/v1/status.json` | `rapp-static-brainstem-status/1.0` | `ok`, `drift`, or `degraded`, with counts, drift, stale, errors, and notes |
| `api/v1/badge.json` | shields.io endpoint | `brainstem`: number of agents |
| `api/v1/health`, `health.json` | `rapp-static-brainstem-health/1.0` | `status: "static"`, `version`, `model`, `soul`, `agents`, `copilot`, `endpoint` |
| `api/v1/version`, `version.json` | `rapp-static-brainstem-version/1.0` | `version` |
| `api/v1/agents.json` | `rapp-static-brainstem-agents/1.0` | `tools[]` in OpenAI function schema. `agents[]` with class, tool, name, sha8, sha256, bytes, path, and raw |
| `api/v1/soul.json` | `rapp-static-brainstem-soul/1.0` | The soul's text, with its sha8, sha256, path, and raw URL |
| `versions/<name>/<sha8><ext>` | none | Stored versions, never changed |
| `run.py` | none | The runner |
| `index.html` | none | The dashboard, through GitHub Pages |

A registry entry, taken from a real build of the demo agent:

```json
{
  "name": "agents/hello_agent.py",
  "kind": "agent",
  "class": "HelloAgent",
  "tool": "Hello",
  "description": "Greet someone by name. A tiny agent that proves the static brainstem can run tools.",
  "sha8": "70fde91fecdb",
  "sha256": "70fde91fecdb5c56ac61ca52752d1100808a97ad8f3f85a6d92ccb472d045d38",
  "bytes": 792,
  "path": "versions/agents/hello_agent.py/70fde91fecdb.py",
  "raw": "<raw_base>versions/agents/hello_agent.py/70fde91fecdb.py",
  "policy": "observe",
  "fresh": true,
  "drift": false,
  "sources": [
    {"label": "repo", "path": "src/agents/hello_agent.py", "url": "<raw_base>src/agents/hello_agent.py", "sha8": "70fde91fecdb"}
  ],
  "history": [
    {"sha8": "70fde91fecdb", "sha256": "70fde91fecdb5c56ac61ca52752d1100808a97ad8f3f85a6d92ccb472d045d38", "bytes": 792}
  ]
}
```

Status values:

- `ok`: every entry is fresh, with no drift.
- `drift`: an upstream copy differs under `observe`.
- `degraded`: an entry is stale or missing.

Changes within v1 only add fields. A breaking change goes to `api/v2/`, and `api/v1/` stays published.

### 7.1 Conformance to rapp-static-api/1.0

| Spec rule | How it's met |
|---|---|
| Served only from static raw or Pages URLs | Everything is a file. Nothing runs on a server. |
| One build step turns one hand-authored input into a schema-tagged index | `build.py` turns `manifest.json` into `registry.json` |
| Idempotent and stable-write | FR-2.2, covered by tests |
| `.nojekyll` for Pages | Included |
| Versioned endpoints under `api/v<major>/` | `api/v1/` |
| Content-addressed with a 12-hex sha8, append-only | `versions/<name>/<sha8><ext>`, FR-4 |
| Index fetchable over raw, naming its raw base | `registry.json` includes `raw_base` |
| Status endpoint `<name>-status/<major>.<minor>`, optional badge | `rapp-static-brainstem-status/1.0` and `badge.json` |
| Observe or enforce, chosen per entry | FR-5.3 |

## 8. How a reading AI uses it

1. Read `SKILL.md`. It holds the soul and the agent list.
2. Answer as the soul describes, within the AI's own rules.
3. When an agent fits, run `python3 run.py call <agent> '<json>'` and use the result, for up to 3 tool rounds per turn, as the brainstem does.
4. Say which agent ran. If an agent can't run (no Python, no network, a missing package), say so. Never claim a run that didn't happen.

Whether a host follows these steps, and whether it can run Python, is up to the host.

## 9. Security and trust

| ID | Requirement |
|---|---|
| SEC-1 | The build reads only the files `manifest.json` lists. It refuses content that looks like a credential and never prints the value. That covers GitHub, OpenAI-style, AWS, and Slack tokens, private keys, Azure storage keys, SAS signatures, and hard-coded passwords. |
| SEC-2 | Repository paths can't leave the repository folder. |
| SEC-3 | Agents are never run at build time. |
| SEC-4 | The runner checks SHA-256 before running an agent, installs nothing, and runs from a temporary folder. |
| SEC-5 | `SKILL.md` says the soul and agent output are the owner's data, not instructions that outrank the host or the user. It also says the brainstem grants no permission to send, publish, delete, or contact anyone. |
| SEC-6 | A hash proves the bytes match the index, not who wrote them. The registry calls itself non-authoritative; signed RAPP/1 frames and registries remain the authority. |
| SEC-7 | Running an agent runs the publisher's Python with the caller's permissions. Only run brainstems you trust. For reads that can't change, use a commit SHA in the URL. |
| SEC-8 | Public means world-readable. Keep secrets, private memory, and customer data out. |
| SEC-9 | Skill install never deletes files it didn't write. |

## 10. Other requirements

- Portability: Python 3.10 or later, standard library only, on Linux, macOS, and Windows. Line endings are normalized, so hashes match on every operating system.
- Determinism: the same inputs produce the same bytes.
- Freshness: GitHub raw caches for a few minutes. The index shows the latest build. Use a commit SHA in the URL for reads that can't change.
- Accessibility: the dashboard works with a keyboard, adapts to narrow screens, and follows the system's dark mode.

## 11. Success measures (targets)

- A new owner has a working `SKILL.md` link within 10 minutes of creating a repository from the template.
- Every published entry checks out in the dashboard and the runner.
- Scheduled builds commit nothing on days when nothing changed.
- The tests pass on every supported Python version.

## 12. Test plan

Run `python3 -m unittest discover -s tests -v`. The tests run offline, with local HTTP servers standing in for GitHub raw and for upstream agents.

| Area | Tests |
|---|---|
| Conformance (FR-2, FR-6) | `test_conformance_shape`, `test_health_mirrors_the_live_brainstem`, `test_rebuild_is_byte_identical`, `test_skill_and_llms_entry_points` |
| `raw_base` auto (FR-2.3) | `test_raw_base_auto_uses_the_github_repository`, `test_raw_base_auto_reads_the_origin_remote_and_subfolder`, `test_raw_base_auto_explains_what_to_do_when_unlinked` |
| Content store (FR-4) | `test_change_appends_a_version_and_keeps_the_old_one`, `test_removed_agent_is_retired_not_deleted`, `test_store_is_append_only`, `test_missing_published_version_is_an_error` |
| Sources and drift (FR-5) | `test_url_source_is_captured_and_falls_back_when_unreachable`, `test_unreachable_url_with_no_history_is_left_out`, `test_drift_is_observed_by_default_and_enforced_on_request` |
| Refusals (FR-1, FR-3, SEC-1, SEC-2) | `test_refuses_secret_like_values_without_echoing_them`, `test_refuses_agents_it_cannot_read_safely`, `test_refuses_paths_outside_the_folder` |
| Runner (FR-7) | `test_runner_verifies_then_runs`, `test_runner_reads_over_http_like_github_raw`, `test_runner_pins_an_earlier_version`, `test_runner_refuses_a_tampered_version`, `test_runner_reports_bad_calls_plainly` |
| Skill install (FR-8) | `test_skill_install_runs_and_keeps_foreign_files`, `test_skill_install_shortcuts`, `test_skill_install_refuses_an_unrelated_folder` |

Not automated: the dashboard in a real browser, the workflow on GitHub, and runs on macOS and Windows. Check them by hand after the first push.

## 13. Release plan

Version 1.0 (this repository) covers FR-1 through FR-10.

1. Create the public repository and push. The first workflow run builds and commits the API.
2. Mark it as a template: Settings → General → Template repository.
3. Optional: turn on GitHub Pages (deploy from the `main` branch, root folder) and set `pages_base` to `"auto"`.
4. Check the release. `SKILL.md` loads from raw, the dashboard shows every hash matching, and a second workflow run commits nothing.

Later, not in 1.0:

- Pull the soul and agents straight from a local brainstem install.
- Publish entries as signed RAPP/1 frames.
- List it in the rapp-static-apis index of indexes.
- A browser-only runner, for hosts without Python.
- Private brainstems, for example through RAPP/1 sealed eggs.

## 14. Open questions

- Final name and home: `rapp-static-brainstem` under `kody-w`?
- License: MIT is proposed, to match rapp-static-apis.
- Should the template ship the two demo agents, or start empty?
- How should it be listed in the rapp-static-apis root registry?

## 15. Build it with an AI coding agent

Paste this into an AI coding agent, in a clone of this repository or in an empty folder that holds only this file:

```text
Build (or verify) the rapp-static-brainstem repository described in PRD.md.
1. Read PRD.md completely. It's the contract. Don't add anything it doesn't ask for.
2. If build.py, run.py, index.html and tests/ exist, run: python3 -m unittest discover -s tests -v
   Fix only what breaks a PRD requirement. If they don't exist, implement them with the
   Python standard library (3.10 or later), along with the tests listed in PRD section 12.
3. Run python3 build.py --repo <owner>/<repo> twice. Show that the second run reports 0 files changed.
4. Run python3 run.py call Hello '{"name": "me"}' and show the JSON it prints.
5. Report every requirement ID (FR- and SEC-) as pass or fail, with the evidence.
   Treat a failing check as a finding: stop and show it. Don't work around it.
```

## Appendix A: Repository layout

```
manifest.json            the input (edited by hand)
src/soul.md              the soul
src/agents/*_agent.py    the agents
build.py                 the only build step
run.py                   the runner, also published in the API
index.html               the dashboard
tests/                   offline tests and their fixture brainstem
.github/workflows/       test, build, and commit only real changes
registry.json, api/v1/, versions/, SKILL.md, llms.txt    generated and committed by the workflow
```

## Appendix B: Glossary

| Term | Meaning |
|---|---|
| Brainstem | A RAPP agent server: a soul plus tools the model can call |
| Soul | The brainstem's system prompt, `soul.md` |
| Agent | One `*_agent.py` file: one `BasicAgent` class, one `metadata`, one `perform()` |
| `raw_base` | The GitHub raw URL of this folder. Every path in the API is relative to it |
| sha8 | The first 12 hex characters of an artifact's SHA-256 |
| Drift | An upstream copy that differs from the version being served |
| Stale | An entry served from its last published version because its source couldn't be read |
