# Shared APME Backstage frontend

Projects, findings, workflow, history, dependencies, graphs, rules and settings
are host-composable. Mutation permissions default to denied in embedding hosts.
The Local host factory is for personal deployments only. Portal supplies RBAC.

Run `tox -e frontend-dev` for the Gateway-backed development harness or
`tox -e frontend-dev-build` to verify it. This PR adds the plugin without changing
the default Local deployment. Uploads and AI provider administration follow in
the next stack PR; Portal integration and Local activation follow separately.
