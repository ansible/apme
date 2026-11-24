"""Tests for license resolution and open-source classification (ADR-040)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apme_engine.licensing import (
    _MAX_LICENSE_FILES,
    _UNVERIFIED_LICENSES,
    OPEN_SOURCE,
    PROPRIETARY,
    UNKNOWN,
    _is_compound_name,
    canonical_spdx,
    classify_license,
    detect_license_text,
    is_spdx_id,
    join_licenses,
    license_from_classifiers,
    license_from_collection_files,
    resolve_license,
    text_to_spdx,
)

# Realistic PEP 566/639 metadata for ansible-core 2.19.0: no License field, no
# License-Expression, only a Trove classifier.
ANSIBLE_CORE_CLASSIFIERS = [
    "Development Status :: 5 - Production/Stable",
    "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
    "Programming Language :: Python :: 3",
]

MIT_TEXT = """MIT License

Copyright (c) 2015 Nicolas JOUANIN

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software.
"""

BSD3_TEXT = """Copyright (c) 2013, Mozilla Foundation
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice.
2. Redistributions in binary form must reproduce the above copyright notice.
3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software.
"""

GPL3_TEXT = """GNU GENERAL PUBLIC LICENSE
Version 3, 29 June 2007

Everyone is permitted to copy and distribute verbatim copies of this license
document, but changing it is not allowed.

  This program is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
"""

# Excerpt of the real GPLv3 ``COPYING``: the title and preamble come first, the
# Affero GPL is only mentioned in section 13, and the "any later version"
# sentence is the example text of the "How to Apply" appendix at the end.
# Body-wide scanning therefore reported this file as AGPL-3.0-or-later.
GPL3_COPYING_TEXT = """GNU GENERAL PUBLIC LICENSE
Version 3, 29 June 2007

Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>
Everyone is permitted to copy and distribute verbatim copies of this license
document, but changing it is not allowed.

Preamble

The GNU General Public License is a free, copyleft license for software and
other kinds of works.

The licenses for most software and other practical works are designed to take
away your freedom to share and change the works.  By contrast, the GNU General
Public License is intended to guarantee your freedom to share and change all
versions of a program--to make sure it remains free software for all its users.
We, the Free Software Foundation, use the GNU General Public License for most
of our software; it applies also to any other work released this way by its
authors.  You can apply it to your programs, too.

When we speak of free software, we are referring to freedom, not price.  Our
General Public Licenses are designed to make sure that you have the freedom to
distribute copies of free software (and charge for them if you wish), that you
receive source code or can get it if you want it, that you can change the
software or use pieces of it in new free programs, and that you know you can do
those things.

TERMS AND CONDITIONS

 13. Use with the GNU Affero General Public License.

Notwithstanding any other provision of this License, you have permission to
link or combine any covered work with a work licensed under version 3 of the
GNU Affero General Public License into a single combined work.

How to Apply These Terms to Your New Programs

  This program is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
"""


class TestCanonicalSpdx:
    """SPDX canonicalization behaviour."""

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        ("raw", "expected"),
        [
            ("MIT", "MIT"),
            ("mit", "MIT"),
            ("apache-2.0", "Apache-2.0"),
            ("Apache-2.0 OR BSD-3-Clause", "Apache-2.0 OR BSD-3-Clause"),
            ("GPL-3.0-or-later", "GPL-3.0-or-later"),
            ("LicenseRef-Proprietary", "LicenseRef-Proprietary"),
        ],
    )
    def test_canonicalizes_valid_values(self, raw: str, expected: str) -> None:
        """Valid SPDX values are canonicalized.

        Args:
            raw: Input value.
            expected: Expected canonical value.
        """
        assert canonical_spdx(raw) == expected

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        "raw", ["", "ISC License", "BSD License", "Ansible Project", "UNKNOWN", "   "]
    )
    def test_rejects_non_spdx_values(self, raw: str) -> None:
        """Free-text license names are not valid SPDX expressions.

        Args:
            raw: Input value.
        """
        assert canonical_spdx(raw) == ""

    def test_is_spdx_id_distinguishes_expressions(self) -> None:
        """Only lone identifiers count as SPDX ids, not expressions."""
        assert is_spdx_id("MIT") is True
        assert is_spdx_id("MIT OR Apache-2.0") is False
        assert is_spdx_id("ISC License") is False

    def test_deprecated_plus_identifier_is_normalized(self) -> None:
        """``X+`` becomes ``X-or-later`` so it is recognised as open source.

        ``packaging`` keeps the deprecated spelling, which is neither in the
        open-source identifier set nor a CycloneDX identifier.
        """
        assert canonical_spdx("GPL-2.0+") == "GPL-2.0-or-later"
        assert canonical_spdx("GPL-3.0+") == "GPL-3.0-or-later"

    def test_is_spdx_id_rejects_grouping_parentheses(self) -> None:
        """``(MIT)`` canonicalizes with its parentheses and is not a bare id.

        CycloneDX ``license.id`` accepts SPDX identifiers only, so a
        parenthesized value has to travel as an ``expression`` instead.
        """
        assert canonical_spdx("(MIT)") == "(MIT)"
        assert is_spdx_id("(MIT)") is False
        assert is_spdx_id("(MIT OR Apache-2.0)") is False


class TestResolveLicense:
    """Package metadata license resolution (``License``/``License-Expression``)."""

    def test_ansible_core_classifier_only(self) -> None:
        """ansible-core publishes no License field; the classifier is used."""
        assert resolve_license(classifiers=ANSIBLE_CORE_CLASSIFIERS) == "GPL-3.0-or-later"

    def test_prefers_license_expression(self) -> None:
        """PEP 639 License-Expression wins over the deprecated License field."""
        assert (
            resolve_license(
                expression="Apache-2.0 OR BSD-3-Clause",
                text="BSD",
                classifiers=["License :: OSI Approved :: Apache Software License"],
            )
            == "Apache-2.0 OR BSD-3-Clause"
        )

    def test_license_field_with_spdx_id(self) -> None:
        """A License field that is already an SPDX id is kept (PyYAML: MIT)."""
        assert resolve_license(text="MIT") == "MIT"

    def test_license_field_with_free_text_name(self) -> None:
        """Resolvelib's ``License: ISC License`` maps to the ISC identifier."""
        assert resolve_license(text="ISC License") == "ISC"

    def test_license_field_with_full_text(self) -> None:
        """A License field holding the full license body is detected."""
        assert resolve_license(text=GPL3_TEXT) == "GPL-3.0-or-later"

    def test_ambiguous_name_is_preserved(self) -> None:
        """``BSD License`` has no unambiguous SPDX id but stays displayable."""
        assert resolve_license(text="BSD License") == "BSD License"

    def test_restrictive_name_is_not_mapped(self) -> None:
        """A license name with an explicit restriction is kept verbatim.

        ``Commercial Apache License version 2.0`` contains the Apache signature
        but is not Apache-2.0; mapping it would hide the commercial term.
        """
        assert resolve_license(text="Commercial Apache License version 2.0") == "Commercial Apache License version 2.0"

    def test_short_unrecognized_name_is_preserved(self) -> None:
        """Free text that is neither SPDX nor a marker stays visible."""
        assert resolve_license(text="Limited Use") == "Limited Use"

    def test_expression_beats_classifier(self) -> None:
        """Marker Safe's License-Expression is used instead of its classifier."""
        assert (
            resolve_license(
                expression="BSD-3-Clause",
                classifiers=["License :: OSI Approved :: BSD License"],
            )
            == "BSD-3-Clause"
        )

    def test_empty_metadata_returns_empty(self) -> None:
        """No metadata at all yields an empty license."""
        assert resolve_license() == ""

    def test_long_license_expression_is_kept(self) -> None:
        """A declared expression past the storage bound is still authoritative.

        ``License-Expression`` is machine-readable, so dropping a long but
        valid value would fall back to weaker metadata and hide the required
        ``SSPL-1.0`` component.
        """
        expression = "MIT AND " * 25 + "SSPL-1.0"

        resolved = resolve_license(expression=expression)

        assert resolved == expression
        assert classify_license(resolved) == PROPRIETARY

    def test_long_license_field_expression_is_kept(self) -> None:
        """A long expression in the legacy ``License`` field beats classifiers.

        Without the bounded parse the value is dropped at the storage limit and
        a permissive Trove classifier reports the package as open source.
        """
        expression = "MIT AND " * 25 + "SSPL-1.0"

        resolved = resolve_license(
            text=expression,
            classifiers=["License :: OSI Approved :: MIT License"],
        )

        assert resolved == expression
        assert classify_license(resolved) == PROPRIETARY

    def test_compound_license_field_keeps_every_operand(self) -> None:
        """A restricted operand survives resolution, not just classification.

        Name detection matches `apache license ... version 2.0` anywhere in the
        value, so resolving it would store `Apache-2.0` and drop `SSPL-1.0`
        before the compound evaluation ever sees it.
        """
        resolved = resolve_license(text="Apache License version 2.0 AND SSPL-1.0")

        assert resolved == "Apache License version 2.0 AND SSPL-1.0"
        assert classify_license(resolved) == PROPRIETARY

    def test_license_body_is_not_read_as_an_operator_list(self) -> None:
        """`TERMS AND CONDITIONS` in a body does not make it a list."""
        body = "GNU GENERAL PUBLIC LICENSE Version 3 TERMS AND CONDITIONS " + "x" * 200

        assert not _is_compound_name(body)
        assert classify_license(body) == OPEN_SOURCE

    def test_package_license_body_keeps_supplemental_restriction(self) -> None:
        """A restriction appended to a package `License` body is not dropped.

        The body is longer than the marker limit, so only the supplemental
        scan over a recognized body can keep the restriction.
        """
        body = (
            "Commons Clause License Condition v1.0\n\n"
            "MIT License\n\n"
            "Permission is hereby granted, free of charge, to any person obtaining a copy.\n"
        )

        resolved = resolve_license(text=body)

        assert resolved == "MIT with Commons Clause"
        assert classify_license(resolved) == PROPRIETARY

    def test_declared_reference_with_dot_suffix_is_not_a_file_name(self) -> None:
        """A custom reference must reach the SPDX policy, not the file-name guard."""
        assert classify_license("LicenseRef-Acme.EULA") == PROPRIETARY
        assert classify_license("MIT.txt") == UNKNOWN

    def test_expanded_expression_is_still_evaluated(self) -> None:
        """A canonical form that outgrows its declared text is still SPDX.

        `X+` becomes `X-or-later` during canonicalization, so a declared
        expression within the bound can be stored above it; treating the
        expansion as prose would report a dual-licensed value as proprietary.
        """
        declared = ("GPL-2.0+ OR " * 340) + "SSPL-1.0"

        resolved = resolve_license(expression=declared)

        assert len(resolved) > 4096
        assert classify_license(resolved) == OPEN_SOURCE

    def test_license_file_name_is_not_license_evidence(self) -> None:
        """An unverified `license_file` name stays unknown, not open source."""
        assert classify_license("MIT.txt") == UNKNOWN
        assert classify_license("LICENSE.mit") == UNKNOWN
        assert classify_license("MIT") == OPEN_SOURCE
        assert classify_license("Apache-2.0") == OPEN_SOURCE

    def test_license_body_is_not_parsed_as_an_expression(self) -> None:
        """A long ``License`` body is still detected as a body, not an id."""
        body = "Permission is hereby granted, free of charge, to any person obtaining a copy " * 5

        assert resolve_license(text=body) == "MIT"

    def test_invalid_license_expression_falls_back(self) -> None:
        """A malformed expression still lets ``License`` metadata decide."""
        assert resolve_license(expression="(MIT", text="ISC License") == "ISC"


class TestTextMapping:
    """Free-text and Trove classifier mapping."""

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        ("raw", "expected"),
        [
            ("MIT License", "MIT"),
            ("The MIT License (MIT)", "MIT"),
            ("Apache Software License", "Apache-2.0"),
            ("GNU General Public License v3 or later (GPLv3+)", "GPL-3.0-or-later"),
            ("ISC Licence", "ISC"),
            ("Python Software Foundation License", "PSF-2.0"),
        ],
    )
    def test_text_to_spdx(self, raw: str, expected: str) -> None:
        """Common free-text license names map to SPDX ids.

        Args:
            raw: Free-text license name.
            expected: Expected SPDX id.
        """
        assert text_to_spdx(raw) == expected

    def test_text_to_spdx_unknown(self) -> None:
        """Unrecognized names produce no id."""
        assert text_to_spdx("Same as Perl") == ""

    def test_classifiers_are_joined(self) -> None:
        """Multiple recognizable classifiers become an OR expression."""
        classifiers = [
            "License :: OSI Approved :: MIT License",
            "License :: OSI Approved :: Apache Software License",
        ]
        assert license_from_classifiers(classifiers) == "MIT OR Apache-2.0"

    def test_classifiers_ignored_when_absent(self) -> None:
        """A missing or malformed classifier list yields an empty result."""
        assert license_from_classifiers(None) == ""
        assert license_from_classifiers("License :: OSI Approved :: MIT License") == ""

    def test_ambiguous_classifier_reported_by_name(self) -> None:
        """``BSD License`` has no SPDX id but is still a usable license name.

        Jinja2 publishes only this classifier, so dropping it left the package
        at "Not recorded".
        """
        assert license_from_classifiers(["License :: OSI Approved :: BSD License"]) == "BSD License"

    def test_proprietary_classifier_reported_by_name(self) -> None:
        """The standard closed-source classifier is preserved, not dropped."""
        classifiers = ["License :: Other/Proprietary License"]
        assert license_from_classifiers(classifiers) == "Other/Proprietary License"
        assert resolve_license(classifiers=classifiers) == "Other/Proprietary License"
        assert classify_license("Other/Proprietary License") == PROPRIETARY

    def test_mapped_classifier_beats_display_name(self) -> None:
        """An SPDX-mappable classifier wins over an ambiguous sibling."""
        classifiers = [
            "License :: OSI Approved :: BSD License",
            "License :: OSI Approved :: Apache Software License",
        ]
        assert license_from_classifiers(classifiers) == "Apache-2.0"


class TestDetectLicenseText:
    """License body detection used for collection license files."""

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        ("body", "expected"),
        [
            (MIT_TEXT, "MIT"),
            (BSD3_TEXT, "BSD-3-Clause"),
            (GPL3_TEXT, "GPL-3.0-or-later"),
            ("GNU GENERAL PUBLIC LICENSE Version 2, June 1991", "GPL-2.0-only"),
            ("GNU LESSER GENERAL PUBLIC LICENSE Version 3", "LGPL-3.0-only"),
            ("GNU AFFERO GENERAL PUBLIC LICENSE Version 3", "AGPL-3.0-only"),
            ("Apache License\nVersion 2.0, January 2004", "Apache-2.0"),
            ("Mozilla Public License Version 2.0", "MPL-2.0"),
        ],
    )
    def test_detects_known_bodies(self, body: str, expected: str) -> None:
        """Known license bodies are detected.

        Args:
            body: License file content.
            expected: Expected SPDX id.
        """
        assert detect_license_text(body) == expected

    def test_full_gplv3_copying_is_gpl_not_agpl(self) -> None:
        """A GPLv3 body is GPLv3 even though it names the Affero GPL.

        Real-world regression: GPLv3 section 13 mentions the GNU Affero General
        Public License, and the "How to Apply" appendix contains "any later
        version", so body-wide matching labelled every GPLv3 ``COPYING`` as
        ``AGPL-3.0-or-later``.  A bare license body grants the version it
        states; ``-or-later`` has to come from metadata.
        """
        assert detect_license_text(GPL3_COPYING_TEXT) == "GPL-3.0-only"

    def test_unknown_body(self) -> None:
        """Unrecognized bodies produce no id."""
        assert detect_license_text("You may use this software for evaluation only.") == ""
        assert detect_license_text("") == ""


class TestJoinLicenses:
    """Combining multi-license metadata lists."""

    def test_spdx_ids_joined_with_or(self) -> None:
        """SPDX lists become a single OR expression (not a comma list)."""
        assert join_licenses(["GPL-3.0-or-later", "MIT"]) == "GPL-3.0-or-later OR MIT"

    def test_duplicates_removed(self) -> None:
        """Repeated ids collapse."""
        assert join_licenses(["MIT", "MIT "]) == "MIT"

    def test_mixed_values_are_joined_conjunctively(self) -> None:
        """A list that is not fully SPDX binds the consumer to every entry.

        Joining with commas would leave ````["MIT", "SSPL-1.0", "Custom
        terms"]`` as prose in which only the ``MIT`` hint is recognisable.
        """
        assert join_licenses(["GPL-3.0-or-later", "Custom Eval"]) == "GPL-3.0-or-later AND Custom Eval"

    def test_mixed_list_keeps_a_restricted_entry(self) -> None:
        """The conjunction classifies by its worst entry, not its first hint."""
        joined = join_licenses(["MIT", "SSPL-1.0", "Custom terms"])

        assert joined == "MIT AND SSPL-1.0 AND Custom terms"
        assert classify_license(joined) == PROPRIETARY

    def test_empty(self) -> None:
        """Empty input yields an empty string."""
        assert join_licenses([]) == ""
        assert join_licenses(["", "  "]) == ""


class TestClassifyLicense:
    """Open-source classification driving the red legal-risk indicator."""

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        "value",
        [
            "MIT",
            "Apache-2.0",
            "GPL-3.0-or-later",
            "Apache-2.0 OR BSD-3-Clause",
            "MIT OR SSPL-1.0",
            "GPL-3.0-or-later OR MIT OR BSD-2-Clause",
            "BSD License",
            "ISC License",
            "Python Software Foundation License",
            "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
            "CC0-1.0",
        ],
    )
    def test_open_source(self, value: str) -> None:
        """Known open-source licenses classify as open source.

        Args:
            value: License value.
        """
        assert classify_license(value) == OPEN_SOURCE

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        "value",
        [
            "LicenseRef-Proprietary",
            "LicenseRef-Acme-Commercial",
            "Proprietary",
            "All rights reserved",
            "Commercial license",
            "SSPL-1.0",
            "BUSL-1.1",
            "Elastic-2.0",
            "BSD-3-Clause AND SSPL-1.0",
            "Apache-2.0 AND LicenseRef-Acme-EULA",
        ],
    )
    def test_proprietary(self, value: str) -> None:
        """Non-open-source values are flagged for legal review.

        Args:
            value: License value.
        """
        assert classify_license(value) == PROPRIETARY

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        "value", ["", "   ", "Not recorded", "Same as Perl", "Custom"]
    )
    def test_unknown(self, value: str) -> None:
        """Missing or unrecognized licenses are neutral, not red.

        Args:
            value: License value.
        """
        assert classify_license(value) == UNKNOWN

    def test_license_body_with_copyright_boilerplate_is_open_source(self) -> None:
        """BSD text contains "All rights reserved" yet must not be flagged."""
        assert classify_license(BSD3_TEXT) == OPEN_SOURCE
        assert classify_license(MIT_TEXT) == OPEN_SOURCE

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        "value", ["Limited Use", "Limited Use License", "Acme Limited License", "Submitted to Acme"]
    )
    def test_abbreviations_do_not_match_inside_words(self, value: str) -> None:
        """Short hints match whole words only (``mit`` is not in "Limited").

        Args:
            value: License value.
        """
        assert classify_license(value) == UNKNOWN

    def test_restrictive_marker_beats_license_name(self) -> None:
        """An explicit restriction is not overwritten by an Apache signature."""
        assert classify_license("Commercial Apache License version 2.0") == PROPRIETARY
        assert classify_license("Commercial license") == PROPRIETARY

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        "value", ["MIT with Commons Clause", "MIT and Commons-Clause", "Commons Clause"]
    )
    def test_commons_clause_is_proprietary(self, value: str) -> None:
        """``MIT with Commons Clause`` is a restricted license, not MIT.

        The value is not a valid SPDX expression, so without an explicit marker
        the leading ``MIT`` hint would report the package as open source.

        Args:
            value: License value.
        """
        assert classify_license(value) == PROPRIETARY

    def test_gnu_classifier_name_is_not_split(self) -> None:
        """A recognized name containing "or later" is not read as a compound.

        ansible-core's classifier reports ``GNU General Public License v3 or
        later (GPLv3+)``; splitting it at ``or``/parentheses would leave an
        unknown ``later`` fragment and hide the license.
        """
        assert classify_license("GNU General Public License v3 or later (GPLv3+)") == OPEN_SOURCE

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        ("value", "expected"),
        [
            # An open-source OR alternative is usable as-is.
            ("MIT OR (Apache-2.0 AND SSPL-1.0)", OPEN_SOURCE),
            ("(MIT AND Apache-2.0) OR BUSL-1.1", OPEN_SOURCE),
            ("MIT AND SSPL-1.0 OR Apache-2.0", OPEN_SOURCE),
            # Required components decide an AND.
            ("Apache-2.0 AND SSPL-1.0", PROPRIETARY),
            ("MIT AND SSPL-1.0", PROPRIETARY),
            # An unclassified required component keeps the verdict neutral.
            ("MIT AND CC-BY-4.0", UNKNOWN),
            ("CC-BY-4.0 AND CC-BY-SA-4.0", UNKNOWN),
            # WITH keeps the base license's verdict.
            ("GPL-2.0-only WITH Classpath-exception-2.0", OPEN_SOURCE),
            ("SSPL-1.0 WITH LLVM-exception", PROPRIETARY),
        ],
    )
    def test_expression_operators_evaluated_per_branch(self, value: str, expected: str) -> None:
        """AND/OR/WITH are evaluated within their own branch.

        Args:
            value: SPDX expression.
            expected: Expected status.
        """
        assert classify_license(value) == expected

    def test_padded_expression_is_still_evaluated(self) -> None:
        """A long expression is parsed, not decided by a license-name hint.

        ``_MAX_EXPRESSION_LEN`` bounds what is worth storing as an expression;
        classification canonicalizes up to ``_MAX_STATUS_EXPRESSION_LEN`` so a
        value padded past the storage bound cannot degrade into a hint match and
        hide its restricted component.
        """
        value = "MIT AND " * 25 + "SSPL-1.0"

        assert len(value) > 200
        assert classify_license(value) == PROPRIETARY

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        ("value", "expected"),
        [
            # Not valid SPDX, but every operand is open source.
            ("Apache License, Version 2.0 or the MIT license", OPEN_SOURCE),
            ("BSD and MIT", OPEN_SOURCE),
            # An unknown operand is not evidence of an open-source license, even
            # when another operand is a recognised license *body* signature.
            ("MIT and FooBar-9.9", UNKNOWN),
            ("FooBar-9.9 AND MIT", UNKNOWN),
            ("Apache License, Version 2.0 and FooBar-9.9", UNKNOWN),
            # A restricted operand is caught by the marker list ...
            ("MIT and Commons Clause", PROPRIETARY),
            # ... and a custom reference is never overridden by a name hint.
            ("MIT License and LicenseRef-MIT", PROPRIETARY),
        ],
    )
    def test_compound_value_evaluated_operand_by_operand(self, value: str, expected: str) -> None:
        """A non-SPDX compound value is never decided by one open-source name.

        Args:
            value: Compound license value.
            expected: Expected status.
        """
        assert classify_license(value) == expected


class TestLicenseFromCollectionFiles:
    """Collection license recovery from on-disk license files."""

    def test_reuse_licenses_directory(self, tmp_path: Path) -> None:
        """community.general style: license lost, SPDX-named LICENSES/ present.

        ``LICENSES/`` lists the licenses used by files in the collection, not
        alternatives the consumer may pick, so the entries are joined with
        ``AND``: joining them with ``OR`` would let an open-source entry hide a
        source-available sibling and would tell SBOM consumers they may choose.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        licenses = tmp_path / "LICENSES"
        licenses.mkdir()
        for name in ("GPL-3.0-or-later.txt", "MIT.txt", "BSD-2-Clause.txt"):
            (licenses / name).write_text("placeholder\n")

        assert license_from_collection_files(tmp_path) == "BSD-2-Clause AND GPL-3.0-or-later AND MIT"

    def test_custom_license_in_licenses_directory_stays_proprietary(self, tmp_path: Path) -> None:
        """A ``LicenseRef-`` sibling is not hidden behind an open-source entry.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        licenses = tmp_path / "LICENSES"
        licenses.mkdir()
        (licenses / "LicenseRef-Acme-Commercial.txt").write_text("placeholder\n")
        (licenses / "MIT.txt").write_text("placeholder\n")

        value = license_from_collection_files(tmp_path)
        assert value == "LicenseRef-Acme-Commercial AND MIT"
        assert classify_license(value) == PROPRIETARY

    def test_license_file_with_spdx_name(self, tmp_path: Path) -> None:
        """A ``license_file`` that already is an SPDX id is used directly.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        (tmp_path / "GPL-3.0-or-later.txt").write_text("placeholder\n")
        assert license_from_collection_files(tmp_path, "GPL-3.0-or-later.txt") == "GPL-3.0-or-later"

    def test_missing_license_file_falls_through_to_licenses_directory(self, tmp_path: Path) -> None:
        """A declared but absent ``license_file`` is not trusted by its name.

        ``license_file: MIT.txt`` with no such file must not be reported as MIT
        while ``LICENSES/SSPL-1.0.txt`` next to it says otherwise — the SPDX
        stem is only used once the file is verified inside the collection.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        licenses = tmp_path / "LICENSES"
        licenses.mkdir()
        (licenses / "SSPL-1.0.txt").write_text("placeholder\n")

        assert license_from_collection_files(tmp_path, "MIT.txt") == "SSPL-1.0"

    def test_oversized_license_file_is_not_trusted(self, tmp_path: Path) -> None:
        """A file past the read limit is unverified, not a truncated grant.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        (tmp_path / "COPYING").write_text(
            "MIT License\n\nPermission is hereby granted, free of charge.\n"
            + "x" * 70000
            + "\nCommons Clause applies.\n"
        )

        value = license_from_collection_files(tmp_path, "COPYING")

        assert classify_license(value) == UNKNOWN

    def test_license_file_path_with_nul_byte_does_not_raise(self, tmp_path: Path) -> None:
        """A malformed path is unverified rather than an escaping exception.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        (tmp_path / "COPYING").write_text("Redistribution and use in source and binary forms.\n")

        value = license_from_collection_files(tmp_path, "bad\x00name")

        assert value == "bad\x00name"
        assert classify_license(value) == UNKNOWN

    def test_missing_license_file_name_is_unclassified(self, tmp_path: Path) -> None:
        """The fallback keeps the name for display but reports no verdict.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        (tmp_path / "COPYING").write_text("Redistribution and use in source and binary forms.\n")

        value = license_from_collection_files(tmp_path, "MIT.txt")

        assert value == "MIT.txt"
        assert classify_license(value) == UNKNOWN

    def test_unidentified_licenses_entry_is_unverified(self, tmp_path: Path) -> None:
        """A file the listing cannot identify makes the whole listing unverified.

        Skipping `Commercial License.txt` would leave a lone `MIT`, so the
        restricted sibling would disappear from the result.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        licenses = tmp_path / "LICENSES"
        licenses.mkdir()
        (licenses / "MIT.txt").write_text("placeholder\n")
        (licenses / "Commercial License.txt").write_text("placeholder\n")

        value = license_from_collection_files(tmp_path)

        assert value == _UNVERIFIED_LICENSES
        assert classify_license(value) == UNKNOWN

    def test_fully_identified_licenses_listing_is_joined(self, tmp_path: Path) -> None:
        """A listing of lone identifiers keeps the `AND` expression.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        licenses = tmp_path / "LICENSES"
        licenses.mkdir()
        (licenses / "MIT.txt").write_text("placeholder\n")
        (licenses / "Apache-2.0.txt").write_text("placeholder\n")

        assert license_from_collection_files(tmp_path) == "Apache-2.0 AND MIT"

    def test_expression_shaped_licenses_entry_is_unverified(self, tmp_path: Path) -> None:
        """A ``LICENSES/`` file name that is an expression is not trusted.

        ``MIT OR ISC.txt`` next to ``SSPL-1.0.txt`` would join to
        ``MIT OR ISC AND SSPL-1.0``, whose precedence makes the permissive
        alternative win even though the directory never said the licenses are
        alternatives.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        licenses = tmp_path / "LICENSES"
        licenses.mkdir()
        (licenses / "MIT OR ISC.txt").write_text("placeholder\n")
        (licenses / "SSPL-1.0.txt").write_text("placeholder\n")

        value = license_from_collection_files(tmp_path)

        assert value == _UNVERIFIED_LICENSES
        assert classify_license(value) == UNKNOWN

    def test_license_file_body_keeps_supplemental_restriction(self, tmp_path: Path) -> None:
        """A Commons Clause notice in a ``COPYING`` is not lost to the MIT grant.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        (tmp_path / "COPYING").write_text(
            "Commons Clause License Condition v1.0\n\n"
            "The license granted is limited to non-commercial use only.\n\n"
            "MIT License\n\n"
            "Permission is hereby granted, free of charge, to any person obtaining a copy.\n"
        )

        value = license_from_collection_files(tmp_path, "COPYING")

        assert value == "MIT with Commons Clause"
        assert classify_license(value) == PROPRIETARY

    def test_bsd_body_with_all_rights_reserved_stays_open_source(self, tmp_path: Path) -> None:
        """A body restriction scan must not trip over ordinary OSI wording.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        (tmp_path / "COPYING").write_text(
            "Redistribution and use in source and binary forms, with or without modification, "
            "are permitted provided that the following conditions are met. All rights reserved.\n"
        )

        value = license_from_collection_files(tmp_path, "COPYING")

        assert value.startswith("BSD-")
        assert classify_license(value) == OPEN_SOURCE

    def test_license_file_contents_detected(self, tmp_path: Path) -> None:
        """amazon.aws style: ``license_file: COPYING`` holding GPLv3 text.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        (tmp_path / "COPYING").write_text(GPL3_TEXT)
        assert license_from_collection_files(tmp_path, "COPYING") == "GPL-3.0-or-later"

    def test_license_file_name_kept_as_last_resort(self, tmp_path: Path) -> None:
        """An unrecognizable license file still names the file for the reader.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        (tmp_path / "COPYING").write_text("All rights reserved by Acme Corp.\n")
        assert license_from_collection_files(tmp_path, "COPYING") == "COPYING"

    def test_no_license_information(self, tmp_path: Path) -> None:
        """No license file and no LICENSES/ yields an empty license.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        assert license_from_collection_files(tmp_path) == ""

    def test_full_gplv3_copying_detected(self, tmp_path: Path) -> None:
        """A real GPLv3 ``COPYING`` is GPLv3, not AGPL.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        (tmp_path / "COPYING").write_text(GPL3_COPYING_TEXT)
        assert license_from_collection_files(tmp_path, "COPYING") == "GPL-3.0-only"

    def test_traversal_path_is_not_read(self, tmp_path: Path) -> None:
        """A ``license_file`` escaping the collection is never read.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        coll = tmp_path / "collection"
        coll.mkdir()
        (tmp_path / "secret.txt").write_text(GPL3_TEXT)

        assert license_from_collection_files(coll, "../secret.txt") == "../secret.txt"

    def test_absolute_path_is_not_read(self, tmp_path: Path) -> None:
        """An absolute ``license_file`` is never read.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        coll = tmp_path / "collection"
        coll.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text(GPL3_TEXT)

        assert license_from_collection_files(coll, str(secret)) == str(secret)

    def test_symlink_escaping_collection_is_not_followed(self, tmp_path: Path) -> None:
        """A symlinked license file pointing outside is not read.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        coll = tmp_path / "collection"
        coll.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text(GPL3_TEXT)
        (coll / "COPYING").symlink_to(outside)
        licenses = coll / "LICENSES"
        licenses.mkdir()
        (licenses / "MIT.txt").symlink_to(outside)

        # Neither the escaping ``COPYING`` nor the escaping ``LICENSES/`` entry
        # can be verified, so the collection stays unclassified instead of
        # falling back to a file name the scan could not read.
        assert license_from_collection_files(coll, "COPYING") == _UNVERIFIED_LICENSES
        assert license_from_collection_files(coll) == _UNVERIFIED_LICENSES

    def test_oversized_license_file_read_is_bounded(self, tmp_path: Path) -> None:
        """Only a bounded prefix is read, so a huge file cannot exhaust memory.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        (tmp_path / "COPYING").write_text("x" * (128 * 1024) + GPL3_TEXT)

        assert license_from_collection_files(tmp_path, "COPYING") == "COPYING"

    def test_unverifiable_licenses_directory_stays_unknown(self, tmp_path: Path) -> None:
        """An ``LICENSES/`` listing beyond the limit is not summarized.

        A truncated listing could drop the only restricted entry and leave an
        open-source sibling looking like the whole collection, so the result
        must not be an SPDX expression at all.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        licenses = tmp_path / "LICENSES"
        licenses.mkdir()
        for index in range(_MAX_LICENSE_FILES):
            (licenses / f"{index:02d}.txt").write_text("placeholder\n")
        (licenses / "MIT.txt").write_text("placeholder\n")
        (licenses / "SSPL-1.0.txt").write_text("placeholder\n")

        value = license_from_collection_files(tmp_path)

        assert value == _UNVERIFIED_LICENSES
        assert classify_license(value) == UNKNOWN
