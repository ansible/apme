r"""Zero-dependency ANSI color/style abstraction for CLI output.

Provides terminal styling with NO_COLOR/FORCE_COLOR support, severity badges,
box drawing, simple tables, and tree connectors.

Usage:
    from apme_engine.cli.ansi import bold, red, green, severity_badge, box, table

    print(bold(red("Error:")), "something went wrong")
    print(severity_badge("high"))  # -> " ERROR " on red background
    print(box("Summary\n2 errors", title="Check Results"))
"""

from __future__ import annotations

import os
import re
import sys

# ─────────────────────────────────────────────────────────────────────────────
# TTY / NO_COLOR detection
# ─────────────────────────────────────────────────────────────────────────────

_color_enabled: bool | None = None


def _use_color() -> bool:
    """Check if color output should be used. Follows https://no-color.org.

    Returns:
        Whether color output should be used.
    """
    global _color_enabled
    if _color_enabled is not None:
        return _color_enabled

    # NO_COLOR takes precedence (any value disables color)
    if "NO_COLOR" in os.environ:
        _color_enabled = False
        return False

    # FORCE_COLOR enables color even without TTY (useful in CI)
    if os.environ.get("FORCE_COLOR"):
        _color_enabled = True
        return True

    # Default: enable if stdout is a TTY
    _color_enabled = sys.stdout.isatty()
    return _color_enabled


def reset_color_detection() -> None:
    """Reset color detection cache (for testing)."""
    global _color_enabled
    _color_enabled = None


def force_no_color() -> None:
    """Programmatically disable color output (for --no-ansi flag)."""
    global _color_enabled
    _color_enabled = False


# ─────────────────────────────────────────────────────────────────────────────
# ANSI SGR codes
# ─────────────────────────────────────────────────────────────────────────────


class Style:
    """ANSI SGR (Select Graphic Rendition) escape codes.

    Color constants aligned with ansible-creator's Color class.

    Attributes:
        RESET: Reset all styles.
        BOLD: Bold text.
        DIM: Dim text.
        UNDERLINE: Underlined text.
        REVERSE: Reverse video.
        BLACK: Black foreground.
        RED: Red foreground.
        GREEN: Green foreground.
        YELLOW: Yellow foreground.
        BLUE: Blue foreground.
        MAGENTA: Magenta foreground.
        CYAN: Cyan foreground.
        WHITE: White foreground.
        GREY: Grey foreground.
        GRAY: Grey foreground (alias).
        BRIGHT_RED: Bright red foreground.
        BRIGHT_GREEN: Bright green foreground.
        BRIGHT_YELLOW: Bright yellow foreground.
        BRIGHT_BLUE: Bright blue foreground.
        BRIGHT_MAGENTA: Bright magenta foreground.
        BRIGHT_CYAN: Bright cyan foreground.
        BRIGHT_WHITE: Bright white foreground.
        BG_RED: Red background.
        BG_GREEN: Green background.
        BG_YELLOW: Yellow background.
        BG_BLUE: Blue background.
        BG_MAGENTA: Magenta background.
        BG_CYAN: Cyan background.
    """

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    REVERSE = "\033[7m"

    # Foreground colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    GREY = "\033[90m"
    GRAY = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # Background colors (for badges)
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"


# ─────────────────────────────────────────────────────────────────────────────
# Core styling functions
# ─────────────────────────────────────────────────────────────────────────────


def style(text: str, *styles: str) -> str:
    """Wrap text with ANSI style codes. Respects NO_COLOR.

    Args:
        text: Text to style.
        *styles: ANSI SGR code strings to apply.

    Returns:
        Styled text string.
    """
    if not _use_color() or not styles:
        return text
    return "".join(styles) + text + Style.RESET


def bold(text: str) -> str:
    """Apply bold style.

    Args:
        text: Text to style.

    Returns:
        Styled text string.
    """
    return style(text, Style.BOLD)


def dim(text: str) -> str:
    """Apply dim style.

    Args:
        text: Text to style.

    Returns:
        Styled text string.
    """
    return style(text, Style.DIM)


def underline(text: str) -> str:
    """Apply underline style.

    Args:
        text: Text to style.

    Returns:
        Styled text string.
    """
    return style(text, Style.UNDERLINE)


def red(text: str) -> str:
    """Apply red foreground color.

    Args:
        text: Text to style.

    Returns:
        Styled text string.
    """
    return style(text, Style.RED)


def green(text: str) -> str:
    """Apply green foreground color.

    Args:
        text: Text to style.

    Returns:
        Styled text string.
    """
    return style(text, Style.GREEN)


def yellow(text: str) -> str:
    """Apply yellow foreground color.

    Args:
        text: Text to style.

    Returns:
        Styled text string.
    """
    return style(text, Style.YELLOW)


def blue(text: str) -> str:
    """Apply blue foreground color.

    Args:
        text: Text to style.

    Returns:
        Styled text string.
    """
    return style(text, Style.BLUE)


def magenta(text: str) -> str:
    """Apply magenta foreground color.

    Args:
        text: Text to style.

    Returns:
        Styled text string.
    """
    return style(text, Style.MAGENTA)


def cyan(text: str) -> str:
    """Apply cyan foreground color.

    Args:
        text: Text to style.

    Returns:
        Styled text string.
    """
    return style(text, Style.CYAN)


def gray(text: str) -> str:
    """Apply gray foreground color.

    Args:
        text: Text to style.

    Returns:
        Styled text string.
    """
    return style(text, Style.GREY)


# ─────────────────────────────────────────────────────────────────────────────
# ANSI-aware string width
# ─────────────────────────────────────────────────────────────────────────────

# Regex to match ANSI escape sequences
_ANSI_ESCAPE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text.

    Args:
        text: Text possibly containing ANSI codes.

    Returns:
        Text with ANSI codes removed.
    """
    return _ANSI_ESCAPE.sub("", text)


def visible_width(text: str) -> int:
    """Get visible width of text (excluding ANSI codes).

    Args:
        text: Text possibly containing ANSI codes.

    Returns:
        Visible character count.
    """
    return len(strip_ansi(text))


def ljust_ansi(text: str, width: int, fillchar: str = " ") -> str:
    """Left-justify text to width, accounting for ANSI codes.

    Args:
        text: Text to justify.
        width: Target width.
        fillchar: Fill character.

    Returns:
        Left-justified text string.
    """
    visible = visible_width(text)
    if visible >= width:
        return text
    return text + fillchar * (width - visible)


def rjust_ansi(text: str, width: int, fillchar: str = " ") -> str:
    """Right-justify text to width, accounting for ANSI codes.

    Args:
        text: Text to justify.
        width: Target width.
        fillchar: Fill character.

    Returns:
        Right-justified text string.
    """
    visible = visible_width(text)
    if visible >= width:
        return text
    return fillchar * (width - visible) + text


# ─────────────────────────────────────────────────────────────────────────────
# Severity badges
# ─────────────────────────────────────────────────────────────────────────────

# Map severity labels to display badge and style (ADR-043).
SEVERITY_DISPLAY: dict[str, tuple[str, str]] = {
    "critical": ("CRIT", Style.BG_RED + Style.WHITE + Style.BOLD),
    "error": ("ERROR", Style.BG_RED + Style.WHITE + Style.BOLD),
    "high": ("HIGH", Style.BG_RED + Style.WHITE + Style.BOLD),
    "medium": ("MED", Style.BG_YELLOW + Style.BOLD),
    "low": ("LOW", Style.BG_CYAN + Style.WHITE),
    "info": ("INFO", Style.BG_MAGENTA + Style.WHITE),
}


def severity_badge(level: str) -> str:
    """Return a colored badge for a severity level.

    Args:
        level: Severity label (critical, error, high, medium, low, info).

    Returns:
        Styled badge like " ERROR " with colored background.
    """
    level_lower = level.lower() if level else "info"
    label, styles = SEVERITY_DISPLAY.get(level_lower, ("?", Style.DIM))
    padded = f" {label} "
    return style(padded, styles)


def severity_indicator(level: str) -> str:
    """Return a single-char severity indicator for tree views.

    Args:
        level: Severity label.

    Returns:
        Colored indicator: x (red), △ (yellow), i (cyan).
    """
    level_lower = level.lower() if level else "info"
    if level_lower in ("critical", "error", "high"):
        return red("x")
    if level_lower == "medium":
        return yellow("△")
    return cyan("i")


# ─────────────────────────────────────────────────────────────────────────────
# Remediation badges
# ─────────────────────────────────────────────────────────────────────────────

# Map remediation class to display label and style
REMEDIATION_DISPLAY: dict[str, tuple[str, str]] = {
    "auto-fixable": ("FIX", Style.BG_GREEN + Style.WHITE + Style.BOLD),
    "ai-candidate": ("AI", Style.BG_BLUE + Style.WHITE),
    "manual-review": ("MANUAL", Style.BG_MAGENTA + Style.WHITE),
}


def remediation_badge(classification: str) -> str:
    """Return a colored badge for a remediation classification.

    Args:
        classification: Remediation class (auto-fixable, ai-candidate, manual-review)

    Returns:
        Styled badge like " FIX " with colored background
    """
    classification_lower = classification.lower() if classification else "ai-candidate"
    label, styles = REMEDIATION_DISPLAY.get(classification_lower, ("?", Style.DIM))
    padded = f" {label} "
    return style(padded, styles)


# ─────────────────────────────────────────────────────────────────────────────
# Box drawing
# ─────────────────────────────────────────────────────────────────────────────

# Unicode box-drawing characters
BOX_TL = "┌"  # Top-left
BOX_TR = "┐"  # Top-right
BOX_BL = "└"  # Bottom-left
BOX_BR = "┘"  # Bottom-right
BOX_H = "─"  # Horizontal
BOX_V = "│"  # Vertical


def box(content: str, title: str = "", width: int | None = None) -> str:
    """Draw a Unicode box around content.

    Args:
        content: Text content (can be multiline)
        title: Optional title for top border
        width: Box width (auto-calculated if None)

    Returns:
        Boxed text with Unicode borders
    """
    lines = content.split("\n")

    # Calculate width: max of content lines, title, or specified width
    content_widths = [visible_width(line) for line in lines]
    auto_width = max(content_widths) if content_widths else 0
    if title:
        auto_width = max(auto_width, visible_width(title) + 4)
    inner_width = width if width else auto_width
    inner_width = max(inner_width, 10)  # Minimum width

    # Build box
    result = []

    # Top border with optional title
    if title:
        title_part = f" {title} "
        remaining = inner_width - visible_width(title_part)
        left_pad = remaining // 2
        right_pad = remaining - left_pad
        top = BOX_TL + BOX_H * left_pad + bold(title_part) + BOX_H * right_pad + BOX_TR
    else:
        top = BOX_TL + BOX_H * inner_width + BOX_TR
    result.append(top)

    # Content lines
    for line in lines:
        padded = ljust_ansi(line, inner_width)
        result.append(f"{BOX_V}{padded}{BOX_V}")

    # Bottom border
    result.append(BOX_BL + BOX_H * inner_width + BOX_BR)

    return "\n".join(result)


def section_header(title: str, width: int = 60, char: str = "─") -> str:
    """Create a section header with centered title.

    Args:
        title: Header title
        width: Total width
        char: Character for the line

    Returns:
        Header like "──── Title ────"
    """
    title_part = f" {title} "
    remaining = width - len(title_part)
    left_pad = remaining // 2
    right_pad = remaining - left_pad
    return char * left_pad + bold(title_part) + char * right_pad


# ─────────────────────────────────────────────────────────────────────────────
# Table formatting
# ─────────────────────────────────────────────────────────────────────────────


def table(
    headers: list[str],
    rows: list[list[str]],
    col_widths: list[int] | None = None,
    sep: str = "  ",
) -> str:
    """Format a simple columnar table.

    Args:
        headers: Column headers
        rows: List of rows (each row is list of cell values)
        col_widths: Optional column widths (auto-calculated if None)
        sep: Column separator

    Returns:
        Formatted table string
    """
    if not headers and not rows:
        return ""

    num_cols = len(headers) if headers else (len(rows[0]) if rows else 0)

    # Calculate column widths if not specified
    if col_widths is None:
        col_widths = [0] * num_cols
        for i, h in enumerate(headers):
            col_widths[i] = max(col_widths[i], visible_width(h))
        for row in rows:
            for i, cell in enumerate(row):
                if i < num_cols:
                    col_widths[i] = max(col_widths[i], visible_width(cell))

    lines = []

    # Header row
    if headers:
        header_cells = [ljust_ansi(bold(h), col_widths[i]) for i, h in enumerate(headers)]
        lines.append(sep.join(header_cells))
        # Underline
        underline_cells = [BOX_H * w for w in col_widths]
        lines.append(sep.join(underline_cells))

    # Data rows
    for row in rows:
        cells = []
        for i in range(num_cols):
            cell = row[i] if i < len(row) else ""
            cells.append(ljust_ansi(cell, col_widths[i]))
        lines.append(sep.join(cells))

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Tree connectors
# ─────────────────────────────────────────────────────────────────────────────

TREE_LAST = "└── "  # Last item in a level
TREE_MID = "├── "  # Middle item
TREE_PIPE = "│   "  # Continuation line
TREE_SPACE = "    "  # Empty continuation


def tree_prefix(is_last: bool, depth: int = 0, parent_prefixes: list[bool] | None = None) -> str:
    """Generate tree prefix for an item.

    Args:
        is_last: Whether this is the last item at this level
        depth: Nesting depth (0 = root)
        parent_prefixes: List of is_last values for parent levels

    Returns:
        Tree prefix like "│   ├── " or "    └── "
    """
    if depth == 0:
        return TREE_LAST if is_last else TREE_MID

    prefix_parts = []
    if parent_prefixes:
        for parent_is_last in parent_prefixes:
            prefix_parts.append(TREE_SPACE if parent_is_last else TREE_PIPE)

    prefix_parts.append(TREE_LAST if is_last else TREE_MID)
    return "".join(prefix_parts)
