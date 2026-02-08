# Headless Blender Known Issues

## Symptoms

- `GET /scene/{thread_id}`, `GET /scene/{thread_id}/renders`, and `GET /scene/{thread_id}/gltf` time out in headless mode.
- Local-client mode works.
- Backend logs show socket send succeeded but no response was received.

## Root Cause (Confirmed)

The headless Blender process exits shortly after the addon server starts, so the
socket server stops before any request can be handled. This is caused by the
headless bootstrap script returning immediately (Blender exits when the script
finishes), which unregisters the addon and stops the server.

Evidence from runtime logs:
- Headless client starts and reports addon server started.
- Immediately afterwards, the Blender process quits and the addon is unregistered.

This results in:
- The API layer sending the command successfully.
- The socket receive call blocking until timeout because no response is ever sent.

## Why Local-Client Works

In local-client mode, the addon server is attached to an interactive Blender
session that stays open, so the socket server remains alive to process commands.

## Mitigation / Fix Direction

The headless process must be kept alive explicitly. Viable approaches:

1. Run a blocking headless server that keeps the Blender process alive.
2. Use a long-lived daemon process and reuse the same headless session.
3. Avoid `--background --python` one-shot scripts without a keepalive mechanism.

## Xvfb Consideration

Using Xvfb can be viable if Blender requires a windowed context for certain
operators or modal handlers. However, the primary issue here is process lifetime
management; Xvfb alone does not prevent the headless script from exiting.
If you choose Xvfb, prefer launching Blender without `--background`, and keep
the process alive with a persistent server loop.
