const identities = new WeakMap<object, number>();
let nextIdentity = 0;

/** Return a stable per-instance key for state owned by a host adapter. */
export function adapterIdentity(adapter: object): number {
  let identity = identities.get(adapter);
  if (identity === undefined) {
    identity = ++nextIdentity;
    identities.set(adapter, identity);
  }
  return identity;
}
