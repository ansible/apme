"""Ansible version constants for the validator.

The Primary orchestrator owns all venv lifecycle (creation, collection
install, reaping via ``VenvSessionManager``).  This module only exports
the default version constant that the validator and Primary need.
"""

DEFAULT_VERSION = "2.20"
