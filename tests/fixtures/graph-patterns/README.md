# Graph Pattern Test Fixtures

Comprehensive Ansible content for validating ContentGraph construction
(ADR-044). Each file exercises specific graph edge types from the
node/edge taxonomy.

## Coverage matrix

| Pattern | File(s) | Edge type(s) |
|---------|---------|--------------|
| Static roles list | `site.yml` | `dependency` |
| import_playbook | `master.yml` → `site.yml`, `deploy.yml` | `import` (cross-playbook) |
| import_tasks | `site.yml`, `roles/webserver/tasks/main.yml` | `import` |
| import_role | `site.yml` | `import` |
| include_tasks | `site.yml` | `include` |
| include_role | `site.yml` | `include` |
| Dynamic include (variable path) | `site.yml` | `include` + `dynamic: true` |
| Handlers + notify | `site.yml`, `roles/webserver/handlers/main.yml` | `notify`, `listen` |
| Block + rescue/always | `site.yml` | `rescue`, `always` |
| Nested blocks | `site.yml` | nested `contains` |
| vars_files (play keyword) | `site.yml` | `vars_include` |
| group_vars | `group_vars/all/main.yml` | inventory vars |
| host_vars | `host_vars/web01/main.yml` | inventory vars |
| set_fact / register | `site.yml`, `tasks/deploy_app.yml` | `data_flow` |
| Role dependencies | `roles/webserver/meta/main.yml` | `dependency` |
| Multi-play | `site.yml` | multiple play nodes |
| Collection layout | `galaxy.yml` + `plugins/` | FQCN ownership |
| Python modules | `plugins/modules/`, `plugins/module_utils/` | `invokes`, `py_imports` |
| Filter plugins | `plugins/filter/` | `invokes` |
| Handler chaining | `roles/webserver/handlers/main.yml` | `notify` (handler→handler) |
