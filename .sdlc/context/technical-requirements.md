# Technical Requirements

Non-functional and technical constraints for APME implementation.

## Parsing

| Requirement | Details |
|-------------|---------|
| YAML Support | Must support YAML 1.2 |
| Jinja2 | Must understand Ansible Jinja2 templating logic |
| Content Types | Playbooks, roles, collections, task files |

## Metadata

| Requirement | Details |
|-------------|---------|
| Module Database | Comprehensive mapping of module changes from Ansible 2.9 through current |
| Version Coverage | Support target versions: 2.14, 2.15, 2.16, 2.17+ |
| Collection Registry | Track collection versions and compatibility |

## Authentication

| Requirement | Details |
|-------------|---------|
| SSO/OIDC | Support enterprise identity providers |
| Private Galaxy | Support private Automation Hub / Galaxy servers |
| API Keys | Support token-based authentication for CI/CD |

## Output Formats

| Format | Purpose |
|--------|---------|
| JSON | Automation and API consumption |
| JUnit | CI/CD pipeline integration |
| SARIF | GitHub/GitLab code scanning |
| Text | Human-readable terminal output |

## Performance

| Metric | Target |
|--------|--------|
| Scan Speed | 100 playbooks in < 30 seconds |
| Memory | < 512MB for typical scan |
| Concurrency | Support parallel validation |

## Security

| Requirement | Details |
|-------------|---------|
| No Secrets in Logs | Redact sensitive data |
| Container Isolation | Run validators in isolated containers |
| Rootless | Support rootless container execution |

## Deployment

| Requirement | Details |
|-------------|---------|
| Container-Native | Podman/Docker deployment |
| Kubernetes-Ready | Pod-based architecture |
| Offline Mode | Function without internet (cached collections) |
