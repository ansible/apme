"""Pure parsing of Ansible Git requirement declarations."""

from collections.abc import Iterator

REQUIREMENTS_PATHS = (
    "requirements.yml",
    "requirements.yaml",
    "collections/requirements.yml",
    "collections/requirements.yaml",
    "roles/requirements.yml",
    "roles/requirements.yaml",
)


def is_git_requirement(entry: object) -> bool:
    """Identify Git declarations without treating Galaxy names as URLs.

    Args:
        entry: String or mapping from an Ansible requirements file.

    Returns:
        Whether this entry declares a Git dependency.
    """
    if isinstance(entry, dict):
        return (
            entry.get("type") == "git"
            or entry.get("scm") == "git"
            or is_git_requirement(entry.get("src", entry.get("name", "")))
        )
    if not isinstance(entry, str):
        return False
    return entry.startswith(("git+", "ssh://", "git://", "git@")) or (
        entry.startswith("https://") and entry.split(",", 1)[0].split("#", 1)[0].endswith(".git")
    )


def git_entries(data: object, *, roles_file: bool = False) -> Iterator[tuple[str, dict[str, object]]]:
    """Yield typed Git declarations from collection and role requirements.

    Args:
        data: Parsed YAML requirements.
        roles_file: Interpret legacy lists as roles.

    Yields:
        tuple[str, dict[str, object]]: Kind and normalized requirement mapping.
    """
    groups = {"roles" if roles_file else "collections": data} if isinstance(data, list) else data
    if not isinstance(groups, dict):
        return
    for kind in ("collections", "roles"):
        entries = groups.get(kind, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if is_git_requirement(entry):
                yield kind, entry if isinstance(entry, dict) else {"name" if kind == "collections" else "src": entry}
