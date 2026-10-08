# Guides

This directory contains **operational guides** — how to set up, develop, and
troubleshoot APME.

## Contents

| Document | Description |
|----------|-------------|
| [CLI.md](CLI.md) | CLI installation, commands, daemon mode, CI usage, limitations |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Podman pod, bootc VM, Helm chart — setup and configuration |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Local setup, tox environments, adding rules, testing |
| [RULE_CONFIGURATION.md](RULE_CONFIGURATION.md) | Rule configuration, suppression, dependency scan options |
| [PLUGIN_SIDECARS.md](PLUGIN_SIDECARS.md) | Build plugin images and add OPA / ansible-security-scanner containers to the Podman pod |
| [PODMAN_OPA_ISSUES.md](PODMAN_OPA_ISSUES.md) | Podman rootless troubleshooting for OPA |

## When to Add a Document Here

Add a guide when you need step-by-step instructions for a workflow,
environment setup, or troubleshooting procedure. Guides are task-oriented
("how do I ...") rather than explanatory ("why does it ...").
