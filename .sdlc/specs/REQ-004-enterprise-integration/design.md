# REQ-004: Design

## Architecture

See [architecture.md](../../context/architecture.md) for system design.
See [design-dashboard.md](../../context/design-dashboard.md) for dashboard design.

## Implementation Approach

CLI implemented as thin client with daemon mode (ADR-024). Web dashboard built with a gateway service and modern frontend, replacing the original Streamlit concept.

## Key Components

- **CLI Application** (argparse) — Thin CLI with daemon mode, implemented
- **Web Gateway** — HTTP/WebSocket gateway service (ADR-029)
- **Frontend** — Project-centric UI with sidebar navigation (ADR-037)
- **AAP Pre-Flight Hook** — Deferred (DR-004)

## Key ADRs

- [ADR-024: Thin CLI Daemon Mode](../../adrs/ADR-024-thin-cli-daemon-mode.md)
- [ADR-029: Web Gateway Architecture](../../adrs/ADR-029-web-gateway-architecture.md)
- [ADR-030: Frontend Deployment Model](../../adrs/ADR-030-frontend-deployment-model.md)
- [ADR-037: Project-Centric UI Model](../../adrs/ADR-037-project-centric-ui-model.md)
