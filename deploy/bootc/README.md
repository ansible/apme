# APME bootc VM Deployment

Deploy APME as an atomic, image-based Linux VM using
[bootc](https://containers.github.io/bootc/). The VM ships Podman and quadlet definitions for all APME services. Container images are pulled from the registry on first boot. Systemd quadlet files manage automatic startup and lifecycle management.

## Architecture

The bootc image builds on **RHEL 10 image mode** (`rhel10/rhel-bootc`, ADR-061) and includes:

- **Podman** for container runtime
- **Quadlet files** that define each APME service as a systemd-managed container
- **Persistent storage** under `/var/lib/apme/` for sessions, Gateway DB, and
  proxy cache
- **Firewall rules** for ports 8080 (Gateway REST), 8081 (UI), and 50051
  (Primary gRPC)

All containers run in a single Podman pod (`apme-pod`), matching the reference
topology from `containers/podman/pod.yaml` and preserving ADR-005 localhost
networking.

## Prerequisites

- A host capable of building OCI images (`podman build`)
- **`podman login registry.redhat.io`** for production bootc builds (RHEL 10
  bootc base requires Red Hat registry authentication)
- `bootc-image-builder` for converting to disk images (qcow2, raw, AMI)
- Target hypervisor or cloud for deploying the disk image

Contributors without RHEL registry access can build with the CentOS Stream 10
bootc fallback:

```bash
podman build -f deploy/bootc/Containerfile \
  --build-arg BOOTC_BASE_IMAGE=quay.io/centos-bootc/centos-bootc:stream10 \
  -t apme-bootc:latest .
```

APME **application** container images use UBI10 (`ubi10/python-312-minimal`,
`ubi10/nodejs-22-minimal`, `ubi10/nginx-126`). The bootc image is the **host
OS** layer and uses `rhel-bootc`, not a UBI application base.

## Build

Build the bootc OCI image from the repository root:

```bash
podman build -f deploy/bootc/Containerfile -t apme-bootc:latest .
```

## Convert to Disk Image

Use `bootc-image-builder` to produce a deployable disk image:

```bash
# qcow2 for KVM/libvirt/OpenStack
sudo podman run --rm -it --privileged \
  --pull=newer \
  -v ./output:/output \
  -v /var/lib/containers/storage:/var/lib/containers/storage \
  quay.io/centos-bootc/bootc-image-builder:latest \
  --type qcow2 \
  --local \
  apme-bootc:latest

# raw for bare metal or Azure
sudo podman run --rm -it --privileged \
  --pull=newer \
  -v ./output:/output \
  -v /var/lib/containers/storage:/var/lib/containers/storage \
  quay.io/centos-bootc/bootc-image-builder:latest \
  --type raw \
  --local \
  apme-bootc:latest

# AMI for AWS
sudo podman run --rm -it --privileged \
  --pull=newer \
  -v ./output:/output \
  -v /var/lib/containers/storage:/var/lib/containers/storage \
  quay.io/centos-bootc/bootc-image-builder:latest \
  --type ami \
  --local \
  apme-bootc:latest
```

The disk image will be written to `./output/`.

## Deploy

### Fresh Install

Boot the VM from the generated disk image. APME starts automatically on boot
via the quadlet-generated systemd units.

### Upgrade an Existing VM

If the VM was originally deployed from a bootc image:

```bash
sudo bootc switch --transport containers-storage apme-bootc:latest
sudo systemctl reboot
```

This performs an atomic upgrade — if the new image fails to boot, the system
automatically rolls back to the previous version.

## Configuration

Edit `/etc/apme/env/apme.env` on the running VM to configure API keys and
settings:

```bash
# Example: enable AI provider
sudo vi /etc/apme/env/apme.env
# Set OPENROUTER_API_KEY, APME_AI_MODEL, etc.
sudo systemctl restart apme-pod.service
```

### Container Images

The shipped quadlet files use fixed image references (e.g.
`ghcr.io/ansible/apme-primary:latest`). To use different images or tags,
edit the quadlet files under `/usr/share/containers/systemd/` and run
`systemctl daemon-reload && systemctl restart apme-pod.service`.

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `APME_AI_MODEL` | AI model for Abbenay (empty = disabled) |
| `OPENROUTER_API_KEY` | API key for OpenRouter |
| `VERTEX_ANTHROPIC_API_KEY` | API key for Vertex AI |
| `APME_FEEDBACK_ENABLED` | Enable Gateway feedback feature |
| `APME_FEEDBACK_GITHUB_REPO` | GitHub repo for feedback issues |
| `APME_FEEDBACK_GITHUB_TOKEN` | GitHub token for feedback |

## Service Management

The quadlet files generate systemd units. Standard systemd commands work:

```bash
# Check pod status
systemctl status apme-pod.service

# View logs for a specific service
journalctl -u apme-primary.service -f

# Restart the entire pod
systemctl restart apme-pod.service

# Stop APME
systemctl stop apme-pod.service

# Disable APME from starting on boot
systemctl disable apme-pod.service
```

## Persistent Data

| Path | Purpose | Backup? |
|------|---------|---------|
| `/var/lib/apme/sessions/` | Session venvs (ephemeral, rebuilt on scan) | No |
| `/var/lib/apme/gateway/` | Gateway SQLite database (scan history) | Yes |
| `/var/lib/apme/proxy-cache/` | Galaxy Proxy wheel cache | No |

Back up `/var/lib/apme/gateway/` to preserve scan history across upgrades.

## Exposed Ports

| Port | Service | Protocol |
|------|---------|----------|
| 8080 | Gateway REST API | HTTP |
| 8081 | UI (dashboard) | HTTP |
| 50051 | Primary gRPC | gRPC |

Access the UI at `http://<vm-ip>:8081` and the API at `http://<vm-ip>:8080`.

## File Layout

```
deploy/bootc/
├── Containerfile                     # bootc image definition
├── README.md                         # This file
├── apme-firewall.xml                 # firewalld service definition
├── apme.env.example                  # Runtime configuration → /etc/apme/env/apme.env
└── quadlet/
    ├── apme.pod                      # Pod definition (ports, dependencies)
    ├── apme-primary.container        # Primary orchestrator
    ├── apme-native.container         # Native validator
    ├── apme-opa.container            # OPA validator
    ├── apme-ansible.container        # Ansible validator
    ├── apme-gitleaks.container       # Gitleaks validator
    ├── apme-collection-health.container  # Collection health
    ├── apme-dep-audit.container      # Dependency audit
    ├── apme-galaxy-proxy.container   # Galaxy Proxy (PEP 503)
    ├── apme-gateway.container        # Gateway (REST + gRPC + DB)
    └── apme-ui.container             # UI (nginx)
```

## Troubleshooting

### Containers not starting

Check that images are available locally:

```bash
podman images | grep apme
```

If images are missing, pull them:

```bash
podman pull ghcr.io/ansible/apme-primary:latest
# ... repeat for each service
```

### Port conflicts

If ports 8080/8081/50051 are in use, either stop the conflicting service or
edit the quadlet files under `/usr/share/containers/systemd/` and run
`systemctl daemon-reload`.

### Viewing generated systemd units

Quadlet files are converted to systemd units at generator time. To see the
generated unit:

```bash
/usr/lib/systemd/system-generators/podman-system-generator --dryrun
```
