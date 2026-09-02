/**
 * Map a canonical SCM pull/merge request URL to the "files changed" diff view.
 * Display-time only — stored `pr_url` values remain the SCM web URL.
 */

function pathnameAlreadyDiffView(pathname: string): boolean {
  return /\/(files|diffs|diff)$/.test(pathname.replace(/\/$/, ''));
}

/**
 * Return a URL that opens the PR/MR files-changed tab for supported forges.
 * Unrecognized URLs are returned unchanged.
 */
export function toPrFilesDiffUrl(prUrl: string): string {
  if (!prUrl?.trim()) {
    return prUrl;
  }

  try {
    const url = new URL(prUrl);
    const pathname = url.pathname.replace(/\/$/, '');

    if (pathnameAlreadyDiffView(pathname)) {
      return prUrl;
    }

    if (/\/pull\/\d+$/.test(pathname)) {
      url.pathname = `${pathname}/files`;
      return url.toString();
    }
    if (/\/merge_requests\/\d+$/.test(pathname)) {
      url.pathname = `${pathname}/diffs`;
      return url.toString();
    }
    if (/\/pull-requests\/\d+$/.test(pathname)) {
      url.pathname = `${pathname}/diff`;
      return url.toString();
    }
  } catch {
    // Not a valid absolute URL — return as-is.
  }

  return prUrl;
}
