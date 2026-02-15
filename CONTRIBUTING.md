# Contributing to 3DSceneAgent

Thanks for contributing.

This document explains the expected workflow for code, tests, and pull requests in this repository.

## 1. Development Setup

### Prerequisites
- Python 3.10+
- Node.js 18+ (for `web/`)
- Blender 3.6+
- Docker (optional, for tool servers)

### Backend setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Frontend setup

```bash
cd web
npm install
```

## 2. Project Layout

- `scene_agent/`: backend runtime (agent graph, session management, VLM, API/CLI)
- `mcp_server/`: MCP server runtime and tool registration
- `web/`: React + TypeScript frontend
- `addon/`: Blender addon
- `tool_servers/`: Dockerized external services (TRELLIS2 / retrieval / PCG)
- `tests/`: unit, integration, contract, and manual tests

When making changes, keep concerns separated:
- Agent logic in `scene_agent/agent`
- API surface in `scene_agent/interfaces`
- MCP tool adapters in `mcp_server/tools`

## 3. Branches and Commits

- Keep each branch focused on one logical change.
- Use clear commit subjects, preferably type-led:
  - `feat: ...`
  - `bugfix: ...`
  - `minor: ...`
  - `refactor: ...`

Examples:
- `feat: add thread-level MCP tool hints endpoint`
- `bugfix: avoid stale headless session ownership`

## 4. Coding Guidelines

### Python
- 4-space indentation
- `snake_case` for functions/modules, `PascalCase` for classes
- Follow local style in touched files

### Frontend
- Component files: `PascalCase` (for example `SceneTab.tsx`)
- Utility/state files: `camelCase` (for example `storage.ts`)
- Run lint before opening PR

## 5. Testing Requirements

Run the relevant checks for your change scope.

### Backend tests

```bash
pytest
```

### Integration tests (requires services)

```bash
RUN_INTEGRATION=1 pytest tests/integration
```

### Frontend checks

```bash
cd web
npm run build
npm run lint
```

If your change affects tool-server integration, include any tool-server startup commands you used in the PR notes.

## 6. Pull Request Checklist

Before opening a PR, make sure you include:

1. Purpose and scope of the change.
2. Key implementation notes.
3. Verification commands and results.
4. Linked issue/spec (for example under `specs/...` or `docs/issues/...`).
5. UI screenshots/recordings for `web/` changes.
6. Any required environment flags for reviewers.

## 7. Documentation and API Changes

- Update `README.md` when user-facing setup or behavior changes.
- Update/add docs for new env vars, runtime modes, or tool gates.
- If endpoint behavior changes, update associated specs/contracts when applicable.

## 8. Security and Secrets

- Never commit credentials or private keys.
- Keep secrets in `.env` (local only).
- Treat `execute_blender_code` and remote tool calls as privileged paths; avoid exposing unauthenticated deployments on untrusted networks.

## 9. License

By contributing, you agree that your contributions are licensed under the Apache License 2.0 used by this repository.
