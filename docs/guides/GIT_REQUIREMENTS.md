# Git collections and roles

Enable **Settings → Git collections and roles → Allow Git requirements for all
projects**. This policy is saved in the Gateway database and defaults to disabled.

Declare dependencies in the project's `requirements.yml` or `requirements.yaml`:

```yaml
collections:
  - name: example.custom
    source: https://git.example.com/automation/custom_collection.git
    type: git
    version: v1.2.0
roles:
  - name: organization.base
    src: https://git.example.com/automation/base_role.git
    scm: git
    version: main
```

Requirements under `collections/` and `roles/` are also supported. Legacy role
requirement lists are accepted. Versions may be branches, tags, or commit SHAs;
omitting a version uses the repository's HEAD. For a collection in a repository
subdirectory, use `git+https://git.example.com/repo.git#path/to/collection`.
You can also put the Git URL directly in the collection's `name` field.

Git dependencies use the project SCM credential by default, followed by the
existing deployment SCM fallback. When the global policy is enabled, **Project →
Settings → Git requirements credential** lets you supply a separate credential
for dependency repositories. This credential overrides the default for Git
requirements and does not affect project cloning or pull requests. Remove it to
return to the default. Credentials are sent only to the project repository's HTTPS
origin; dependencies on other origins are fetched anonymously. Saved credentials
are never returned by the API.

The Gateway fetches declared dependencies before checks and remediation. Engine
packages Git collections as local wheels and installs them in the same session
venv as Galaxy collections. Git collections appear in the dependency tree, and
the resolver uses the configured Galaxy servers for their collection
dependencies. A Git collection's Python requirements file remains part of
its snapshot, with Python package installation scoped to those declared by
Galaxy wheels. The Proxy relays those Python wheels during mixed installs.
Required Python packages need a compatible wheel in the configured index for a
mixed install to succeed. The session's Ansible Core version must also satisfy
any `requires_ansible` constraint declared in the collection's `meta/runtime.yml`.
Engine copies Git roles into the venv for Ansible syntax checks. Dependency
snapshots are separate from project
source and generated project edits. Their content selects the venv, so updates
select a fresh environment and plugin cache.
Git collection metadata must declare a version that can be represented in a
Python wheel. Use a release version or a supported prerelease such as `1.2.3-rc.1`.

Use HTTPS Git URLs without embedded credentials. Other transports, symlinks,
files over 2 MiB, and dependency uploads over the session file/byte limits are
rejected. Fetch failures fail the operation. This implementation resolves direct
Git declarations. Nested Git manifests are outside this resolution path. The
feature applies to Gateway project operations.

Changing the gRPC dependency payload requires upgrading Gateway and Engine
together. Existing clients without dependency payloads retain their scan flow.
Each distinct Git snapshot retains a separate venv on disk. Automatic disk-cache
reaping is not wired into the daemon yet; account for retained session storage
when operating this feature.

Disk cleanup follow-up: [ansible/apme#878](https://github.com/ansible/apme/issues/878).

Git requirement fetching uses the safeguards from [PR #886](https://github.com/ansible/apme/pull/886).
The local Podman pod provisions the bounded `APME_GIT_FETCH_ROOT` mount. For
other deployments, configure that mount before enabling the feature.
Private Git server addresses require deployment configuration through
`APME_GIT_PRIVATE_CIDRS`. See [Gateway Git fetch safeguards](SAFE_GIT_FETCH.md)
for exact limits, destination rules and examples. Missing enforcement fails closed.
