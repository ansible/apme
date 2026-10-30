# @apme/backstage-backend

Optional authenticated upload transport for RHDH Local and Portal (ADR-074).
Relays the existing Gateway FixSession WebSocket frames; does not own workflow
state or duplicate Engine behavior.

For a personal Local deployment register the backend plugin:

```ts
backend.add(import("@apme/backstage-backend"));
```

Configure the server-only Gateway origin:

```yaml
apme:
    gatewayBaseUrl: http://127.0.0.1:8080
```

The frontend discovers `apme` for its upload channel. Existing REST/SSE still use
the configured host proxy. Keep the Gateway private to the host backend.

Portal must mount `createUploadSessionRouter` on its catalog router under `/apme`
with an authorization callback that checks its user, organization, entitlement
and scan permission on every request. The callback returns a stable user subject.
Register `close` as a shutdown hook. Do not use the personal plugin's default
policy in a shared Portal deployment.

Channels accept only the existing session command types and bind resume IDs and
scan IDs to their authenticated owner. Unknown/restarted ownership bindings fail
closed. Active channels require sticky routing in multi-replica hosts. The
transport keeps at most 64 live channels (4 per user), accepts payloads up to
70 MiB (the Gateway's 50 MiB messages plus base64 overhead), caps pre-stream
buffering at 256 KiB, and closes channels not attached to a reader within 30 s.
Streaming uses backpressure. Cleanup releases sockets and timers on disconnect
and shutdown. Resource and real-host acceptance remain required.
