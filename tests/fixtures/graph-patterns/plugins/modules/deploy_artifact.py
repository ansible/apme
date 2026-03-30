"""Test fixture module — exercises py_imports and invokes edges.

GraphBuilder should detect:
- ``py_imports`` edge from this module to ``module_utils.deploy_helpers``
- ``invokes`` edge from tasks using ``testorg.graph_patterns.deploy_artifact``
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.testorg.graph_patterns.plugins.module_utils.deploy_helpers import (  # type: ignore[import-not-found]
    validate_artifact,
)

DOCUMENTATION = r"""
module: deploy_artifact
short_description: Deploy an artifact
description:
  - Fixture module for ContentGraph edge testing.
options:
  name:
    description: Artifact name
    required: true
    type: str
  version:
    description: Artifact version
    required: true
    type: str
"""


def main() -> None:
    """Run the deploy_artifact module."""
    module = AnsibleModule(
        argument_spec={
            "name": {"required": True, "type": "str"},
            "version": {"required": True, "type": "str"},
        },
        supports_check_mode=True,
    )

    if not validate_artifact(module.params["name"], module.params["version"]):
        module.fail_json(msg="Invalid artifact specification")

    module.exit_json(changed=False, artifact=module.params["name"])


if __name__ == "__main__":
    main()
