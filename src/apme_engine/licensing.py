"""License normalization and open-source classification (ADR-040 follow-up).

Dependency manifests draw license data from heterogeneous metadata:

* Python packages — PEP 566/639 core metadata: the deprecated free-text
  ``License`` field, the PEP 639 ``License-Expression`` field, and Trove
  ``Classifier: License :: ...`` entries.
* Ansible collections — ``MANIFEST.json`` / ``galaxy.yml``: a ``license``
  list, or (REUSE-style, most of the ansible package today) only a
  ``license_file`` plus a ``LICENSES/`` directory.

Reading only ``License`` and the collection ``license`` list left most modern
dependencies reported as "Not recorded": ansible-core ships neither
``License`` nor ``License-Expression`` (only a classifier), and collections
such as ``community.general`` publish ``license: []`` with
``license_file: COPYING``.  This module centralises the resolution rules so
the engine (extraction), the gateway (REST API and SBOM) and the UI agree:

* :func:`resolve_license` — best-effort SPDX identifier or expression.
* :func:`classify_license` — three-way legal-risk classification; the UI
  flags :data:`PROPRIETARY` in red so a non-open-source dependency cannot be
  adopted unnoticed.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Final, Literal

from packaging.licenses import InvalidLicenseExpression, canonicalize_license_expression

LicenseStatus = Literal["open-source", "proprietary", "unknown"]

OPEN_SOURCE: Final[LicenseStatus] = "open-source"
PROPRIETARY: Final[LicenseStatus] = "proprietary"
UNKNOWN: Final[LicenseStatus] = "unknown"

#: Prefix of a license reference that is not an SPDX license-list identifier.
CUSTOM_LICENSE_PREFIX: Final = "LicenseRef-"

#: Deprecated ``X+`` identifier suffix, normalized to ``X-or-later``
#: canonicalization: neither the open-source identifier set nor SBOM consumers
#: recognise the deprecated spelling.
_DEPRECATED_PLUS_RE: Final[re.Pattern[str]] = re.compile(r"(?<![\w.+-])([A-Za-z][A-Za-z0-9.\-]*)\+")

#: Maximum length of a metadata value still treated as a license *name*.
#: Longer values are either license bodies (detected below) or noise that
#: must not be rendered as an identifier.
_MAX_NAME_LEN: Final = 60

#: Maximum length of a value still inspected for several licenses separated by
#: operators.  Longer values are license bodies: their prose contains "and"
#: and "or", and splitting them would discard the body signature.
_MAX_COMPOUND_NAME_LEN: Final = 120

#: Maximum length of an SPDX expression we are willing to canonicalize.
_MAX_EXPRESSION_LEN: Final = 200

#: Maximum length of a value canonicalized for *classification*.  Larger than
#: :data:`_MAX_EXPRESSION_LEN` on purpose: that limit decides what is worth
#: storing as an expression, whereas classification must still parse a long
#: compound value — otherwise a padded value such as
#: ``"MIT AND " * 25 + "SSPL-1.0"`` would fall through to license-name hints
#: and lose the restricted component.
_MAX_STATUS_EXPRESSION_LEN: Final = 4096

#: Maximum length of a canonical expression evaluated for status.  A canonical
#: form can outgrow its declared text, because a deprecated ``X+`` is rewritten
#: to ``X-or-later``: the worst case adds 8 characters per operand, and no
#: operand takes fewer than 7 characters including its operator, so the
#: canonical form is below twice the declared bound.
_MAX_CANONICAL_EXPRESSION_LEN: Final = _MAX_STATUS_EXPRESSION_LEN * 3

#: Maximum length of a value still checked for proprietary *markers*.  Only
#: short values are license names; a license body may legitimately mention
#: "commercial" (e.g. the BSD wording "All rights reserved") without being
#: proprietary.
_MAX_MARKER_LEN: Final = 80

#: Window (in characters) at the start of a license file searched for the
#: license *title*.  Body-wide matching mislabels a GPLv3 ``COPYING`` as AGPL:
#: GPLv3 section 13 mentions the Affero GPL, and its "How to Apply" appendix
#: always contains "any later version".
_TITLE_WINDOW: Final = 1000

#: Maximum number of ``LICENSES/`` entries examined, so a collection cannot
#: make the scan worker walk an unbounded directory.
_MAX_LICENSE_FILES: Final = 32

#: Maximum number of bytes read from one license file for detection.
_MAX_LICENSE_FILE_BYTES: Final = 64 * 1024

#: SPDX identifiers approved by the Open Source Initiative.  Deliberately
#: curated: an unrecognised identifier classifies as ``unknown`` (neutral in
#: the UI) rather than being falsely reported as proprietary.
_OSI_APPROVED: Final[frozenset[str]] = frozenset(
    {
        "0BSD",
        "AFL-3.0",
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
        "Apache-1.1",
        "Apache-2.0",
        "APL-1.0",
        "Artistic-1.0",
        "Artistic-1.0-Perl",
        "Artistic-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "BSD-3-Clause-Clear",
        "BSD-4-Clause",
        "BSL-1.0",
        "CDDL-1.0",
        "CDDL-1.1",
        "CPL-1.0",
        "ECL-2.0",
        "EPL-1.0",
        "EPL-2.0",
        "EUPL-1.1",
        "EUPL-1.2",
        "GPL-1.0-only",
        "GPL-1.0-or-later",
        "GPL-2.0-only",
        "GPL-2.0-or-later",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "IPL-1.0",
        "ISC",
        "LGPL-2.0-only",
        "LGPL-2.0-or-later",
        "LGPL-2.1-only",
        "LGPL-2.1-or-later",
        "LGPL-3.0-only",
        "LGPL-3.0-or-later",
        "MIT",
        "MIT-0",
        "MPL-1.0",
        "MPL-1.1",
        "MPL-2.0",
        "MPL-2.0-no-copyleft-exception",
        "MS-PL",
        "MS-RL",
        "MulanPSL-2.0",
        "NCSA",
        "NTP",
        "OFL-1.1",
        "OpenSSL",
        "PHP-3.01",
        "PostgreSQL",
        "PSF-2.0",
        "Python-2.0",
        "QPL-1.0",
        "Ruby",
        "Sleepycat",
        "UCL-1.0",
        "Unlicense",
        "UPL-1.0",
        "Vim",
        "W3C",
        "X11",
        "Zlib",
        "ZPL-2.0",
        "ZPL-2.1",
    }
)

#: Non-OSI licenses that are nonetheless free/open for adoption purposes
#: (public-domain dedications).  Never flagged red.
_PUBLIC_DOMAIN_LIKE: Final[frozenset[str]] = frozenset({"CC0-1.0", "WTFPL"})

#: SPDX identifiers that are known *not* to be open source.  Anything else
#: unrecognised stays ``unknown`` — only positive evidence turns the UI red.
_NON_OPEN_SOURCE_IDS: Final[frozenset[str]] = frozenset(
    {
        "BUSL-1.1",
        "Commons-Clause",
        "Elastic-2.0",
        "JSON",
        "PolyForm-Free-Trial-1.0.0",
        "PolyForm-Internal-Use-1.0.0",
        "PolyForm-Noncommercial-1.0.0",
        "PolyForm-Shield-1.0.0",
        "PolyForm-Small-Business-1.0.0",
        "SSPL-1.0",
    }
)

#: Substrings that identify a proprietary / closed license in short metadata
#: values.  Applied only to values short enough to be a license *name*.
_PROPRIETARY_MARKERS: Final[tuple[str, ...]] = (
    "proprietary",
    "commercial",
    "commons clause",
    "commons-clause",
    "all rights reserved",
    "closed source",
    "confidential",
    "end user license agreement",
    "eula",
    "internal use only",
    "not for redistribution",
    "non-commercial",
    "noncommercial",
)

#: Restrictions that are appended to a base license instead of being a license
#: of their own, mapped to the display form kept in the resolved value.
#: Only these are searched for in a license *body*; the generic markers above
#: cannot be used there because ordinary open-source wording contains them.
_SUPPLEMENTAL_RESTRICTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("commons clause", "Commons Clause"),
    ("commons-clause", "Commons Clause"),
)

#: Free-text spellings of well-known licenses, keyed by a normalized form
#: (lowercase, single spaces, no trailing period).
_TEXT_TO_SPDX: Final[dict[str, str]] = {
    "apache 2.0": "Apache-2.0",
    "apache license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "apache software license 2.0": "Apache-2.0",
    "bsd 2-clause": "BSD-2-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "gnu affero general public license v3": "AGPL-3.0-only",
    "gnu affero general public license v3 or later (agplv3+)": "AGPL-3.0-or-later",
    "gnu general public license v2 (gplv2)": "GPL-2.0-only",
    "gnu general public license v2 or later (gplv2+)": "GPL-2.0-or-later",
    "gnu general public license v3 (gplv3)": "GPL-3.0-only",
    "gnu general public license v3 or later (gplv3+)": "GPL-3.0-or-later",
    "gnu lesser general public license v2 (lgplv2)": "LGPL-2.0-only",
    "gnu lesser general public license v2 or later (lgplv2+)": "LGPL-2.0-or-later",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0-only",
    "gnu lesser general public license v3 or later (lgplv3+)": "LGPL-3.0-or-later",
    "isc license": "ISC",
    "isc license (iscl)": "ISC",
    "isc licence": "ISC",
    "lgpl-2.0+": "LGPL-2.0-or-later",
    "lgpl-3.0+": "LGPL-3.0-or-later",
    "mit license": "MIT",
    "mit licence": "MIT",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "psf": "PSF-2.0",
    "psfl": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "the mit license (mit)": "MIT",
    "the unlicense (unlicense)": "Unlicense",
    "unlicense": "Unlicense",
}

#: Trove classifiers (``License :: ...``) that map unambiguously to an SPDX id.
#: Ambiguous classifiers ("BSD License") are intentionally absent: they still
#: classify as open source, but are reported by their human-readable name.
_TROVE_TO_SPDX: Final[dict[str, str]] = {
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: Boost Software License 1.0 (BSL-1.0)": "BSL-1.0",
    "License :: OSI Approved :: Eclipse Public License 2.0 (EPL-2.0)": "EPL-2.0",
    "License :: OSI Approved :: GNU Affero General Public License v3": "AGPL-3.0-only",
    "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)": "AGPL-3.0-or-later",
    "License :: OSI Approved :: GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)": "GPL-2.0-or-later",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)": "GPL-3.0-or-later",
    "License :: OSI Approved :: GNU Lesser General Public License v2 (LGPLv2)": "LGPL-2.0-only",
    "License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.0-or-later",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: The Unlicense (Unlicense)": "Unlicense",
    "License :: OSI Approved :: zlib/libpng License": "Zlib",
}

_OSI_CLASSIFIER_PREFIX: Final = "license :: osi approved ::"

_OPEN_SOURCE_HINTS: Final[tuple[str, ...]] = (
    "apache",
    "artistic",
    "bsd",
    "cc0",
    "eclipse public license",
    "eupl",
    "gpl",
    "isc",
    "lgpl",
    "mit",
    "mozilla public license",
    "mpl",
    "public domain",
    "python software foundation",
    "unlicense",
    "zlib",
)


def _collapse(value: str) -> str:
    """Collapse whitespace in a metadata value.

    Args:
        value: Raw metadata string.

    Returns:
        Single-space separated text with surrounding whitespace removed.
    """
    return " ".join(value.split()).strip()


def canonical_spdx(value: str) -> str:
    """Canonicalize an SPDX identifier or expression.

    Args:
        value: Candidate SPDX string, e.g. ``mit`` or ``Apache-2.0 OR MIT``.

    Returns:
        Canonical SPDX string (e.g. ``MIT``), or ``""`` when *value* is not a
        valid SPDX identifier/expression.
    """
    text = _collapse(value)
    if not text or len(text) > _MAX_EXPRESSION_LEN:
        return ""
    return _canonicalize(text)


def _canonicalize(text: str) -> str:
    """Canonicalize already-collapsed SPDX text, without a length bound.

    Deprecated ``X+`` identifiers are rewritten to their ``X-or-later``
    spelling: ``packaging`` keeps the deprecated spelling, and neither the
    open-source identifier set nor SBOM consumers recognise ``GPL-2.0+``.

    Args:
        text: Collapsed candidate expression.

    Returns:
        Canonical SPDX string, or ``""`` when *text* is not valid SPDX.
    """
    try:
        canonical = str(canonicalize_license_expression(text))
    except (InvalidLicenseExpression, ValueError):
        return ""
    return _normalize_or_later(canonical)


def _normalize_or_later(expression: str) -> str:
    """Rewrite deprecated ``X+`` identifiers as ``X-or-later``.

    ``LicenseRef-*`` values are left untouched: they are not SPDX license
    identifiers, so the ``+`` suffix carries no ``-or-later`` meaning.

    Args:
        expression: Canonical SPDX expression.

    Returns:
        Expression with every deprecated ``X+`` identifier normalized.
    """

    def replace(match: re.Match[str]) -> str:
        identifier = match.group(1)
        if identifier.startswith(CUSTOM_LICENSE_PREFIX):
            return match.group(0)
        return f"{identifier}-or-later"

    return _DEPRECATED_PLUS_RE.sub(replace, expression)


def _canonical_declared_expression(value: str) -> str:
    """Canonicalize a declared SPDX expression up to the classification bound.

    Used for the authoritative PEP 639 ``License-Expression`` field: a value
    that is a valid expression but longer than ``_MAX_EXPRESSION_LEN`` still
    describes the package's licensing, so discarding it would let the metadata
    fall back to a weaker source and hide a restricted component.

    Args:
        value: Declared ``License-Expression`` metadata value.

    Returns:
        Canonical SPDX expression, or ``""`` when unusable.
    """
    text = _collapse(value)
    if not text or len(text) > _MAX_STATUS_EXPRESSION_LEN:
        return ""
    return _canonicalize(text)


def is_spdx_id(value: str) -> bool:
    """Return whether *value* is a single SPDX identifier.

    A lone identifier contains no operators and no grouping parentheses.
    ``canonicalize_license_expression("(MIT)")`` keeps the parentheses, so a
    parenthesized value must not be reported as an identifier: consumers such
    as CycloneDX ``license.id`` only accept bare identifiers and would reject
    it.

    Args:
        value: SPDX candidate string.

    Returns:
        True when the value canonicalizes to a lone identifier (no operators,
        no grouping parentheses).
    """
    canonical = canonical_spdx(value)
    return bool(canonical) and not any(char in canonical for char in " ()")


#: Identifiers treated as open source for classification purposes: OSI
#: approved licenses plus public-domain dedications.
_OPEN_SOURCE_IDS: Final[frozenset[str]] = _OSI_APPROVED | _PUBLIC_DOMAIN_LIKE

#: One hint per alternative, bounded on both sides so a short abbreviation
#: cannot match inside an unrelated word: ``mit`` must not match ``Limited``
#: (which would report ``Limited Use License`` as open source).
_OPEN_SOURCE_HINT_RE: Final[re.Pattern[str]] = re.compile(
    "|".join(rf"(?<![a-z]){re.escape(hint)}(?:[- ]?v?\d+(?:\.\d+)?\+?)?(?![a-z])" for hint in _OPEN_SOURCE_HINTS)
)


def text_to_spdx(value: str) -> str:
    """Map a free-text license name to an SPDX identifier.

    Args:
        value: Free-text license name, e.g. ``"ISC License"``.

    Returns:
        SPDX identifier, or ``""`` when the text is not a recognized name.
    """
    key = _collapse(value).lower().rstrip(".")
    direct = _TEXT_TO_SPDX.get(key)
    if direct:
        return direct
    lowered = key.replace("  ", " ")
    for classifier, spdx_id in _TROVE_TO_SPDX.items():
        suffix = classifier.split(" :: ")[-1].lower()
        if lowered == suffix:
            return spdx_id
    return ""


def detect_license_text(body: str) -> str:
    """Identify a license from the body of a license file.

    Used for collections that ship only ``license_file`` / ``LICENSES/``
    (REUSE style) instead of a ``license`` list in ``MANIFEST.json``.

    The GNU family is matched on its *title* near the start of the document,
    never on mentions further down: the GPLv3 text names the Affero GPL in
    section 13, so a body-wide search reports a GPLv3 ``COPYING`` as
    ``AGPL-3.0-or-later``.  For the same reason the ``-or-later`` variant is
    only returned when the grant is stated in that title window; the "any
    later version" sentence in the "How to Apply" appendix is template text
    and says nothing about the licensed work.  A bare license body therefore
    yields the ``-only`` variant.

    Args:
        body: Contents of the license file (or a long ``License`` value).

    Returns:
        SPDX identifier, or ``""`` when the body is not recognized.
    """
    text = _collapse(body)
    if not text:
        return ""
    lowered = text.lower()
    title = lowered[:_TITLE_WINDOW]
    later = "any later version" in title

    if "gnu affero general public license" in title:
        if "version 3" in title:
            return "AGPL-3.0-or-later" if later else "AGPL-3.0-only"
        return ""
    if "gnu lesser general public license" in title:
        if "version 3" in title:
            return "LGPL-3.0-or-later" if later else "LGPL-3.0-only"
        if "version 2.1" in title:
            return "LGPL-2.1-or-later" if later else "LGPL-2.1-only"
        if "version 2" in title:
            return "LGPL-2.0-or-later" if later else "LGPL-2.0-only"
        return ""
    if "gnu general public license" in title:
        if "version 3" in title:
            return "GPL-3.0-or-later" if later else "GPL-3.0-only"
        if "version 2" in title:
            return "GPL-2.0-or-later" if later else "GPL-2.0-only"
        return ""
    if "apache license" in lowered and "version 2.0" in lowered:
        return "Apache-2.0"
    if "mozilla public license" in lowered and "2.0" in lowered:
        return "MPL-2.0"
    if "eclipse public license" in lowered and "2.0" in lowered:
        return "EPL-2.0"
    if "boost software license" in lowered:
        return "BSL-1.0"
    if "permission is hereby granted, free of charge" in lowered:
        return "MIT"
    if "permission to use, copy, modify, and/or distribute" in lowered:
        return "ISC"
    if "redistribution and use in source and binary forms" in lowered:
        if "neither the name" in lowered:
            return "BSD-3-Clause"
        return "BSD-2-Clause"
    if "python software foundation license" in lowered:
        return "PSF-2.0"
    if "this is free and unencumbered software released into the public domain" in lowered:
        return "Unlicense"
    return ""


def license_from_classifiers(classifiers: object) -> str:
    """Map Trove license classifiers to an SPDX expression or license name.

    Classifiers without an unambiguous SPDX id (``BSD License``,
    ``Other/Proprietary License``) are reported by their human-readable name so
    the dependency still shows *a* license: dropping them left packages such
    as Jinja2 at "Not recorded", and discarded the standard
    ``License :: Other/Proprietary License`` marker that identifies a
    closed-source package.

    Args:
        classifiers: Iterable of ``Classifier:`` values from package metadata.

    Returns:
        SPDX identifier or ``OR``-joined expression, a comma-separated list of
        license names, or ``""`` when the package declares no license.
    """
    if not isinstance(classifiers, (list, tuple)):
        return ""
    ids: list[str] = []
    names: list[str] = []
    for classifier in classifiers:
        if not isinstance(classifier, str):
            continue
        clean = _collapse(classifier)
        mapped = _TROVE_TO_SPDX.get(clean)
        if mapped:
            if mapped not in ids:
                ids.append(mapped)
            continue
        if not clean.lower().startswith("license ::"):
            continue
        name = clean.split(" :: ")[-1].strip()
        if name and name.lower() != "unknown" and name not in names:
            names.append(name)
    if ids:
        return " OR ".join(ids)
    return ", ".join(names)


def resolve_license(
    *,
    expression: str = "",
    text: str = "",
    classifiers: object = (),
) -> str:
    """Resolve the best available license value from package metadata.

    Preference order: ``License-Expression`` (PEP 639), a ``License`` value
    that already is an SPDX identifier, a short license *name* carrying a
    restrictive marker (``Commercial License``) which is preserved verbatim so
    the reader still sees the restriction, a recognized free-text license
    name, a recognized license body, then Trove classifiers.  Unrecognized
    short free text is preserved as a license *name* so it can still be
    displayed.

    Both SPDX fields are canonicalized up to ``_MAX_STATUS_EXPRESSION_LEN``
    rather than the smaller storage bound: dropping a long but valid
    declaration would fall through to a weaker source (for example a permissive
    Trove classifier) and hide a restricted component such as ``AND
    SSPL-1.0``.  A ``License`` value that reads like a license body is still
    detected as a body instead of being parsed as an expression.

    Args:
        expression: ``License-Expression`` metadata value.
        text: ``License`` metadata value (may be a full license body).
        classifiers: ``Classifier`` values from package metadata.

    Returns:
        SPDX identifier/expression, free-text license name, or ``""``.
    """
    canonical = _canonical_declared_expression(expression)
    if canonical:
        return canonical
    canonical = canonical_spdx(text)
    if canonical:
        return canonical
    clean = _collapse(text)
    if _has_proprietary_marker(clean):
        return clean
    if not _looks_like_license_body(clean):
        declared = _canonical_declared_expression(clean)
        if declared:
            return declared
    mapped = text_to_spdx(clean)
    if mapped:
        return mapped
    # A value naming several licenses is kept whole: name/body detection would
    # match the first recognizable license and drop a restricted sibling
    # (`Apache License version 2.0 AND SSPL-1.0` is not `Apache-2.0`).
    if _is_compound_name(clean):
        return clean
    detected = detect_license_text(clean)
    if detected:
        return _license_body_value(clean, detected)
    from_classifier = license_from_classifiers(classifiers)
    if from_classifier:
        return from_classifier
    if clean and len(clean) <= _MAX_NAME_LEN and not _looks_like_license_body(clean):
        return clean
    return ""


def _looks_like_license_body(value: str) -> bool:
    """Return whether *value* reads like a license body rather than a name.

    Args:
        value: Collapsed metadata text.

    Returns:
        True when the text starts like a license grant or copyright notice.
    """
    lowered = value.lower()
    return lowered.startswith(("copyright", "permission is hereby", "redistribution and use", "this program"))


def _has_proprietary_marker(value: str) -> bool:
    """Return whether a short license value carries a restrictive marker.

    Checked before name mapping and body detection so an explicit restriction
    is never overridden: ``Commercial Apache License version 2.0`` must not be
    resolved to ``Apache-2.0`` because the body signature matches, and
    ``Limited Use License`` must not reach the open-source hints.

    Args:
        value: Collapsed license value.

    Returns:
        True when the value is short enough to be a license name and contains
        a proprietary marker.
    """
    if not value or len(value) > _MAX_MARKER_LEN:
        return False
    lowered = value.lower()
    return any(marker in lowered for marker in _PROPRIETARY_MARKERS)


def join_licenses(values: list[str]) -> str:
    """Combine several license values into one SPDX-ish expression.

    A list of SPDX identifiers reads as a choice, so it becomes an ``OR``
    expression.  A list that is not fully SPDX cannot express a choice and is
    joined with ``AND`` instead: a consumer is bound by every declared value,
    so joining ````["MIT", "SSPL-1.0", "Custom terms"]`` with commas would let
    the ``MIT`` hint make the whole list look open source.

    Args:
        values: Raw license strings (may be empty, free text, or SPDX ids).

    Returns:
        ``" OR "``-joined canonical identifiers when all values are SPDX
        identifiers, otherwise the ``" AND "``-joined declarations.
    """
    cleaned = [_collapse(value) for value in values if _collapse(value)]
    if not cleaned:
        return ""
    canonical = [canonical_spdx(value) for value in cleaned]
    if all(canonical):
        unique: list[str] = []
        for value in canonical:
            if value not in unique:
                unique.append(value)
        return " OR ".join(unique)
    unique_text: list[str] = []
    for value in cleaned:
        if value not in unique_text:
            unique_text.append(value)
    return " AND ".join(unique_text)


def license_from_collection_files(collection_dir: Path, license_file: str = "") -> str:
    """Derive a collection license from on-disk license files.

    Handles the REUSE/SPDX layout most collections in the ``ansible`` package
    now use: ``MANIFEST.json`` carries ``license: []`` plus a ``license_file``
    (``COPYING``/``LICENSE``) and sometimes an SPDX-named ``LICENSES/``
    directory.  Machine-readable sources win: an SPDX-named ``license_file``
    stem, then ``LICENSES/<id>.txt``, then the body of ``license_file``, and
    finally the file name itself so the reader at least sees where to look.

    ``LICENSES/`` entries describe the licenses *used by files* in the
    collection, not alternatives the consumer may choose between, so they are
    joined with ``AND``: joining them with ``OR`` would let an open-source
    entry hide a ``LicenseRef-`` (source-available) sibling, and would tell
    SBOM consumers they may pick either license.  Only lone identifiers count:
    an expression-shaped file name such as ``MIT OR ISC.txt`` is not verifiable
    from a directory listing, so it marks the listing unverified instead of
    contributing a precedence-sensitive expression.  So does an entry that does
    not name a license at all: ``MIT.txt`` beside ``Commercial License.txt``
    yields an unverified listing, never a lone ``MIT``.

    Collection metadata is untrusted: only regular files whose resolved path
    stays inside *collection_dir* are read, at most ``_MAX_LICENSE_FILE_BYTES``
    of each, and a ``LICENSES/`` directory larger than ``_MAX_LICENSE_FILES``
    entries is not summarized at all, so a traversal path or multi-gigabyte
    symlink cannot exhaust the scan worker and an unverified listing cannot
    hide a restricted license.

    Args:
        collection_dir: Installed collection directory.
        license_file: ``license_file`` value from collection metadata.

    Returns:
        SPDX identifier/expression, file name, or ``""``.
    """
    name = _collapse(license_file)
    root = _collection_root(collection_dir)
    if name and root is not None and _contained_path(root, name) is not None:
        stem_id = canonical_spdx(Path(name).stem)
        if stem_id:
            return stem_id
    if root is not None:
        ids = _licenses_directory_ids(root)
        if ids is None:
            return _UNVERIFIED_LICENSES
        if ids:
            return " AND ".join(ids)
    if name:
        detected = _read_license_body(root, name)
        if detected:
            return detected
        return name
    return ""


def _collection_root(collection_dir: Path) -> Path | None:
    """Resolve an installed collection directory, following symlinks.

    Args:
        collection_dir: Installed collection directory.

    Returns:
        Resolved directory, or None when it cannot be resolved.
    """
    try:
        root = collection_dir.resolve()
    except (OSError, RuntimeError):
        return None
    return root if root.is_dir() else None


def _contained_path(root: Path, relative: str) -> Path | None:
    """Return a path inside *root*, or None when it escapes or is not a file.

    Args:
        root: Resolved collection directory.
        relative: Collection-controlled relative path.

    Returns:
        Resolved regular file inside *root*, or None.
    """
    if not relative or Path(relative).is_absolute():
        return None
    try:
        candidate = (root / relative).resolve()
    except (OSError, RuntimeError, ValueError):
        # `Path.resolve` raises ValueError for a path with an embedded NUL.
        return None
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _contained_dir(root: Path, name: str) -> Path | None:
    """Return a subdirectory inside *root*, or None when it escapes.

    Args:
        root: Resolved collection directory.
        name: Single directory name, e.g. ``LICENSES``.

    Returns:
        Resolved directory inside *root*, or None.
    """
    try:
        candidate = (root / name).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not candidate.is_relative_to(root) or not candidate.is_dir():
        return None
    return candidate


def _licenses_directory_ids(root: Path) -> list[str] | None:
    """Collect SPDX ids from an ``LICENSES/`` directory.

    A listing larger than ``_MAX_LICENSE_FILES`` is reported as incomplete
    rather than truncated: dropping entries could hide a restricted license
    behind an open-source sibling and make the collection look usable.  For the
    same reason an entry that cannot be identified as a lone SPDX identifier,
    or that resolves outside the collection, makes the whole listing
    unverifiable instead of being skipped.

    Args:
        root: Resolved collection directory.

    Returns:
        Sorted distinct SPDX identifiers, an empty list when there is no usable
        directory, or None when the directory cannot be verified in full.
    """
    spdx_dir = _contained_dir(root, "LICENSES")
    if spdx_dir is None:
        return []
    ids: list[str] = []
    examined = 0
    try:
        with os.scandir(spdx_dir) as entries:
            for entry in entries:
                examined += 1
                if examined > _MAX_LICENSE_FILES:
                    return None
                try:
                    if not entry.is_file() or not Path(entry.path).resolve().is_relative_to(root):
                        return None
                except OSError:
                    return None
                spdx_id = canonical_spdx(Path(entry.name).stem)
                if not spdx_id or not is_spdx_id(spdx_id):
                    # An unidentified or expression-shaped file name
                    # (``Commercial License.txt``, ``MIT OR ISC.txt``) cannot be
                    # verified: skipping it would drop a restricted sibling and
                    # leave the remaining open-source ids looking like the whole
                    # listing, while the directory records only that a file
                    # exists, not how its alternatives relate.
                    return None
                if spdx_id not in ids:
                    ids.append(spdx_id)
    except OSError:
        return None
    return sorted(ids)


def _license_body_value(text: str, detected: str) -> str:
    """Combine a license detected in a body with any supplemental restriction.

    Args:
        text: License text that *detected* was recognized in.
        detected: SPDX identifier recognized in the text, or ``""``.

    Returns:
        SPDX identifier, a restriction display form when only a restriction was
        recognized, or the identifier with the restriction appended (`MIT with
        Commons Clause`) so classification sees both.
    """
    restriction = _supplemental_restriction(text)
    if restriction:
        # A restriction appended to a base license is not a license of its own:
        # keep both so classification does not report the bare grant.
        return f"{detected} with {restriction}" if detected else restriction
    return detected


def _read_license_body(root: Path | None, relative: str) -> str:
    """Detect a license from a bounded read of a collection license file.

    Args:
        root: Resolved collection directory, or None when unresolvable.
        relative: ``license_file`` value from collection metadata.

    Returns:
        SPDX identifier, or ``""`` when the file is unusable or unrecognized.
    """
    if root is None:
        return ""
    candidate = _contained_path(root, relative)
    if candidate is None:
        return ""
    try:
        with candidate.open("rb") as handle:
            # One byte past the limit distinguishes a complete file from a
            # truncated prefix: a restriction can sit beyond the read window,
            # so a partial body would report the grant without it.
            body = handle.read(_MAX_LICENSE_FILE_BYTES + 1)
    except OSError:
        return ""
    if len(body) > _MAX_LICENSE_FILE_BYTES:
        return ""
    text = body.decode("utf-8", errors="replace")
    return _license_body_value(text, detect_license_text(text))


def _supplemental_restriction(text: str) -> str:
    """Return an explicit supplemental restriction named in a license body.

    Only restrictions that are *added to* a base license are detected here.
    The generic proprietary markers cannot be used on a body: BSD boilerplate
    legitimately contains "All rights reserved" and GPL text mentions
    "commercial" without being a commercial license.

    Args:
        text: Decoded license file contents.

    Returns:
        Display form of the restriction (``Commons Clause``), or ``""``.
    """
    lowered = text.lower()
    for marker, display in _SUPPLEMENTAL_RESTRICTIONS:
        if marker in lowered:
            return display
    return ""


def classify_license(value: str) -> LicenseStatus:
    """Classify a license value for the legal-risk indicator in the UI.

    Only positive evidence produces ``"proprietary"``: a known non-open-source
    SPDX identifier, a ``LicenseRef-`` reference, or a restrictive marker in a
    value short enough to be a license *name*.  Restrictive markers are checked
    before name mapping and body detection so an explicit restriction survives
    (``Commercial Apache License version 2.0`` is not ``Apache-2.0``), and
    open-source hints must match whole words so ``Limited Use License`` does not
    classify as open source through the ``mit`` in "Limited".  A value mixing
    several licenses is decided by evaluating each operand, never by one
    matching name, so a restricted component cannot be hidden behind an
    open-source sibling — including when the value is too long to canonicalize
    as an SPDX expression.  A bare file name (``MIT.txt``) is display text, not
    a declaration, so it stays unknown rather than matching the ``mit`` hint.
    Unrecognized values stay ``"unknown"`` — neutral, never red.

    Args:
        value: Stored license string (SPDX id/expression, free text, or empty).

    Returns:
        ``"open-source"`` when every required component is a known open-source
        license, ``"proprietary"`` when the value is positively non-open-source,
        and ``"unknown"`` otherwise (missing or unrecognized).
    """
    text = _collapse(value)
    if not text:
        return UNKNOWN
    lowered = text.lower()

    # A declared reference such as `LicenseRef-Acme.EULA` looks like a file
    # name, so SPDX identity is decided first and the file-name check only
    # applies to values that are not an expression.
    if len(text) <= _MAX_CANONICAL_EXPRESSION_LEN:
        canonical = _canonicalize(text)
        if canonical:
            return _classify_expression(canonical)
    if _LICENSE_FILE_NAME_RE.match(text):
        return UNKNOWN

    if _has_proprietary_marker(text):
        return PROPRIETARY
    if lowered.startswith(_OSI_CLASSIFIER_PREFIX):
        return OPEN_SOURCE
    if text_to_spdx(text):
        return OPEN_SOURCE
    if _is_compound_name(text):
        return _classify_compound(text)
    if detect_license_text(text):
        return OPEN_SOURCE
    if _OPEN_SOURCE_HINT_RE.search(lowered):
        return OPEN_SOURCE
    return UNKNOWN


#: Operator or grouping token separating licenses in a compound value, e.g.
#: ``MIT AND Apache-2.0`` or ``(MIT OR Apache-2.0)``.
_COMPOUND_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[()]|\b(?:and|or|with)\b", re.IGNORECASE)

#: Uppercase operator emitted by :func:`join_licenses` when it combines a
#: metadata list.  Such a list can be longer than a license name without being
#: prose, so it is always split into operands.
_LIST_JOIN_RE: Final[re.Pattern[str]] = re.compile(r" (?:AND|OR|WITH) ")


def _is_compound_name(text: str) -> bool:
    """Return whether a value names several licenses and is not plain prose.

    Only short values are considered: a license *body* is long and mentions
    "and"/"or" throughout, so splitting it into operands would discard the
    signature the body detector recognises.

    Args:
        text: Collapsed license value.

    Returns:
        True when the value is short enough to be a license name and contains
        an operator or grouping token.
    """
    if _LIST_JOIN_RE.search(text):
        operands = [operand for operand in _LIST_JOIN_RE.split(text) if operand.strip()]
        return len(operands) > 1 and all(len(operand) <= _MAX_NAME_LEN for operand in operands)
    return len(text) <= _MAX_COMPOUND_NAME_LEN and bool(_COMPOUND_TOKEN_RE.search(text))


def _classify_compound(text: str) -> LicenseStatus:
    """Classify a value that names several licenses without being valid SPDX.

    Structure cannot be trusted for such a value (it is not parseable as an
    expression), so the verdict is conservative: a restricted operand makes the
    whole value ``proprietary`` — positive evidence that legal review is
    needed — and ``open-source`` requires every operand to stand on its own.
    One recognised name must never decide the value, which would hide a
    restricted sibling such as ``LicenseRef-MIT``.

    Args:
        text: Collapsed compound license value.

    Returns:
        Status of the compound value.
    """
    operands = [part.strip(" ,;:") for part in _COMPOUND_TOKEN_RE.split(text)]
    meaningful = [part for part in operands if part]
    if not meaningful:
        return UNKNOWN
    statuses = [_operand_status(part) for part in meaningful]
    if PROPRIETARY in statuses:
        return PROPRIETARY
    if all(status == OPEN_SOURCE for status in statuses):
        return OPEN_SOURCE
    return UNKNOWN


def _operand_status(text: str) -> LicenseStatus:
    """Classify a single operand of a compound value.

    SPDX identity is resolved before free-text hints, so ``LicenseRef-MIT``
    stays a custom reference requiring legal review instead of matching the
    ``MIT`` hint.

    Args:
        text: One operand, e.g. ``MIT``, ``Apache License, Version 2.0``, or
            ``LicenseRef-MIT``.

    Returns:
        Status of the operand.
    """
    canonical = canonical_spdx(text)
    if canonical:
        return _classify_expression(canonical)
    if _is_non_open_source_id(text):
        return PROPRIETARY
    if _has_proprietary_marker(text):
        return PROPRIETARY
    if text_to_spdx(text) or detect_license_text(text):
        return OPEN_SOURCE
    if _OPEN_SOURCE_HINT_RE.search(text.lower()):
        return OPEN_SOURCE
    return UNKNOWN


#: A bare file name such as ``MIT.txt`` or ``LICENSE.mit``.  Collection metadata
#: may point at a ``license_file`` that is absent or unreadable; the name is kept
#: as display text, but the file name itself is not evidence of a license, so it
#: must not classify through the ``mit`` hint.
_LICENSE_FILE_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[^\s/\\]+\.[A-Za-z]{2,5}$")

#: Reported when a ``LICENSES/`` listing cannot be verified: it is too large to
#: scan in full, unreadable, or contains an expression-shaped file name whose
#: structure a directory listing does not establish.  Guessing from a partial or
#: ambiguous listing could omit a restricted sibling and leave an open-source
#: entry looking like the whole story, so the value stays unclassified.
_UNVERIFIED_LICENSES: Final = "LICENSES/ directory could not be verified"


#: Tokens of a canonical SPDX expression: parentheses, operators, identifiers.
_EXPRESSION_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"\(|\)|[^\s()]+")

#: SPDX operators understood by the expression evaluator.
_EXPRESSION_OPERATORS: Final[frozenset[str]] = frozenset({"AND", "OR", "WITH"})


def _classify_expression(expression: str) -> LicenseStatus:
    """Classify a canonical SPDX expression.

    The expression is evaluated as a tree so operators apply within their own
    branch (``AND`` binds tighter than ``OR``): ``MIT OR (Apache-2.0 AND
    SSPL-1.0)`` is open source because the open-source alternative is usable,
    while ``Apache-2.0 AND SSPL-1.0`` is proprietary.

    Args:
        expression: Canonical SPDX expression.

    Returns:
        License status for the expression.
    """
    tokens = _EXPRESSION_TOKEN_RE.findall(expression)
    if not tokens:
        return UNKNOWN
    try:
        status, _ = _evaluate_or(tokens, 0)
    except (IndexError, RecursionError):
        return UNKNOWN
    return status


def _evaluate_or(tokens: list[str], position: int) -> tuple[LicenseStatus, int]:
    """Evaluate an ``OR`` level of an SPDX expression.

    Args:
        tokens: Expression tokens.
        position: Index of the first token of this level.

    Returns:
        Status of the level and the index after it.
    """
    status, position = _evaluate_and(tokens, position)
    while position < len(tokens) and tokens[position] == "OR":
        right, position = _evaluate_and(tokens, position + 1)
        status = _combine_or(status, right)
    return status, position


def _evaluate_and(tokens: list[str], position: int) -> tuple[LicenseStatus, int]:
    """Evaluate an ``AND`` level of an SPDX expression.

    Args:
        tokens: Expression tokens.
        position: Index of the first token of this level.

    Returns:
        Status of the level and the index after it.
    """
    status, position = _evaluate_operand(tokens, position)
    while position < len(tokens) and tokens[position] == "AND":
        right, position = _evaluate_operand(tokens, position + 1)
        status = _combine_and(status, right)
    return status, position


def _evaluate_operand(tokens: list[str], position: int) -> tuple[LicenseStatus, int]:
    """Evaluate one identifier, parenthesized group, or exception.

    Args:
        tokens: Expression tokens.
        position: Index of the operand.

    Returns:
        Status of the operand and the index after it.
    """
    if position >= len(tokens):
        return UNKNOWN, position
    token = tokens[position]
    if token == "(":
        status, position = _evaluate_or(tokens, position + 1)
        if position < len(tokens) and tokens[position] == ")":
            position += 1
    elif token in _EXPRESSION_OPERATORS or token == ")":
        return UNKNOWN, position + 1
    else:
        status = _identifier_status(token)
        position += 1
    if position < len(tokens) and tokens[position] == "WITH":
        position += 2
    return status, position


def _combine_or(left: LicenseStatus, right: LicenseStatus) -> LicenseStatus:
    """Combine alternatives: open source as soon as one branch is.

    Args:
        left: Status of the left branch.
        right: Status of the right branch.

    Returns:
        Combined status.
    """
    if OPEN_SOURCE in (left, right):
        return OPEN_SOURCE
    if PROPRIETARY in (left, right):
        return PROPRIETARY
    return UNKNOWN


def _combine_and(left: LicenseStatus, right: LicenseStatus) -> LicenseStatus:
    """Combine required components: proprietary if any one is.

    Args:
        left: Status of the left component.
        right: Status of the right component.

    Returns:
        Combined status.  Open source only when both sides are known open
        source; an unclassified required component keeps the result neutral.
    """
    if PROPRIETARY in (left, right):
        return PROPRIETARY
    if left == OPEN_SOURCE and right == OPEN_SOURCE:
        return OPEN_SOURCE
    return UNKNOWN


def _identifier_status(spdx_id: str) -> LicenseStatus:
    """Classify a single SPDX identifier.

    Args:
        spdx_id: SPDX identifier or ``LicenseRef-`` reference.

    Returns:
        Status of the identifier.
    """
    if spdx_id in _OPEN_SOURCE_IDS:
        return OPEN_SOURCE
    if _is_non_open_source_id(spdx_id):
        return PROPRIETARY
    return UNKNOWN


def _is_non_open_source_id(spdx_id: str) -> bool:
    """Return whether an SPDX identifier is a known non-open-source license.

    Args:
        spdx_id: SPDX identifier.

    Returns:
        True for known proprietary/source-available licenses and for custom
        ``LicenseRef-`` references (which always need legal review).
    """
    return spdx_id in _NON_OPEN_SOURCE_IDS or spdx_id.startswith("LicenseRef-")
