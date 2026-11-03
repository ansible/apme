/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import type { ReactElement } from "react";

/** An already-open tab may hold chunk addresses from the previous image. */
export async function loadLocalPage(load: () => Promise<ReactElement>) {
  try {
    return await load();
  } catch (error) {
    if (
      !(error instanceof Error) ||
      !/ChunkLoadError|Loading (?:CSS )?chunk|Failed to fetch dynamically imported module/i.test(
        `${error.name}: ${error.message}`,
      )
    )
      throw error;
    return (
      <section role="alert" style={{ padding: 24 }}>
        <h2>The APME interface needs to reload</h2>
        <p>
          A page asset could not be loaded. If APME was rebuilt or updated,
          reload to use the current version.
        </p>
        <button type="button" onClick={() => window.location.reload()}>
          Reload APME
        </button>
      </section>
    );
  }
}
