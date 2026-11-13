# Security

This server gives an AI agent control of a simulator on your machine. `run_python` runs arbitrary Python, the file tools
touch the filesystem, and opening a USD file trusts it. It trusts the local user and is **not a sandbox**: see
[Handle with care](AGENTS.md#handle-with-care) and [one-time approval](docs/TOOLS-SCENE.md#one-time-approval). Keep the bridge on
its default loopback address, and don't point a client at a scene or project you don't trust.

To report a security problem, use the repository's **Security** tab, "Report a vulnerability", rather than a public issue.
Please say which commit and Isaac Sim build you used, and how to reproduce it.

Crash dumps stay on your machine by default: `run.sh` and Isaac Lab jobs turn off Kit's upload to NVIDIA. See
[`TWINSMITH_CRASH_UPLOAD`](docs/ENVIRONMENT.md).
