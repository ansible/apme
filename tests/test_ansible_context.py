"""Tests for selecting and transmitting Ansible project context."""

from apme.v1.common_pb2 import File
from apme_engine.daemon.engine_server import _ansible_context_files


def test_ansible_context_keeps_declared_plugin_sources_and_collection_metadata() -> None:
    """Configured roots, role plugins, and collection plugins are retained."""
    files = {
        "ansible.cfg": b"[defaults]\nlibrary = custom_plugins\ncollections_path = collection_cache\n",
        "custom_plugins/helper.py": b"helper = True\n",
        "roles/web/library/site_module.py": b"module = True\n",
        "library/default_module.py": b"module = True\n",
        "ansible_collections/acme/demo/plugins/modules/sample.py": b"module = True\n",
        "ansible_collections/acme/demo/meta/runtime.yml": b"requires_ansible: '>=2.14'\n",
        "ansible_collections/acme/demo/MANIFEST.json": b"{}\n",
        "collection_cache/ansible_collections/contoso/tools/plugins/modules/remote.py": b"module = True\n",
        "collection_cache/ansible_collections/contoso/tools/README.py": b"unrelated = True\n",
        "collection_cache/unrelated.py": b"unrelated = True\n",
        "src/plugins/not_a_plugin.py": b"unrelated = True\n",
        "ansible_collections/acme/demo/README.py": b"unrelated = True\n",
    }

    selected = _ansible_context_files(files)

    assert {file.path for file in selected} == {
        "ansible.cfg",
        "custom_plugins/helper.py",
        "roles/web/library/site_module.py",
        "library/default_module.py",
        "ansible_collections/acme/demo/plugins/modules/sample.py",
        "ansible_collections/acme/demo/meta/runtime.yml",
        "ansible_collections/acme/demo/MANIFEST.json",
        "collection_cache/ansible_collections/contoso/tools/plugins/modules/remote.py",
    }
    assert all(isinstance(file, File) for file in selected)
