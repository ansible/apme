#!/usr/bin/env python3
"""
TASK-001 PoC: Rich Terminal Output

Demonstrates CLI scan results using Rich tables and panels.
Run: python rich_terminal.py
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

# Sample scan results (simulating CLI output)
SAMPLE_RESULTS = {
    "summary": {
        "total": 12,
        "errors": 2,
        "warnings": 7,
        "hints": 3,
    },
    "issues": [
        {"rule": "L001", "severity": "error", "message": "Module 'apt' should use FQCN", "file": "playbook.yml", "line": 15},
        {"rule": "L001", "severity": "error", "message": "Module 'yum' should use FQCN", "file": "playbook.yml", "line": 23},
        {"rule": "M002", "severity": "warning", "message": "Parameter 'state=latest' deprecated", "file": "tasks/main.yml", "line": 8},
        {"rule": "M003", "severity": "warning", "message": "Module 'include' deprecated, use 'include_tasks'", "file": "playbook.yml", "line": 45},
        {"rule": "R001", "severity": "warning", "message": "Using 'shell' when 'command' would suffice", "file": "handlers/main.yml", "line": 12},
        {"rule": "P001", "severity": "warning", "message": "Missing 'become' declaration", "file": "playbook.yml", "line": 1},
        {"rule": "SEC001", "severity": "warning", "message": "Hardcoded password detected", "file": "vars/main.yml", "line": 5},
        {"rule": "L002", "severity": "hint", "message": "Consider using 'ansible.builtin.debug'", "file": "playbook.yml", "line": 50},
    ],
}


def render_summary(console: Console, results: dict) -> None:
    """Render summary panel."""
    summary = results["summary"]

    summary_text = (
        f"[bold]Total Issues:[/bold] {summary['total']}\n"
        f"[red]Errors:[/red] {summary['errors']}  "
        f"[yellow]Warnings:[/yellow] {summary['warnings']}  "
        f"[blue]Hints:[/blue] {summary['hints']}"
    )

    panel = Panel(summary_text, title="Scan Summary", border_style="green")
    console.print(panel)


def render_issues_table(console: Console, results: dict) -> None:
    """Render issues as a table."""
    table = Table(title="Issues Found", show_header=True, header_style="bold magenta")

    table.add_column("Rule", style="cyan", width=8)
    table.add_column("Severity", width=10)
    table.add_column("Message", style="white")
    table.add_column("Location", style="dim")

    severity_styles = {
        "error": "[bold red]ERROR[/bold red]",
        "warning": "[yellow]WARNING[/yellow]",
        "hint": "[blue]HINT[/blue]",
    }

    for issue in results["issues"]:
        table.add_row(
            issue["rule"],
            severity_styles.get(issue["severity"], issue["severity"]),
            issue["message"],
            f"{issue['file']}:{issue['line']}",
        )

    console.print(table)


def render_issues_tree(console: Console, results: dict) -> None:
    """Render issues as a tree grouped by file."""
    tree = Tree("[bold]Issues by File[/bold]")

    # Group by file
    by_file: dict[str, list] = {}
    for issue in results["issues"]:
        by_file.setdefault(issue["file"], []).append(issue)

    severity_icons = {"error": "[red]✗[/red]", "warning": "[yellow]⚠[/yellow]", "hint": "[blue]ℹ[/blue]"}

    for filename, issues in by_file.items():
        file_branch = tree.add(f"[cyan]{filename}[/cyan]")
        for issue in issues:
            icon = severity_icons.get(issue["severity"], "•")
            file_branch.add(f"{icon} L{issue['line']}: {issue['rule']} - {issue['message']}")

    console.print(tree)


def main() -> None:
    console = Console()

    console.print("\n[bold green]APME Scan Results[/bold green]\n")

    render_summary(console, SAMPLE_RESULTS)
    console.print()

    render_issues_table(console, SAMPLE_RESULTS)
    console.print()

    render_issues_tree(console, SAMPLE_RESULTS)
    console.print()


if __name__ == "__main__":
    main()
