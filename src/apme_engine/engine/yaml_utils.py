"""Utility helpers to simplify working with yaml-based data."""

# pylint: disable=too-many-lines
from __future__ import annotations

import re
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from re import Pattern
from typing import TYPE_CHECKING, cast

import ruamel.yaml.events
from ruamel.yaml.comments import CommentedMap, CommentedSeq, Format
from ruamel.yaml.composer import ComposerError
from ruamel.yaml.constructor import RoundTripConstructor
from ruamel.yaml.emitter import Emitter, ScalarAnalysis

# Module 'ruamel.yaml' does not explicitly export attribute 'YAML'; implicit reexport disabled
# To make the type checkers happy, we import from ruamel.yaml.main instead.
from ruamel.yaml.main import YAML
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scalarint import HexInt, ScalarInt

from . import logger
from .models import YAMLValue

if TYPE_CHECKING:
    # noinspection PyProtectedMember
    from ruamel.yaml.compat import StreamTextType
    from ruamel.yaml.nodes import ScalarNode
    from ruamel.yaml.representer import RoundTripRepresenter
    from ruamel.yaml.tokens import CommentToken


def _nested_items_path(
    data_collection: dict[object, object] | list[object],
    parent_path: list[str | int] | None = None,
) -> Iterator[tuple[object, object, list[str | int]]]:
    """Iterate a nested data structure, yielding key/index, value, and parent_path.

    Args:
        data_collection: Dict or list to walk recursively.
        parent_path: Accumulated path of keys/indices from the root.

    Yields:
        (object, object, list[str | int]): Tuples of (key_or_index, value, parent_path).
    """
    if data_collection is None:
        return
    if parent_path is None:
        parent_path = []
    if isinstance(data_collection, dict):
        items: Iterator[tuple[object, object]] = iter(data_collection.items())
    elif isinstance(data_collection, list):
        items = iter(enumerate(data_collection))
    else:
        return
    for key, value in items:
        yield key, value, parent_path
        if isinstance(value, dict | list):
            yield from _nested_items_path(value, [*parent_path, cast(str | int, key)])


class OctalIntYAML11(ScalarInt):  # type: ignore[misc]
    """OctalInt representation for YAML 1.1."""

    # tell mypy that ScalarInt has these attributes
    _width: object
    _underscore: object

    def __new__(cls: type[OctalIntYAML11], *args: object, **kwargs: object) -> OctalIntYAML11:
        """Create a new int with ScalarInt-defined attributes.

        Args:
            *args: Positional args for ScalarInt.__new__.
            **kwargs: Keyword args for ScalarInt.__new__.

        Returns:
            New OctalIntYAML11 instance.
        """
        return cast("OctalIntYAML11", ScalarInt.__new__(cls, *args, **kwargs))

    @staticmethod
    def represent_octal(representer: RoundTripRepresenter, data: OctalIntYAML11) -> object:
        """Return a YAML 1.1 octal representation.

        Based on ruamel.yaml.representer.RoundTripRepresenter.represent_octal_int()
        (which only handles the YAML 1.2 octal representation).

        Args:
            representer: RoundTripRepresenter instance.
            data: OctalIntYAML11 value to represent.

        Returns:
            Representer output (node) for the octal value.
        """
        v = format(data, "o")
        anchor = data.yaml_anchor(any=True)
        # noinspection PyProtectedMember
        return representer.insert_underscore(
            "0",
            v,
            data._underscore,  # noqa: SLF001
            anchor=anchor,
        )


class CustomConstructor(RoundTripConstructor):  # type: ignore[misc]
    """Custom YAML constructor that preserves Octal formatting in YAML 1.1."""

    def construct_yaml_int(self, node: ScalarNode) -> int | HexInt | OctalIntYAML11:
        """Construct int while preserving Octal formatting in YAML 1.1.

        ruamel.yaml only preserves the octal format for YAML 1.2.
        For 1.1, it converts the octal to an int. So, we preserve the format.

        Code partially copied from ruamel.yaml (MIT licensed).

        Args:
            node: ScalarNode for the integer value.

        Returns:
            int, HexInt, or OctalIntYAML11 depending on format.
        """
        ret = super().construct_yaml_int(node)
        if self.resolver.processing_version == (1, 1) and isinstance(ret, int):
            # Do not rewrite zero as octal.
            if ret == 0:
                return ret
            # see if we've got an octal we need to preserve.
            value_su = self.construct_scalar(node)
            try:
                v = value_su.rstrip("_")
                underscore: list[int | bool] | None = [len(v) - v.rindex("_") - 1, False, False]
            except ValueError:
                underscore = None
            except IndexError:
                underscore = None
            value_s = value_su.replace("_", "")
            if value_s[0] in "+-":
                value_s = value_s[1:]
            if value_s[0:2] == "0x":
                ret = HexInt(ret, width=len(value_s) - 2)
            elif value_s[0] == "0":
                # got an octal in YAML 1.1
                ret = OctalIntYAML11(
                    ret,
                    width=None,
                    underscore=underscore,
                    anchor=node.anchor,
                )
        return cast(int | HexInt | OctalIntYAML11, ret)


CustomConstructor.add_constructor(
    "tag:yaml.org,2002:int",
    CustomConstructor.construct_yaml_int,
)


class FormattedEmitter(Emitter):  # type: ignore[misc]
    """Emitter that applies custom formatting rules when dumping YAML.

    Differences from ruamel.yaml defaults:

      - indentation of root-level sequences
      - prefer double-quoted scalars over single-quoted scalars

    This ensures that root-level sequences are never indented.
    All subsequent levels are indented as configured (normal ruamel.yaml behavior).

    Earlier implementations used dedent on ruamel.yaml's dumped output,
    but string magic like that had a ton of problematic edge cases.

    Attributes:
        preferred_quote: Quote character to prefer for scalars.
        min_spaces_inside: Minimum spaces inside flow mappings.
        max_spaces_inside: Maximum spaces inside flow mappings.

    """

    preferred_quote = '"'  # either " or '

    min_spaces_inside = 0
    max_spaces_inside = 1

    _sequence_indent = 2
    _sequence_dash_offset = 0  # Should be _sequence_indent - 2
    _root_is_sequence = False

    _in_empty_flow_map = False

    @property
    def _is_root_level_sequence(self) -> bool:
        """Return True if this is a sequence at the root level of the yaml document."""
        return self.column < 2 and self._root_is_sequence

    def expect_document_root(self) -> None:
        """Expect doc root (extend to record if the root doc is a sequence)."""
        self._root_is_sequence = isinstance(
            self.event,
            ruamel.yaml.events.SequenceStartEvent,
        )
        super().expect_document_root()

    # NB: mypy does not support overriding attributes with properties yet:
    #     https://github.com/python/mypy/issues/4125
    #     To silence we have to ignore[override] both the @property and the method.

    @property
    def best_sequence_indent(self) -> int:
        """Return the configured sequence_indent or 2 for root level."""
        return 2 if self._is_root_level_sequence else self._sequence_indent

    @best_sequence_indent.setter
    def best_sequence_indent(self, value: int) -> None:
        """Configure how many columns to indent each sequence item (including the '-').

        Args:
            value: Number of columns to indent each sequence item.
        """
        self._sequence_indent = value

    @property
    def sequence_dash_offset(self) -> int:
        """Return the configured sequence_dash_offset or 0 for root level."""
        return 0 if self._is_root_level_sequence else self._sequence_dash_offset

    @sequence_dash_offset.setter
    def sequence_dash_offset(self, value: int) -> None:
        """Configure how many spaces to put before each sequence item's '-'.

        Args:
            value: Number of spaces before each sequence item's '-'.
        """
        self._sequence_dash_offset = value

    def choose_scalar_style(self) -> object:
        """Select how to quote scalars if needed.

        Returns:
            Scalar style string or None.
        """
        style = super().choose_scalar_style()
        if style == "" and self.event.value.startswith("0") and len(self.event.value) > 1:
            if (
                self.event.value.startswith("0x")
                and self.event.tag == "tag:yaml.org,2002:int"
                and self.event.implicit[0]
            ):
                self.event.tag = "tag:yaml.org,2002:str"
                return ""
            try:
                int(self.event.value, 8)
            except ValueError:
                pass
            else:
                self.event.tag = "tag:yaml.org,2002:str"
                self.event.implicit = (True, True, True)
                return '"'
        if style != "'":
            return style
        if '"' in self.event.value:
            return "'"
        return self.preferred_quote

    def increase_indent(
        self,
        flow: bool = False,  # noqa: FBT002
        sequence: bool | None = None,
        indentless: bool = False,  # noqa: FBT002
    ) -> None:
        """Increase indentation level for nested content.

        Args:
            flow: Whether in flow context.
            sequence: Whether this is a sequence context.
            indentless: Whether to use indentless style.
        """
        super().increase_indent(flow, sequence, indentless)
        # If our previous node was a sequence and we are still trying to indent, don't
        if self.indents.last_seq():
            self.indent = self.column + 1

    def write_indicator(
        self,
        indicator: str,  # ruamel.yaml typehint is wrong. This is a string.
        need_whitespace: bool,
        whitespace: bool = False,  # noqa: FBT002
        indention: bool = False,  # (sic) ruamel.yaml has this typo in their API # noqa: FBT002
    ) -> None:
        """Make sure that flow maps get whitespace by the curly braces.

        Args:
            indicator: YAML indicator character(s).
            need_whitespace: Whether whitespace is required.
            whitespace: Whether to add whitespace.
            indention: Whether to add indentation.
        """
        spaces_inside = min(
            max(1, self.min_spaces_inside),
            self.max_spaces_inside if self.max_spaces_inside != -1 else 1,
        )
        if indicator == "}" and (self.column or 0) > (self.indent or 0) and not self._in_empty_flow_map:
            indicator = (" " * spaces_inside) + "}"
        if indicator == "  -" and self.indents.last_seq():
            indicator = "-"
        super().write_indicator(indicator, need_whitespace, whitespace, indention)
        if indicator == "{" and self.column < self.best_width:
            if self.check_empty_mapping():
                self._in_empty_flow_map = True
            else:
                self.column += 1
                self.stream.write(" " * spaces_inside)
                self._in_empty_flow_map = False

    _re_repeat_blank_lines: Pattern[str] = re.compile(r"\n{3,}")

    @staticmethod
    def add_octothorpe_protection(string: str) -> str:
        """Modify strings to protect '#' from full-line-comment post-processing.

        Args:
            string: Input string to protect.

        Returns:
            String with '#' protected from comment detection.
        """
        try:
            if "#" in string:
                string = string.replace("#", "\uff03#\ufe5f")
        except (ValueError, TypeError):
            pass
        return string

    @staticmethod
    def drop_octothorpe_protection(string: str) -> str:
        """Remove string protection of '#' after full-line-comment post-processing.

        Args:
            string: Input string with protection to remove.

        Returns:
            String with '#' protection removed.
        """
        try:
            if "\uff03#\ufe5f" in string:
                string = string.replace("\uff03#\ufe5f", "#")
        except (ValueError, TypeError):
            pass
        return string

    def analyze_scalar(self, scalar: str) -> ScalarAnalysis:
        """Determine quoting and other requirements for string.

        And protect '#' from full-line-comment post-processing.

        Args:
            scalar: Scalar string to analyze.

        Returns:
            ScalarAnalysis with quoting and protection applied.
        """
        analysis: ScalarAnalysis = super().analyze_scalar(scalar)
        if analysis.empty:
            return analysis
        analysis.scalar = self.add_octothorpe_protection(analysis.scalar)
        return analysis

    def write_comment(
        self,
        comment: CommentToken,
        pre: bool = False,  # noqa: FBT002
    ) -> None:
        """Clean up extra new lines and spaces in comments.

        ruamel.yaml treats new or empty lines as comments.
        See: https://stackoverflow.com/questions/42708668/

        Args:
            comment: Comment token to write.
            pre: Whether this is a pre-comment.
        """
        value: str = comment.value
        if (
            pre
            and not value.strip()
            and not isinstance(
                self.event,
                ruamel.yaml.events.CollectionEndEvent
                | ruamel.yaml.events.DocumentEndEvent
                | ruamel.yaml.events.StreamEndEvent
                | ruamel.yaml.events.MappingStartEvent,
            )
        ):
            value = ""
        elif pre and not value.strip() and isinstance(self.event, ruamel.yaml.events.MappingStartEvent):
            value = self._re_repeat_blank_lines.sub("", value)
        elif pre:
            value = self._re_repeat_blank_lines.sub("\n", value)
        else:
            value = self._re_repeat_blank_lines.sub("\n\n", value)
        comment.value = value

        if comment.column > self.column + 1 and not pre:
            comment.column = self.column + 1

        super().write_comment(comment, pre)

    def write_version_directive(self, version_text: object) -> None:
        """Skip writing '%YAML 1.1'.

        Args:
            version_text: YAML version string (e.g. "1.1" or "1.2").
        """
        if version_text == "1.1":
            return
        super().write_version_directive(version_text)


# pylint: disable=too-many-instance-attributes
class FormattedYAML(YAML):  # type: ignore[misc]
    """A YAML loader/dumper that handles ansible content better by default.

    Attributes:
        default_config: Default formatting options for explicit_start, width, etc.
    """

    default_config = {
        "explicit_start": True,
        "explicit_end": False,
        "width": 160,
        "indent_sequences": True,
        "preferred_quote": '"',
        "min_spaces_inside": 0,
        "max_spaces_inside": 1,
    }

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        typ: str | None = None,
        pure: bool = False,
        output: object | None = None,
        plug_ins: list[str] | None = None,
        version: tuple[int, int] | None = None,
        config: dict[str, bool | int | str] | None = None,
    ):
        """Return a configured ``ruamel.yaml.YAML`` instance.

        ``ruamel.yaml.YAML`` uses attributes to configure how it dumps yaml files.
        Some of these settings can be confusing, so here are examples of how different
        settings will affect the dumped yaml.

        This example does not indent any sequences:

        .. code:: python

            yaml.explicit_start=True
            yaml.map_indent=2
            yaml.sequence_indent=2
            yaml.sequence_dash_offset=0

        .. code:: yaml

            ---
            - name: A playbook
              tasks:
              - name: Task

        This example indents all sequences including the root-level:

        .. code:: python

            yaml.explicit_start=True
            yaml.map_indent=2
            yaml.sequence_indent=4
            yaml.sequence_dash_offset=2
            # yaml.Emitter defaults to ruamel.yaml.emitter.Emitter

        .. code:: yaml

            ---
              - name: Playbook
                tasks:
                  - name: Task

        This example indents all sequences except at the root-level:

        .. code:: python

            yaml.explicit_start=True
            yaml.map_indent=2
            yaml.sequence_indent=4
            yaml.sequence_dash_offset=2
            yaml.Emitter = FormattedEmitter  # custom Emitter prevents root-level indents

        .. code:: yaml

            ---
            - name: Playbook
              tasks:
                - name: Task

        Args:
            typ: YAML type (e.g. "rt" for round-trip).
            pure: Use pure Python implementation.
            output: Output stream for dump.
            plug_ins: List of plugin module paths.
            version: YAML version tuple (e.g. (1, 1)).
            config: Override default formatting options.
        """
        if version:
            if isinstance(version, str):
                x, y = version.split(".", maxsplit=1)
                version = (int(x), int(y))
            self._yaml_version_default: tuple[int, int] = version
            self._yaml_version: tuple[int, int] = self._yaml_version_default
        super().__init__(typ=typ, pure=pure, output=output, plug_ins=plug_ins)

        # NB: We ignore some mypy issues because ruamel.yaml typehints are not great.

        if config is None:
            config = dict(self.default_config)  # type: ignore[arg-type]

        self.explicit_start = config["explicit_start"]
        self.explicit_end = config["explicit_end"]
        self.width = config["width"]
        indent_sequences: bool = cast(bool, config["indent_sequences"])
        preferred_quote: str = cast(str, config["preferred_quote"])

        min_spaces_inside: int = cast(int, config["min_spaces_inside"])
        max_spaces_inside: int = cast(int, config["max_spaces_inside"])

        self.default_flow_style = False
        self.compact_seq_seq = True  # dash after dash
        self.compact_seq_map = True  # key after dash

        # Do not use yaml.indent() as it obscures the purpose of these vars:
        self.map_indent = 2
        self.sequence_indent = 4 if indent_sequences else 2
        self.sequence_dash_offset = self.sequence_indent - 2

        # If someone doesn't want our FormattedEmitter, they can change it.
        self.Emitter = FormattedEmitter

        if preferred_quote in ['"', "'"]:
            FormattedEmitter.preferred_quote = preferred_quote
        # NB: default_style affects preferred_quote as well.
        # self.default_style ∈ None (default), '', '"', "'", '|', '>'

        # spaces inside braces for flow mappings
        FormattedEmitter.min_spaces_inside = min_spaces_inside
        FormattedEmitter.max_spaces_inside = max_spaces_inside

        # We need a custom constructor to preserve Octal formatting in YAML 1.1
        self.Constructor = CustomConstructor
        self.Representer.add_representer(OctalIntYAML11, OctalIntYAML11.represent_octal)

    @property
    def version(self) -> tuple[int, int] | None:
        """Return the YAML version used to parse or dump.

        Ansible uses PyYAML which only supports YAML 1.1. ruamel.yaml defaults to 1.2.
        So, we have to make sure we dump yaml files using YAML 1.1.
        We can relax the version requirement once ansible uses a version of PyYAML
        that includes this PR: https://github.com/yaml/pyyaml/pull/555
        """
        if hasattr(self, "_yaml_version"):
            return self._yaml_version
        return None

    @version.setter
    def version(self, value: tuple[int, int] | None) -> None:
        """Ensure that yaml version uses our default value.

        The yaml Reader updates this value based on the ``%YAML`` directive in files.
        So, if a file does not include the directive, it sets this to None.
        But, None effectively resets the parsing version to YAML 1.2 (ruamel's default).

        Args:
            value: YAML version tuple (e.g. (1, 1)) or None to use default.
        """
        if value is not None:
            self._yaml_version = value
        elif hasattr(self, "_yaml_version_default"):
            self._yaml_version = self._yaml_version_default
        # We do nothing if the object did not have a previous default version defined

    def load(self, stream: Path | StreamTextType) -> YAMLValue | None:
        """Load YAML content from a string while avoiding known ruamel.yaml issues.

        Handles ComposerError by falling back to load_all. Preserves preamble
        comments. Path input is not supported (raises NotImplementedError).

        Args:
            stream: String or Path to load from. Only str supported.

        Returns:
            Parsed YAML value, or None for invalid/empty documents.

        Raises:
            NotImplementedError: If stream is not a str.
        """
        if not isinstance(stream, str):
            msg = f"expected a str but got {type(stream)}"
            raise NotImplementedError(msg)
        # As ruamel drops comments for any document that is not a mapping or sequence,
        # we need to avoid using it to reformat those documents.
        # https://sourceforge.net/p/ruamel-yaml/tickets/460/

        text, preamble_comment = self._pre_process_yaml(stream)
        try:
            data = super().load(stream=text)
        except ComposerError:
            data = self.load_all(stream=text)
        except ParserError as ex:
            data = None
            logger.error("Invalid yaml, verify the file contents and try again. %s", ex)  # noqa: TRY400
        except Exception as ex:
            data = None
            logger.error("YAML load error: %s", ex)  # noqa: TRY400
        if preamble_comment is not None and isinstance(
            data,
            CommentedMap | CommentedSeq,
        ):
            data.preamble_comment = preamble_comment
        # Because data can validly also be None for empty documents, we cannot
        # really annotate the return type here, so we need to remember to
        # never save None or scalar data types when reformatting.
        return cast(YAMLValue | None, data)

    def _prevent_wrapping_flow_style(self, data: YAMLValue) -> None:
        """Walk data and convert flow-style maps to block if they would exceed width.

        Args:
            data: YAML value (map or seq) to process in place.
        """
        if not isinstance(data, CommentedMap | CommentedSeq):
            return
        for key, value, parent_path in _nested_items_path(data):
            if not isinstance(value, CommentedMap | CommentedSeq):
                continue
            fa: Format = value.fa
            if fa.flow_style():
                predicted_indent = self._predict_indent_length(parent_path, key)
                predicted_width = len(str(value))
                if predicted_indent + predicted_width > int(self.width or 0):
                    fa.set_block_style()

    def _predict_indent_length(self, parent_path: list[str | int], key: object) -> int:
        """Predict how many columns a value will be indented at a given path.

        Args:
            parent_path: List of keys/indices from root to the parent.
            key: Key or index of the current value.

        Returns:
            Predicted indentation column count.
        """
        indent = 0
        seq_indent = self.sequence_indent
        map_indent = self.map_indent
        for parent_key in parent_path:
            if isinstance(parent_key, int) and indent == 0:
                indent += self.sequence_dash_offset
            elif isinstance(parent_key, int):
                indent += seq_indent
            elif isinstance(parent_key, str):
                indent += map_indent

        if isinstance(key, int) and indent == 0:
            indent += self.sequence_dash_offset
        elif isinstance(key, int) and indent > 0:
            indent += seq_indent
        elif isinstance(key, str):
            indent += len(key + ": ")

        return indent

    def dumps(self, data: YAMLValue) -> str:
        """Dump YAML document to string (including its preamble_comment).

        Args:
            data: YAML value to serialize.

        Returns:
            YAML string with preamble comment if present.
        """
        preamble_comment: str | None = getattr(data, "preamble_comment", None)
        self._prevent_wrapping_flow_style(data)
        with StringIO() as stream:
            if preamble_comment:
                stream.write(preamble_comment)
            self.dump(data, stream)
            text = stream.getvalue()
        strip_version_directive = hasattr(self, "_yaml_version_default")
        return self._post_process_yaml(
            text,
            strip_version_directive=strip_version_directive,
            strip_explicit_start=not self.explicit_start,
        )

    # ruamel.yaml only preserves empty (no whitespace) blank lines
    # (ie "/n/n" becomes "/n/n" but "/n  /n" becomes "/n").
    # So, we need to identify whitespace-only lines to drop spaces before reading.
    _whitespace_only_lines_re = re.compile(r"^ +$", re.MULTILINE)

    def _pre_process_yaml(self, text: str) -> tuple[str, str | None]:
        """Handle known issues with ruamel.yaml loading.

        Preserve blank lines despite extra whitespace.
        Preserve any preamble (aka header) comments before "---".

        For more on preamble comments, see:
        https://stackoverflow.com/questions/70286108/python-ruamel-yaml-package-how-to-get-header-comment-lines/70287507#70287507

        Args:
            text: Raw YAML text to preprocess.

        Returns:
            Tuple of (processed text, preamble comment string or None).
        """
        text = self._whitespace_only_lines_re.sub("", text)

        # I investigated extending ruamel.yaml to capture preamble comments.
        #   preamble comment goes from:
        #     DocumentStartToken.comment -> DocumentStartEvent.comment
        #   Then, in the composer:
        #     once in composer.current_event
        #         discards DocumentStartEvent
        #           move DocumentStartEvent to composer.last_event
        #             all document nodes get composed (events get used)
        #         discard DocumentEndEvent
        #           move DocumentEndEvent to composer.last_event
        # So, there's no convenient way to extend the composer
        # to somehow capture the comments and pass them on.

        preamble_comments = []
        if "\n---\n" not in text and "\n--- " not in text:
            # nothing is before the document start mark,
            # so there are no comments to preserve.
            return text, None
        for line in text.splitlines(True):
            # We only need to capture the preamble comments. No need to remove them.
            # lines might also include directives.
            if line.lstrip().startswith("#") or line == "\n":
                preamble_comments.append(line)
            elif line.startswith("---"):
                break

        return text, "".join(preamble_comments) or None

    @staticmethod
    def _post_process_yaml(
        text: str,
        *,
        strip_version_directive: bool = False,
        strip_explicit_start: bool = False,
    ) -> str:
        """Handle known issues with ruamel.yaml dumping.

        Make sure there's only one newline at the end of the file.

        Fix the indent of full-line comments to match the indent of the next line.
        See: https://stackoverflow.com/questions/71354698/how-can-i-use-the-ruamel-yaml-rtsc-mode/71355688#71355688
        Also, removes "#" protection from strings that prevents them from being
        identified as full line comments in post-processing.

        Make sure null list items don't end in a space.

        Args:
            text: Dumped YAML text to postprocess.
            strip_version_directive: If True, remove %YAML directive from start.
            strip_explicit_start: If True, remove --- document start marker.

        Returns:
            Postprocessed YAML string.
        """
        # remove YAML directive
        if strip_version_directive and text.startswith("%YAML"):
            text = text.split("\n", 1)[1]

        if strip_explicit_start and text.startswith("---"):
            text = text.split("\n", 1)[1]

        text = text.rstrip("\n") + "\n"

        lines = text.splitlines(keepends=True)
        full_line_comments: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if not stripped:
                # blank line. Move on.
                continue

            space_length = len(line) - len(stripped)

            if stripped.startswith("#"):
                # got a full line comment

                # allow some full line comments to match the previous indent
                if i > 0 and not full_line_comments and space_length:
                    prev = lines[i - 1]
                    prev_space_length = len(prev) - len(prev.lstrip())
                    if prev_space_length == space_length:
                        # if the indent matches the previous line's indent, skip it.
                        continue

                full_line_comments.append((i, stripped))
            elif full_line_comments:
                # end of full line comments so adjust to match indent of this line
                spaces = " " * space_length
                for index, comment in full_line_comments:
                    lines[index] = spaces + comment
                full_line_comments.clear()

            cleaned = line.strip()
            if not cleaned.startswith("#") and cleaned.endswith("-"):
                # got an empty list item. drop any trailing spaces.
                lines[i] = line.rstrip() + "\n"

        text = "".join(FormattedEmitter.drop_octothorpe_protection(line) for line in lines)
        return text
