# rapp-static-brainstem

Your RAPP brainstem as a static API. The soul, single-file agents, and tool schemas are served from GitHub raw, so any AI that can read one link can act as your brainstem. There's no server and no API key.

[Requirements (PRD)](PRD.md) · [rapp-static-api/1.0 spec](https://github.com/kody-w/rapp-static-apis/blob/main/SPEC.md) · [MIT license](LICENSE)

## Quick start

1. Select **Use this template** to create your own public repository.
2. Replace `src/soul.md`, put your agents in `src/agents/`, and list them in `manifest.json`.
3. Push. The workflow tests, builds, and commits the API. If it doesn't start, run **build** once from the **Actions** tab.
4. Give any AI your link:
   ```
   Read https://raw.githubusercontent.com/<you>/<repo>/main/SKILL.md and act as my brainstem.
   ```

To start with an empty history instead of the template's demo versions, delete `registry.json`, `api/`, and `versions/` before your first push.

For the dashboard, turn on GitHub Pages (deploy from the `main` branch, root folder) and set `"pages_base": "auto"` in `manifest.json`.

## Hosts that can't fetch links

Install the same files as a skill:

```
python3 build.py --install-skill cowork      # OneDrive > Documents/Cowork/skills/<skill_name>
python3 build.py --install-skill copilot     # ~/.copilot/skills/<skill_name>
python3 build.py --install-skill <folder>    # any skills folder
```

Run it again after each change. The copy doesn't update itself.

## Commands

```
python3 build.py                             # build (in the workflow, the repo is detected automatically)
python3 build.py --repo <owner>/<repo>       # build locally before the repo has a GitHub remote
python3 -m unittest discover -s tests -v     # offline tests
python3 run.py health                        # like GET /health
python3 run.py tools                         # agents and their arguments
python3 run.py call Hello '{"name": "me"}'   # check the hash, then run the agent
python3 run.py call Hello '{"name": "me"}' --pin <sha8>   # run an exact earlier version
```

On Windows, pass `-` instead of the JSON to read the arguments from standard input.

## Endpoints

All paths are relative to `https://raw.githubusercontent.com/<owner>/<repo>/main/`.

| Path | What it is |
|---|---|
| `SKILL.md` | Start here: the soul, the agents, and how to run them |
| `registry.json` | The index: every entry's hash, sources, and version history |
| `api/v1/agents.json` | OpenAI-style `tools` array, plus the hash and raw URL of each agent |
| `api/v1/soul.json` | The soul and its hash |
| `api/v1/health`, `api/v1/version` (and `.json`) | Mirrors of the live brainstem's `GET /health` and `GET /version` |
| `api/v1/status.json`, `api/v1/badge.json` | Build status and a shields.io badge |
| `versions/<name>/<sha8><ext>` | Every published version, never changed or deleted |
| `run.py` | The runner |

`POST /chat` isn't served. The AI that reads `SKILL.md` runs the turn.

## Agents

Each agent follows the brainstem's single-file contract: one `BasicAgent` subclass, one `metadata`, one `perform()`. Static publishing adds one rule: write `metadata` as a literal dict. It can use `self.name` and module constants. The build reads agents without running them.

You can also list agents by URL. The workflow fetches them again every day:

```json
"agents": [
  "src/agents/hello_agent.py",
  {"url": "https://raw.githubusercontent.com/<owner>/<repo>/main/agents/some_agent.py"},
  {"path": "src/agents/my_agent.py", "url": "https://example.com/my_agent.py", "policy": "enforce"}
]
```

If an entry has both a path and a URL, the path is served and the URL is watched. A difference is recorded as drift, or fails the build with `"policy": "enforce"`. If a URL is down, the last published version is served.

The build stops, and names the file, when:

- a file doesn't have exactly one agent class with `perform()`
- `metadata` is built by code
- two agents share a name
- anything looks like a credential (the value is never printed)
- a path leaves the repository folder
- a published version was changed or deleted

## Trust

- The runner checks every agent's SHA-256 before running it. That proves the bytes match the index, not who wrote them.
- Running an agent runs its Python on your machine. Only run brainstems you trust, and use a commit SHA in the URL for reads that can't change.
- A public repository is world-readable. Keep secrets, private memory, and customer data out.
- This is non-authoritative discovery. Signed RAPP/1 frames and registries remain the authority.

## Troubleshooting

- **The workflow can't push:** check Settings → Actions → General → Workflow permissions, and any branch protection on `main`.
- **Your default branch isn't `main`:** set `"ref"` in `manifest.json`, and change the branch in `.github/workflows/build.yml`.
- **A change hasn't shown up yet:** GitHub raw caches files for a few minutes.

## License

MIT. See [LICENSE](LICENSE).
