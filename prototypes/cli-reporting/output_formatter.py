#!/usr/bin/env python3
"""
TASK-001: CLI Output Formatter Implementation

This module shows what would be required to add Rich-based output formatting
to the APME scanner CLI. It provides four output modes:
- Rich terminal (default)
- JSON (for automation)
- JUnit XML (for CI/CD)
- HTML (for sharing)

Run this file to see example outputs for all formats:
    uv run python prototypes/cli-reporting/output_formatter.py

Architecture Integration:
- CLI receives ScanResponse from Primary (gRPC)
- This formatter transforms violations into the selected output format
- Integrates with Typer CLI via --format and --output flags
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TextIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree


# =============================================================================
# DATA MODELS (mirrors proto/apme/v1/common.proto)
# =============================================================================


class Level(Enum):
    """Violation severity level."""

    ERROR = "error"
    WARNING = "warning"
    HINT = "hint"


@dataclass
class Violation:
    """Single scan violation (mirrors protobuf Violation message)."""

    rule_id: str
    level: Level
    message: str
    file: str
    line: int
    path: str = ""  # JSON path within YAML


@dataclass
class ScanResult:
    """Complete scan result (subset of ScanResponse)."""

    project_path: str
    timestamp: str
    violations: list[Violation]
    files_scanned: int
    scan_time_ms: float


# =============================================================================
# OUTPUT FORMAT ENUM
# =============================================================================


class OutputFormat(Enum):
    """Supported output formats."""

    RICH = "rich"  # Terminal with Rich formatting
    JSON = "json"  # JSON for automation
    JUNIT = "junit"  # JUnit XML for CI
    HTML = "html"  # HTML report for sharing


# =============================================================================
# FORMATTER IMPLEMENTATIONS
# =============================================================================


def format_rich(result: ScanResult, console: Console) -> None:
    """
    Render scan results using Rich terminal formatting.

    This is the default output format for interactive use.
    """
    # Summary counts
    error_count = sum(1 for v in result.violations if v.level == Level.ERROR)
    warning_count = sum(1 for v in result.violations if v.level == Level.WARNING)
    hint_count = sum(1 for v in result.violations if v.level == Level.HINT)
    total = len(result.violations)

    # Determine overall status
    if error_count > 0:
        status = "[bold red]FAILED[/bold red]"
        border = "red"
    elif warning_count > 0:
        status = "[bold yellow]WARNINGS[/bold yellow]"
        border = "yellow"
    else:
        status = "[bold green]PASSED[/bold green]"
        border = "green"

    # Header
    console.print()
    console.rule("[bold blue]APME Scan Results[/bold blue]")
    console.print()

    # Summary panel
    summary = (
        f"Status: {status}\n\n"
        f"[red]● Errors:[/red] {error_count}\n"
        f"[yellow]● Warnings:[/yellow] {warning_count}\n"
        f"[blue]● Hints:[/blue] {hint_count}\n"
        f"[dim]─────────────[/dim]\n"
        f"[bold]Total:[/bold] {total} issues in {result.files_scanned} files\n"
        f"[dim]Scan time: {result.scan_time_ms:.0f}ms[/dim]"
    )
    console.print(Panel(summary, title="Summary", border_style=border))
    console.print()

    if not result.violations:
        console.print("[green]No issues found![/green]")
        return

    # Issues table
    table = Table(
        title="Issues",
        show_header=True,
        header_style="bold white on dark_blue",
        border_style="blue",
    )

    table.add_column("Rule", style="cyan", width=10, justify="center")
    table.add_column("Severity", width=10, justify="center")
    table.add_column("Message", min_width=40)
    table.add_column("Location", style="dim", width=30)

    level_styles = {
        Level.ERROR: "[bold white on red] ERROR [/bold white on red]",
        Level.WARNING: "[black on yellow] WARN  [/black on yellow]",
        Level.HINT: "[white on blue] HINT  [/white on blue]",
    }

    for v in result.violations:
        table.add_row(
            v.rule_id,
            level_styles[v.level],
            v.message,
            f"{v.file}:{v.line}",
        )

    console.print(table)
    console.print()

    # Tree view grouped by file
    tree = Tree("[bold]Issues by File[/bold]")
    by_file: dict[str, list[Violation]] = {}
    for v in result.violations:
        by_file.setdefault(v.file, []).append(v)

    icons = {
        Level.ERROR: "[red]✗[/red]",
        Level.WARNING: "[yellow]⚠[/yellow]",
        Level.HINT: "[blue]ℹ[/blue]",
    }

    for filename, violations in by_file.items():
        branch = tree.add(f"[cyan]{filename}[/cyan]")
        for v in violations:
            branch.add(f"{icons[v.level]} L{v.line}: {v.rule_id} - {v.message}")

    console.print(tree)
    console.print()


def format_json(result: ScanResult, output: TextIO) -> None:
    """
    Render scan results as JSON for automation.

    Schema designed for machine parsing and CI/CD integration.
    """
    data = {
        "version": "1.0",
        "scan": {
            "project": result.project_path,
            "timestamp": result.timestamp,
            "files_scanned": result.files_scanned,
            "scan_time_ms": result.scan_time_ms,
        },
        "summary": {
            "total": len(result.violations),
            "errors": sum(1 for v in result.violations if v.level == Level.ERROR),
            "warnings": sum(1 for v in result.violations if v.level == Level.WARNING),
            "hints": sum(1 for v in result.violations if v.level == Level.HINT),
            "passed": not any(v.level == Level.ERROR for v in result.violations),
        },
        "violations": [
            {
                "rule_id": v.rule_id,
                "level": v.level.value,
                "message": v.message,
                "file": v.file,
                "line": v.line,
                "path": v.path or None,
            }
            for v in result.violations
        ],
    }
    json.dump(data, output, indent=2)
    output.write("\n")


def format_junit(result: ScanResult, output: TextIO) -> None:
    """
    Render scan results as JUnit XML for CI/CD integration.

    Compatible with Jenkins, GitHub Actions, GitLab CI, etc.
    """
    error_count = sum(1 for v in result.violations if v.level == Level.ERROR)
    warning_count = sum(1 for v in result.violations if v.level == Level.WARNING)
    total = len(result.violations)

    # Root element
    testsuites = ET.Element("testsuites")
    testsuites.set("name", "APME Scan")
    testsuites.set("tests", str(total))
    testsuites.set("failures", str(error_count))
    testsuites.set("errors", "0")
    testsuites.set("time", str(result.scan_time_ms / 1000))

    # Group violations by file (each file = testsuite)
    by_file: dict[str, list[Violation]] = {}
    for v in result.violations:
        by_file.setdefault(v.file, []).append(v)

    for filename, violations in by_file.items():
        testsuite = ET.SubElement(testsuites, "testsuite")
        testsuite.set("name", filename)
        testsuite.set("tests", str(len(violations)))
        testsuite.set(
            "failures", str(sum(1 for v in violations if v.level == Level.ERROR))
        )
        testsuite.set("errors", "0")

        for v in violations:
            testcase = ET.SubElement(testsuite, "testcase")
            testcase.set("name", f"{v.rule_id} at line {v.line}")
            testcase.set("classname", filename.replace("/", ".").replace(".yml", ""))

            if v.level == Level.ERROR:
                failure = ET.SubElement(testcase, "failure")
                failure.set("type", v.rule_id)
                failure.set("message", v.message)
                failure.text = f"{v.file}:{v.line}: {v.message}"
            elif v.level == Level.WARNING:
                # Warnings as skipped with message (not failures)
                skipped = ET.SubElement(testcase, "skipped")
                skipped.set("message", f"[WARNING] {v.message}")

    # Write XML
    tree = ET.ElementTree(testsuites)
    ET.indent(tree, space="  ")
    tree.write(output, encoding="unicode", xml_declaration=True)
    output.write("\n")


def format_html(result: ScanResult, output_path: Path) -> None:
    """
    Render scan results as standalone HTML using Rich's save_html().

    Generates a shareable HTML file that can be opened in any browser.
    """
    console = Console(record=True, width=120, force_terminal=True)
    format_rich(result, console)
    console.save_html(str(output_path))
    print(f"HTML report saved to: {output_path}")


# =============================================================================
# MAIN FORMATTER ENTRY POINT
# =============================================================================


def format_output(
    result: ScanResult,
    format: OutputFormat,
    output: Path | None = None,
) -> int:
    """
    Format scan results in the specified format.

    Args:
        result: Scan results from Primary service
        format: Output format (rich, json, junit, html)
        output: Output file path (stdout if None, required for HTML)

    Returns:
        Exit code (0 = passed, 1 = errors found)
    """
    has_errors = any(v.level == Level.ERROR for v in result.violations)

    if format == OutputFormat.RICH:
        console = Console()
        format_rich(result, console)

    elif format == OutputFormat.JSON:
        if output:
            with open(output, "w") as f:
                format_json(result, f)
        else:
            format_json(result, sys.stdout)

    elif format == OutputFormat.JUNIT:
        if output:
            with open(output, "w") as f:
                format_junit(result, f)
        else:
            format_junit(result, sys.stdout)

    elif format == OutputFormat.HTML:
        if not output:
            output = Path("apme-report.html")
        format_html(result, output)

    return 1 if has_errors else 0


# =============================================================================
# CLI INTEGRATION EXAMPLE (for apme/cli.py)
# =============================================================================

CLI_INTEGRATION = '''
# Add to src/apme/cli.py

import typer
from pathlib import Path
from enum import Enum

class OutputFormat(str, Enum):
    rich = "rich"
    json = "json"
    junit = "junit"
    html = "html"

@app.command()
def scan(
    path: Path = typer.Argument(Path("."), help="Path to scan"),
    format: OutputFormat = typer.Option(
        OutputFormat.rich,
        "--format", "-f",
        help="Output format",
    ),
    output: Path | None = typer.Option(
        None,
        "--output", "-o",
        help="Output file (required for HTML, optional for JSON/JUnit)",
    ),
    html: Path | None = typer.Option(
        None,
        "--html",
        help="Shortcut for --format html --output FILE",
    ),
):
    """Scan Ansible content for compatibility issues."""

    # Handle --html shortcut
    if html:
        format = OutputFormat.html
        output = html

    # Call Primary.Scan via gRPC
    result = scan_project(path)  # Returns ScanResult

    # Format and output
    exit_code = format_output(result, format, output)
    raise typer.Exit(exit_code)
'''


# =============================================================================
# SAMPLE DATA & DEMO
# =============================================================================

SAMPLE_RESULT = ScanResult(
    project_path="/home/user/ansible-project",
    timestamp=datetime.now(timezone.utc).isoformat(),
    files_scanned=23,
    scan_time_ms=847.3,
    violations=[
        Violation(
            rule_id="L001",
            level=Level.ERROR,
            message="Module 'apt' should use FQCN 'ansible.builtin.apt'",
            file="playbook.yml",
            line=15,
        ),
        Violation(
            rule_id="L001",
            level=Level.ERROR,
            message="Module 'yum' should use FQCN 'ansible.builtin.yum'",
            file="playbook.yml",
            line=23,
        ),
        Violation(
            rule_id="M002",
            level=Level.WARNING,
            message="Parameter 'state=latest' deprecated in ansible-core 2.16+",
            file="tasks/install.yml",
            line=8,
        ),
        Violation(
            rule_id="M003",
            level=Level.WARNING,
            message="Module 'include' deprecated, use 'ansible.builtin.include_tasks'",
            file="playbook.yml",
            line=45,
        ),
        Violation(
            rule_id="R001",
            level=Level.WARNING,
            message="Using 'shell' when 'command' would suffice",
            file="handlers/main.yml",
            line=12,
        ),
        Violation(
            rule_id="SEC001",
            level=Level.WARNING,
            message="Potential hardcoded password detected",
            file="vars/secrets.yml",
            line=5,
        ),
        Violation(
            rule_id="P001",
            level=Level.WARNING,
            message="Missing 'become' declaration at play level",
            file="playbook.yml",
            line=1,
        ),
        Violation(
            rule_id="L002",
            level=Level.HINT,
            message="Consider using 'ansible.builtin.debug' instead of 'debug'",
            file="playbook.yml",
            line=50,
        ),
        Violation(
            rule_id="L003",
            level=Level.HINT,
            message="Task name could be more descriptive",
            file="tasks/install.yml",
            line=1,
        ),
    ],
)


def demo_all_formats() -> None:
    """Demonstrate all output formats with sample data."""
    output_dir = Path(__file__).parent

    print("=" * 80)
    print("DEMO: apme scan . (Rich terminal output - default)")
    print("=" * 80)
    format_output(SAMPLE_RESULT, OutputFormat.RICH)

    print("\n" + "=" * 80)
    print("DEMO: apme scan . --json")
    print("=" * 80 + "\n")
    format_output(SAMPLE_RESULT, OutputFormat.JSON)

    print("\n" + "=" * 80)
    print("DEMO: apme scan . --junit")
    print("=" * 80 + "\n")
    format_output(SAMPLE_RESULT, OutputFormat.JUNIT)

    print("\n" + "=" * 80)
    print("DEMO: apme scan . --html report.html")
    print("=" * 80)
    html_path = output_dir / "demo_report.html"
    format_output(SAMPLE_RESULT, OutputFormat.HTML, html_path)
    print(f"\nOpen in browser: file://{html_path.absolute()}")


if __name__ == "__main__":
    demo_all_formats()
