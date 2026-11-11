# Changelog

## 0.2.0

- Rewritten. Requests carry an `id` that the reply echoes.
- Connections present a token (`ISAAC_MCP_AUTH`, `ISAAC_MCP_TOKEN`); the bridge writes it to a
  private file beside its portfile for clients on the same account.
- Read-only mode (`ISAAC_MCP_READ_ONLY=1`): only tools registered `read_only` run.
- Scheduling is three rules: inline tools answer at once on the socket thread, exclusive tools
  run alone, the rest run in arrival order. `status` and `job_status` are inline, so a wait on a
  job costs no simulator time.
- Every job writes a record, `<jobs folder>/<job_id>/job.json`, in the Isaac Lab job format.
- Removed: holding the bridge for one caller, queue budgets, call telemetry.

## 0.1.0

- First version.
