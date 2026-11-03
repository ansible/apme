/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { existsSync, realpathSync, symlinkSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** Tests and builds must resolve the same installed SDK, even on a fresh checkout. */
export function prepareSdk(checkout) {
    const portal = resolve(
        checkout ??
            process.env.APME_PORTAL_CHECKOUT ??
            "../ansible-backstage-plugins",
    );
    const target = join(portal, "node_modules");
    if (!existsSync(join(target, "@ansible/portal-extension-api/package.json")))
        throw new Error(
            "Install the pinned Portal SDK checkout first; set APME_PORTAL_CHECKOUT or --portal-checkout",
        );
    const source = resolve(dirname(fileURLToPath(import.meta.url)), "..");
    const modules = join(source, "node_modules");
    if (!existsSync(modules)) symlinkSync(target, modules, "dir");
    if (realpathSync(modules) !== realpathSync(target))
        throw new Error(
            "frontend/portal/node_modules must link to the selected Portal SDK checkout; remove the old link before rebuilding",
        );
    return portal;
}
