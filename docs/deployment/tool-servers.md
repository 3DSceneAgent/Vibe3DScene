# Tool Servers

Last updated: 2026-03-30

This repository no longer ships the old in-repo `tool_servers/` stack. Optional external services now live in the sibling `../3DAgentTools` checkout.

For shared host settings, service endpoint variables, and related defaults, see the central [Configuration Reference](../reference/configuration.md).

## 1. Why Tool Servers Were Split Out

The external tool stack includes GPU-heavy, service-heavy, and dependency-heavy components that are operationally different from the core agent runtime.

Separating them provides:

- cleaner boundaries between core runtime and optional services
- easier independent deployment and debugging
- less clutter in the main repository
- more flexibility for teams that only need a subset of tools

## 2. Expected Layout

Recommended sibling checkout:

```text
3DSceneAgent/
├── Vibe3DScene/
└── 3DAgentTools/
```

The main repository refers to the external tool stack through:

- the default sibling path `../3DAgentTools`
- optional override via `AGENT_TOOLS_ROOT`

## 3. Clone and Basic Startup

Clone the external tools repository next to this repo:

```bash
git clone --recurse-submodules https://github.com/3DSceneAgent/3DAgentTools ../3DAgentTools
```

Typical startup flow:

```bash
cd ../3DAgentTools
cp .env.example .env
# edit .env for hosts, ports, API keys, and enabled services
./start_tool_servers.sh
```

Typical stop flow:

```bash
cd ../3DAgentTools
./stop_tool_servers.sh
```

## 4. Typical Services

Depending on your configuration, `3DAgentTools` may provide services such as:

- TRELLIS2
- retrieval backend services
- SceneSmith compatibility APIs
- SAM reconstruction service
- PCG / Infinigen service
- supporting databases such as PostgreSQL for retrieval workflows

The exact enabled set depends on your environment configuration and which services you choose to start.

## 5. Startup Modes

The external stack is designed to support both containerized and local shell workflows depending on the service.

Some setups also support local shell management scripts rather than Docker-only execution.

## 6. Host and Port Coordination

The main repository does not assume hardcoded endpoints only. It resolves tool-service endpoints through environment variables.

Common coordination variables:

- shared host
  - `TOOL_SERVICE_HOST`
- generation
  - `TRELLIS2_HOST`
  - `TRELLIS2_PORT`
- retrieval
  - `OBJAVERSE_HOST`
  - `OBJAVERSE_PORT`
  - `SCENESMITH_COMPAT_HOST`
  - `SCENESMITH_COMPAT_PORT`
- reconstruction
  - `SAM_HOST`
  - `SAM_PORT`
- PCG
  - `INFINIGEN_HOST`
  - `INFINIGEN_PORT`

Recommended pattern:

- if services are colocated on one machine, set `TOOL_SERVICE_HOST` once
- override only the services that differ from the shared default

## 7. How the Main Repository Uses Tool Servers

The main repository uses external tool servers indirectly:

- environment variables resolve their endpoints
- the MCP registry decides whether the related tool family is enabled
- health checks may probe service reachability
- tool adapters invoke the service APIs only after gating passes

This means a missing tool server should disable a tool family cleanly rather than breaking the entire agent runtime.

## 8. Minimal Adoption Strategy

You do not need the entire external stack to use the project.

Common deployment patterns:

- core Blender scene agent only
  - no optional retrieval or generation services
- retrieval-focused
  - enable PolyHaven, Objaverse, or SceneSmith flows
- generation-focused
  - enable one generator family such as Rodin, Tripo3D, TRELLIS2, or Hunyuan3D
- full stack
  - combine retrieval, generation, reconstruction, and PCG services

## 9. Related Docs

- [MCP Server and Tools](../integrations/mcp-server-and-tools.md)
- [System Architecture](../architecture/system-architecture.md)
- [Startup and Deployment Guide](./startup-and-deployment.md)
- [Configuration Reference](../reference/configuration.md)
