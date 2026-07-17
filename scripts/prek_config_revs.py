#!/usr/bin/env python3
"""Keep shared hook ``rev`` pins aligned between ``prek.toml`` and pre-commit YAML.

``tox -e lint`` / CI run ``prek``, which prefers ``prek.toml`` when present.
Dependabot's ``pre-commit`` ecosystem only updates ``.pre-commit-config.yaml``
(upstream: dependabot/dependabot-core#14624). After a Dependabot pre-commit PR::

    python scripts/prek_config_revs.py sync --direction yaml-to-toml

CI / ``prek`` run the ``check`` subcommand and fail on drift.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_TOML = _ROOT / "prek.toml"
_YAML = _ROOT / ".pre-commit-config.yaml"

_SKIP_REPOS = frozenset({"local", "meta"})

# prek.toml is not always stdlib-tomllib-parseable (multiline inline tables),
# so repo/rev extraction is line-based for both formats.
_TOML_REPO_RE = re.compile(r'^(?P<indent>\s*)repo\s*=\s*"(?P<repo>[^"]+)"\s*$')
_TOML_REV_RE = re.compile(r'^(?P<indent>\s*)rev\s*=\s*"(?P<rev>[^"]+)"\s*$')
_YAML_REPO_RE = re.compile(r"^(?P<indent>\s*)-\s+repo:\s+(?P<repo>\S+)\s*$")
_YAML_REV_RE = re.compile(r"^(?P<indent>\s*)rev:\s+(?P<rev>\S+)\s*$")


def _normalize_repo(repo: str) -> str:
    """Normalize repo URLs for comparison.

    Args:
        repo: Raw repository URL or special repo name.

    Returns:
        Normalized repository identifier.
    """
    return repo.rstrip("/").removesuffix(".git")


def _load_revs_from_lines(
    text: str,
    *,
    repo_re: re.Pattern[str],
    rev_re: re.Pattern[str],
) -> dict[str, str]:
    """Parse ``{repo: rev}`` pairs from sequential repo/rev lines.

    Args:
        text: Config file text.
        repo_re: Regex matching a repo declaration line.
        rev_re: Regex matching a rev declaration line.

    Returns:
        Mapping of normalized repo URL to rev pin.
    """
    revs: dict[str, str] = {}
    pending_repo: str | None = None
    for raw in text.splitlines():
        repo_match = repo_re.match(raw)
        if repo_match is not None:
            pending_repo = _normalize_repo(repo_match.group("repo"))
            continue
        rev_match = rev_re.match(raw)
        if rev_match is not None and pending_repo is not None:
            if pending_repo not in _SKIP_REPOS:
                revs[pending_repo] = rev_match.group("rev")
            pending_repo = None
    return revs


def load_toml_revs(path: Path) -> dict[str, str]:
    """Return ``{repo: rev}`` for remote repos in a prek.toml file.

    Args:
        path: Path to ``prek.toml``.

    Returns:
        Mapping of normalized repo URL to rev pin.
    """
    return _load_revs_from_lines(
        path.read_text(encoding="utf-8"),
        repo_re=_TOML_REPO_RE,
        rev_re=_TOML_REV_RE,
    )


def load_yaml_revs(path: Path) -> dict[str, str]:
    """Return ``{repo: rev}`` for remote repos in a pre-commit YAML file.

    Prefer structured YAML load; fall back to line parsing if needed
    (including when ``yaml.safe_load`` raises ``YAMLError``).

    Args:
        path: Path to ``.pre-commit-config.yaml``.

    Returns:
        Mapping of normalized repo URL to rev pin.
    """
    text = path.read_text(encoding="utf-8")
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return _load_revs_from_lines(
            text,
            repo_re=_YAML_REPO_RE,
            rev_re=_YAML_REV_RE,
        )
    data = loaded if isinstance(loaded, dict) else {}
    revs: dict[str, str] = {}
    for entry in data.get("repos", []):
        if not isinstance(entry, dict):
            continue
        repo = str(entry.get("repo", ""))
        rev = entry.get("rev")
        if not repo or repo in _SKIP_REPOS or rev is None:
            continue
        revs[_normalize_repo(repo)] = str(rev)
    if revs:
        return revs
    return _load_revs_from_lines(
        text,
        repo_re=_YAML_REPO_RE,
        rev_re=_YAML_REV_RE,
    )


def compare_shared_revs(
    toml_revs: dict[str, str],
    yaml_revs: dict[str, str],
) -> list[tuple[str, str, str]]:
    """Return mismatches for repos present in both configs.

    Args:
        toml_revs: Repo-to-rev map from ``prek.toml``.
        yaml_revs: Repo-to-rev map from ``.pre-commit-config.yaml``.

    Returns:
        List of ``(repo, toml_rev, yaml_rev)`` triples that disagree.
    """
    shared = sorted(set(toml_revs) & set(yaml_revs))
    return [(repo, toml_revs[repo], yaml_revs[repo]) for repo in shared if toml_revs[repo] != yaml_revs[repo]]


def _replace_revs_in_text(
    text: str,
    *,
    repo_re: re.Pattern[str],
    rev_re: re.Pattern[str],
    target_revs: dict[str, str],
    rev_formatter: str,
) -> tuple[str, int]:
    """Replace ``rev`` lines that follow a matching ``repo`` declaration.

    Args:
        text: Config file text to rewrite.
        repo_re: Regex matching a repo declaration line.
        rev_re: Regex matching a rev declaration line.
        target_revs: Desired rev for each normalized repo URL.
        rev_formatter: Format string containing ``{rev}``.

    Returns:
        Updated text and the number of rev lines changed.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    pending_repo: str | None = None
    replacements = 0

    for line in lines:
        repo_match = repo_re.match(line.rstrip("\n"))
        if repo_match is not None:
            pending_repo = _normalize_repo(repo_match.group("repo"))
            out.append(line)
            continue

        rev_match = rev_re.match(line.rstrip("\n"))
        if rev_match is not None and pending_repo is not None:
            new_rev = target_revs.get(pending_repo)
            if new_rev is not None and new_rev != rev_match.group("rev"):
                newline = "\n" if line.endswith("\n") else ""
                out.append(f"{rev_match.group('indent')}{rev_formatter.format(rev=new_rev)}{newline}")
                replacements += 1
            else:
                out.append(line)
            pending_repo = None
            continue

        out.append(line)

    return "".join(out), replacements


def sync_revs(*, direction: str, toml_path: Path, yaml_path: Path) -> int:
    """Copy shared remote revs in ``direction``.

    Args:
        direction: ``yaml-to-toml`` or ``toml-to-yaml``.
        toml_path: Path to ``prek.toml``.
        yaml_path: Path to ``.pre-commit-config.yaml``.

    Returns:
        Number of rev pins updated.

    Raises:
        ValueError: If ``direction`` is not recognized.
    """
    toml_revs = load_toml_revs(toml_path)
    yaml_revs = load_yaml_revs(yaml_path)
    shared = set(toml_revs) & set(yaml_revs)
    if not shared:
        print("error: no shared remote repos to sync", file=sys.stderr)
        return 0

    if direction == "yaml-to-toml":
        text, n = _replace_revs_in_text(
            toml_path.read_text(encoding="utf-8"),
            repo_re=_TOML_REPO_RE,
            rev_re=_TOML_REV_RE,
            target_revs={repo: yaml_revs[repo] for repo in shared},
            rev_formatter='rev = "{rev}"',
        )
        if n:
            toml_path.write_text(text, encoding="utf-8")
        return n

    if direction == "toml-to-yaml":
        text, n = _replace_revs_in_text(
            yaml_path.read_text(encoding="utf-8"),
            repo_re=_YAML_REPO_RE,
            rev_re=_YAML_REV_RE,
            target_revs={repo: toml_revs[repo] for repo in shared},
            rev_formatter="rev: {rev}",
        )
        if n:
            yaml_path.write_text(text, encoding="utf-8")
        return n

    raise ValueError(f"unknown direction: {direction}")


def cmd_check(toml_path: Path, yaml_path: Path) -> int:
    """Exit 1 when shared remote revs disagree.

    Args:
        toml_path: Path to ``prek.toml``.
        yaml_path: Path to ``.pre-commit-config.yaml``.

    Returns:
        Process exit code (0 aligned, 1 drift or missing files).
    """
    if not toml_path.is_file() or not yaml_path.is_file():
        print(
            "error: both prek.toml and .pre-commit-config.yaml are required",
            file=sys.stderr,
        )
        return 1

    toml_revs = load_toml_revs(toml_path)
    yaml_revs = load_yaml_revs(yaml_path)
    shared = set(toml_revs) & set(yaml_revs)
    if not shared:
        print(
            "error: no shared remote repos between prek.toml and "
            ".pre-commit-config.yaml (parse failure or configs diverged)",
            file=sys.stderr,
        )
        return 1

    mismatches = compare_shared_revs(toml_revs, yaml_revs)
    if not mismatches:
        print("prek.toml and .pre-commit-config.yaml share matching remote revs")
        return 0

    print("Shared hook repo rev drift detected:", file=sys.stderr)
    for repo, toml_rev, yaml_rev in mismatches:
        print(f"  {repo}", file=sys.stderr)
        print(f"    prek.toml:                 {toml_rev}", file=sys.stderr)
        print(f"    .pre-commit-config.yaml:   {yaml_rev}", file=sys.stderr)
    print(
        "\nAfter Dependabot pre-commit PRs, sync the runtime config with:\n"
        "  python scripts/prek_config_revs.py sync --direction yaml-to-toml",
        file=sys.stderr,
    )
    return 1


def cmd_sync(direction: str, toml_path: Path, yaml_path: Path) -> int:
    """Apply sync and re-check.

    Args:
        direction: ``yaml-to-toml`` or ``toml-to-yaml``.
        toml_path: Path to ``prek.toml``.
        yaml_path: Path to ``.pre-commit-config.yaml``.

    Returns:
        Process exit code from the post-sync check.
    """
    n = sync_revs(direction=direction, toml_path=toml_path, yaml_path=yaml_path)
    print(f"Updated {n} rev pin(s) ({direction})")
    return cmd_check(toml_path, yaml_path)


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser.

    Returns:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--toml",
        type=Path,
        default=_TOML,
        help="Path to prek.toml (default: repo root)",
    )
    parser.add_argument(
        "--yaml",
        type=Path,
        default=_YAML,
        help="Path to .pre-commit-config.yaml (default: repo root)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Fail if shared remote revs disagree")

    sync = sub.add_parser("sync", help="Copy shared remote revs between configs")
    sync.add_argument(
        "--direction",
        choices=("yaml-to-toml", "toml-to-yaml"),
        required=True,
        help="yaml-to-toml: apply Dependabot YAML bumps into prek.toml",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return cmd_check(args.toml, args.yaml)
    if args.command == "sync":
        return cmd_sync(args.direction, args.toml, args.yaml)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
