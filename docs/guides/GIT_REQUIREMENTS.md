# Git collections and roles

Enable **Settings → Git collections and roles → Allow Git requirements for all
projects**. This policy is saved in the Gateway database and defaults to disabled.

Declare dependencies in the project's `requirements.yml` or `requirements.yaml`:

```yaml
collections:
  - name: git+https://git.example.com/automation/custom_collection.git
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

Git dependencies use the project SCM credential by default, followed by the
existing deployment SCM fallback. When the global policy is enabled, **Project →
Settings → Git requirements credential** lets you supply a separate credential
for dependency repositories. This credential overrides the default for Git
requirements and does not affect project cloning or pull requests. Remove it to
return to the default. Saved credentials are never returned by the API.

The Gateway fetches declared dependencies before checks and remediation. Engine
installs the supplied files in its session venv and exposes roles to Ansible
syntax checks. Dependencies are separate from project source and cannot be
included in generated project edits. Their content selects the venv, so updated
Git content does not reuse stale dependency files or plugin cache entries.
Galaxy requirements continue through the Galaxy Proxy.

Use HTTPS Git URLs without embedded credentials. Other transports, symlinks,
files over 2 MiB, and dependency uploads over the session file/byte limits are
rejected. Fetch failures fail the operation. This implementation resolves direct
Git declarations; it does not recursively fetch Git manifests or install Python
packages declared inside dependency repositories. It applies to Gateway project
operations; local CLI uploads do not fetch Git dependencies.

Changing the gRPC dependency payload requires upgrading Gateway and Engine
together. Existing clients without dependency payloads retain their scan flow.
