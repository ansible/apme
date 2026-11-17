# Gateway Git fetch safeguards

`apme_gateway.scm.safe_fetch.git_snapshot` is the Gateway entry point for
project-controlled Git dependencies. Callers consume the checkout inside its
async context; closing the context deletes it. Credentials must already be
scoped to the repository origin by the caller.

## Temporary storage

Set `APME_GIT_FETCH_ROOT` to a dedicated, writable **tmpfs mount root** with at
most 512 MiB and 65536 inodes. For example, an administrator can provision a
Linux tmpfs with `size=512m,nr_inodes=8192,mode=0700,uid=1001,gid=0` at
`/var/run/apme-git` and set `APME_GIT_FETCH_ROOT=/var/run/apme-git`.
Use the actual Gateway runtime UID/GID. Configure the mount through the target
deployment system and ensure it exists in the Gateway container mount namespace.

The helper checks `/proc/self/mountinfo` and filesystem capacity before every
fetch, then revalidates and holds an open descriptor to the actual mount.
Checkout allocation and Git access use that descriptor so replacing the mount
pathname cannot redirect writes to another filesystem. The returned checkout
path references the owner process and remains usable by ordinary child processes
while the context is open. Missing configuration, disk-backed directories,
oversized tmpfs mounts, and mounts without a finite acceptable inode budget fail closed. Mount quotas
are shared across simultaneous fetches; they cover Git objects, indexes,
metadata, checkout files, and `TMPDIR`. A repository that exceeds capacity fails
and is cleaned up. Shallow history alone is not a storage bound.

Linux mount administration remains the deployment owner's responsibility.
Grant neither mount capabilities nor privilege to the Gateway. An unprivileged
Gateway cannot change the configured mount limits. Account for this tmpfs budget
in the container/pod memory limit. Kernel quotas prevent fetch storage from
consuming unbounded disk, but a fetch can temporarily consume the configured
shared budget and make other fetches fail. Existing per-file and upload checks
still apply after checkout.

## Network policy

Public unicast addresses are accepted. Private destinations require explicit
CIDRs in the deployment-only `APME_GIT_PRIVATE_CIDRS` environment variable,
for example `10.20.30.40/32,fd00:1234::10/128`. Prefer narrow ranges for actual Git
servers. Loopback, link-local, multicast and unspecified destinations remain
prohibited even if listed. Standard NAT64, 6to4 and Teredo ranges are also
rejected, because translated destinations can conceal restricted IPv4 targets.
All answers for a hostname must satisfy policy. Invalid CIDR configuration
identifies `APME_GIT_PRIVATE_CIDRS` without echoing its value.

Git must expose `http.curloptResolve` support. An approved DNS address is pinned
for the actual HTTPS connection. Numeric IPv4/IPv6 URLs already select the
validated address and do not need a resolve override. Git cannot perform a second DNS lookup that
changes the destination. HTTPS certificate verification and server-name checks
remain enabled. HTTP redirects are disabled, so configure the final clone URL.
Ambient Git configuration, proxies, credential helpers, hooks and TLS bypass
variables are excluded from this fetch path. Explicit custom CA bundles remain
supported. Only HTTPS transport is allowed; submodules are not initialized.

This prerequisite adds the shared helper. PR #879 wires Git requirements to it;
existing project clone operations retain their established behavior.

## Diagnostics and verification

Failures report the Git stage, exit status and a fixed diagnostic category
(for example, authentication failure or quota exhaustion). Git stderr is drained
with at most 8 KiB retained for classification; raw server messages and
credentials are never included in the public exception. After Git exits, the
helper allows up to one second to drain stderr through EOF before closing pipe
handles, so late diagnostics are classified without letting a descendant hold
cleanup open indefinitely. Missing or malformed kernel mount identity produces
a descriptive storage error and closes the held descriptor.

`tox -e git-fetch-quota` exercises concurrent production HTTPS snapshots against
a real 4 MiB tmpfs, covering packed objects, checkout expansion and loose-object
inode exhaustion. A shared-budget case holds one individually valid 3 MiB
snapshot while a second must fail, then verifies capacity recovers after cleanup.
A separate single-fetch case verifies loose-object expansion and inode exhaustion
without relying on how concurrent fetches are scheduled.
CI runs this gate with mount privileges; inability to mount
fails the gate. The normal unit suite skips these cases on unprivileged hosts.
In the normal unit suite, TLS transport tests skip if Git, OpenSSL or
destination-pinning support is unavailable. The required gate fails instead when
any of these prerequisites is missing. Separate unit coverage verifies that
unsupported Git fails closed.
