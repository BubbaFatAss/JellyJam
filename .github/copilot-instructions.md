## Quick orientation for AI coding agents

Repository snapshot: at the time of writing this repo contains only a `.git/` directory and a `.gitignore` file. There is no obvious application code, build manifests, CI configs, or docs. Because of that, these instructions focus on how to discover the project's shape, where to look next, and how to behave when files are missing.

Follow these concrete steps when you start working in this workspace:

1. Scan for common project manifests (stop when you find one):
   - Look for `package.json`, `pyproject.toml`, `requirements.txt`, `Pipfile`, `composer.json`, `go.mod`, `pom.xml`, `build.gradle`, `Cargo.toml`, `*.csproj`, `Makefile`, `Dockerfile`, and `.github/workflows/*.yml`.
   - Example PowerShell search (run from repo root):
     - Get-ChildItem -Path . -Recurse -Force -Include package.json,pyproject.toml,requirements.txt,Dockerfile -ErrorAction SilentlyContinue

2. If no manifests are present (current state):
   - Don't assume a language or framework. Ask the repo owner which language(s) are intended.
   - Offer to scaffold a minimal starter based on user's preference (Node/Express, Python/Flask, .NET, etc.) and show the exact files you will add.

3. Architecture & discovery checklist (how to quickly form the "big picture"):
   - Search for entry points: `index.*`, `main.*`, `app.*`, `server.*`, `Program.cs`.
   - Search README-like files and any `docs/` folder.
   - Search for tests: `test/`, `spec/`, `*.test.*`, `*.spec.*` to infer testing framework.
   - Look for config or secrets patterns: `.env`, `config/`, `settings.*`, or references to environment variables in code.

4. When you encounter source files, extract these facts and include them in your first PR message:
   - Primary language(s) and runtime versions (from files or lockfiles).
   - How to build: exact commands to install deps and run the app or test suite.
   - Where to find the main executable or server start file.

5. Project-specific conventions (rules for this repo):
   - At present there are no discoverable conventions. If you add or modify files, create or update `README.md` with build/test commands and a short architecture diagram.
   - Prefer explicit, minimal changes. Add a single, well-scoped scaffold or fix and include a short changelog entry in the PR.

6. CI / integration points to look for (when present):
   - `.github/workflows/*.yml` — contains automated builds and test commands.
   - `Dockerfile`, `docker-compose.yml` — how services are composed and ports exposed.
   - `azure-pipelines.yml`, `.gitlab-ci.yml`, or `circleci/` — alternate CI providers.

7. Error handling for missing information:
   - If runtime or test commands are not discoverable, propose a safe probe plan (install deps in a disposable environment, run tests with verbose output) and present the results before making further changes.
   - Ask targeted questions: "Which language/runtime should I scaffold?", "Do you want a minimal CI pipeline?", "Should I add a README with examples?"

8. Examples of useful, non-generic suggestions you can make in PRs for this repo:
   - "Add a minimal Node.js `package.json` with `start` and `test` scripts and a tiny `src/index.js` that logs 'hello' so CI can be wired." — include exact file contents and commands.
   - "Add a `README.md` with these three commands to run and test the project (install, start, test)."

9. Safety and scope
   - Avoid committing secrets or keys. If you find credentials in the repo, stop and flag them to the owner.
   - Keep changes minimal and reversible; prefer adding files over changing unrecognized code.

If anything here is unclear, or you intended a different branch or subfolder (for example, a monorepo with code in a subdirectory), tell me where the source lives and I'll re-scan and update this guidance to reference actual files and commands.

---
Repository facts discovered: only `.git/` and `.gitignore` are present. Recommend next step: confirm intended language or allow the agent to scaffold a minimal starter. 
