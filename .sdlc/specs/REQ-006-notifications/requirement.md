# REQ-006: Notifications System

## Metadata

- **Phase**: PHASE-003 - Enterprise Dashboard
- **Status**: Draft
- **Created**: 2026-03-24

## Overview

A notification system that alerts users about scan results, policy violations, remediation completions, and system events. Supports multiple delivery channels (in-app, email, webhooks) with configurable triggers and per-user preferences.

## User Stories

**As a DevOps Engineer**, I want to receive notifications when a scan completes so that I can review results without polling the UI.

**As an Automation Architect**, I want alerts when Critical/High violations are detected so that I can respond quickly to compliance issues.

**As a Platform Admin**, I want webhook notifications so that I can integrate scan events into our existing alerting stack (Slack, PagerDuty, ServiceNow).

**As a Project Owner**, I want a digest of scan activity for my projects so that I stay informed without being overwhelmed by individual alerts.

## Acceptance Criteria

### Scan Completion Notification
- **GIVEN** a user subscribed to scan-complete events
- **WHEN** a scan finishes
- **THEN** they receive a notification with summary (pass/fail, violation counts by severity)

### Severity-Triggered Alert
- **GIVEN** a notification rule: "Alert on Critical violations"
- **WHEN** a scan produces one or more Critical violations
- **THEN** the configured channel (in-app, email, webhook) receives an alert

### Webhook Delivery
- **GIVEN** a webhook URL configured for a project
- **WHEN** a triggering event occurs
- **THEN** a signed JSON payload is POSTed to the webhook URL
- **AND** delivery is retried up to 3 times on failure (exponential backoff)

### In-App Notification Center
- **GIVEN** a logged-in user with unread notifications
- **WHEN** they view the notification center in the UI
- **THEN** they see a chronological list with read/unread state and ability to dismiss

### Notification Preferences
- **GIVEN** a user in the settings page
- **WHEN** they configure notification preferences
- **THEN** they can enable/disable channels per event type (scan complete, violation threshold, remediation complete, system alerts)

### Digest Mode
- **GIVEN** a user who prefers digest notifications
- **WHEN** the digest interval elapses (e.g., daily)
- **THEN** a single summary notification is sent covering all events in the period

## Inputs / Outputs

### Inputs

| Name | Type | Description | Required |
|------|------|-------------|----------|
| event_type | enum | scan_complete, violation_alert, remediation_complete, system | Yes |
| severity_filter | enum[] | Trigger only for specific severity levels | No |
| channel | enum | in_app, email, webhook | Yes |
| webhook_url | string | Target URL for webhook delivery | If channel=webhook |
| digest_interval | string | none, daily, weekly | No |

### Outputs

| Name | Type | Description |
|------|------|-------------|
| notification | Notification | Rendered notification with title, body, metadata |
| delivery_status | enum | sent, failed, retrying, delivered |

## Behavior

### Happy Path

1. Event occurs (scan complete, violation detected, etc.)
2. Notification engine evaluates all subscription rules matching the event
3. For each match, renders notification content using event data
4. Dispatches to the appropriate channel (in-app DB insert, email queue, webhook POST)
5. Tracks delivery status; retries on failure

### Notification Event Types

| Event | Payload Includes |
|-------|-----------------|
| scan_complete | project, scan_id, violation_summary, pass/fail |
| violation_alert | project, rule_id, severity, file, line, message |
| remediation_complete | project, scan_id, fixes_applied, fixes_skipped |
| system | message, severity (info/warn/error) |

### Edge Cases

| Case | Handling |
|------|----------|
| Webhook endpoint down | Retry 3x with exponential backoff; mark as failed |
| User has no preferences set | Defaults to in-app only for scan_complete |
| Burst of events | Batch/debounce within 30s window to avoid flood |
| Invalid webhook URL | Validation on save; reject malformed URLs |

### Error Conditions

| Error | Cause | Response |
|-------|-------|----------|
| Webhook delivery failure | Endpoint unreachable after retries | Mark failed; show in notification center |
| Email delivery failure | SMTP error | Retry; fall back to in-app notification |
| Rate limit exceeded | Too many notifications in window | Aggregate into single summary |

## Dependencies

### Internal

- REQ-001: Core Scanning Engine (scan events)
- REQ-005: Rule Rating & Severity (severity-based triggers)
- REQ-009: Project-Centric UI (project-scoped subscriptions)

### External

- SMTP service for email delivery (optional, configurable)
- Webhook consumers (Slack, PagerDuty, etc.) — external integrations

## Non-Functional Requirements

- **Performance**: Notification dispatch must not block scan completion (<100ms async handoff)
- **Reliability**: Webhook delivery with at-least-once semantics (retry + idempotency key)
- **Security**: Webhook payloads signed with HMAC-SHA256; email content sanitized
- **Scalability**: Support 1000+ notification subscriptions without degradation

## Open Questions

- [ ] Should we support SMS as a channel?
- [ ] Should notification rules be project-scoped, org-scoped, or both?
- [ ] Do we need a notification API for third-party integrations to subscribe programmatically?
- [ ] Should we integrate with AAP notification system (if available)?

## References

- REQ-005: Rule Rating & Severity (triggers based on severity)
- REQ-009: Project-Centric UI (project context for notifications)

---

## Change History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-24 | APME Team | Initial draft |
