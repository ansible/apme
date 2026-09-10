"""Galaxy Proxy — PEP 503 bridge converting Galaxy tarballs to Python wheels."""

__version__ = "0.1.0"

#: Bound on version-list pagination (100 entries/page), shared by the
#: deprecated :mod:`galaxy_proxy.galaxy_client` and
#: :mod:`galaxy_proxy.proxy.server` so the truncation behavior cannot drift.
MAX_VERSION_PAGES = 50
