/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
const { dirname, isAbsolute, relative, resolve, sep } = require("node:path");

function commonAncestorPath(left, right) {
    const target = resolve(right);
    let ancestor = resolve(left);
    for (;;) {
        const rel = relative(ancestor, target);
        if (
            rel === "" ||
            (!isAbsolute(rel) && rel !== ".." && !rel.startsWith(`..${sep}`))
        ) {
            return ancestor;
        }
        const parent = dirname(ancestor);
        if (parent === ancestor) return ancestor;
        ancestor = parent;
    }
}

module.exports = { commonAncestorPath };
