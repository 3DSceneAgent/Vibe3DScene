# Roadmap

This document summarizes the most promising next steps for Vibe3DScene, along with a few concrete bugs that still need cleanup.

## Potential Next Steps

1. Refine the agentic workflow, including the experimental dual-agent setup.
   Improve planning reliability, execution handoff, and verification behavior across both single-agent and dual-agent paths.

2. Make resend behavior smarter after a user submits another message.
   Instead of always replacing the current todo state, the runtime should decide whether to continue the existing plan, merge into the current todos, or override outdated todo terms.

3. Stream the live 3D viewport back to the backend.
   The frontend user's current viewpoint could be reused as a camera prior, which would give the agent better situational awareness during scene editing and verification.

4. Integrate more generation modules into a larger end-to-end system.
   This includes text-to-3D and image-to-3D capabilities, so the project can orchestrate more of the full 3D creation pipeline instead of only scene editing and assembly.

5. Improve support for the local client workflow.
   A stronger packaging story could make it easier to install the system as a local server and integrate it directly with Blender in a more turnkey way.

## Known Issues

1. In fullscreen mode, the 3D viewport `Settings` button cannot be clicked.

2. While the agent is replying, especially during streamed scene construction, the current camera viewpoint cannot be reused reliably.
