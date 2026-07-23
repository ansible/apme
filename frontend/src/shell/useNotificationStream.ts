/**
 * Hook that loads notifications from the Gateway REST API, connects to the
 * SSE stream for real-time updates, and pushes everything into the vendored
 * notification drawer (zustand) and alert toaster (context) stores.
 *
 * To avoid a gap between the initial REST load and SSE subscription, the
 * EventSource opens first and buffers incoming events.  After the REST
 * response arrives, buffered items are merged (id-based dedupe) so no
 * notification is lost.
 */

import { useEffect, useRef } from 'react';
import { usePageNotifications } from '@ansible/ansible-ui-framework';
import { usePageAlertToaster } from '@ansible/ansible-ui-framework/PageAlertToaster';
import type { IPageNotification } from '@ansible/ansible-ui-framework/PageNotifications/PageNotification';
import type { IPageNotificationGroup } from '@ansible/ansible-ui-framework/PageNotifications/PageNotificationGroup';
import { apmeSseUrl } from '../api/apmeApiAdapter';
import { listNotifications, markNotificationRead } from '../services/api';
import type { NotificationItem } from '../types/api';

const GROUP_LABELS: Record<string, string> = {
  scan_complete: 'Scans',
  secrets_detected: 'Security',
  health_changed: 'Health',
};

function groupKey(n: NotificationItem): string {
  return GROUP_LABELS[n.type] ?? 'Other';
}

function toPageNotification(n: NotificationItem): IPageNotification {
  return {
    id: String(n.id),
    title: n.title,
    description: n.message,
    timestamp: n.created_at,
    variant: n.variant,
    to: n.link || undefined,
  };
}

function buildGroups(items: NotificationItem[]): Record<string, IPageNotificationGroup> {
  const groups: Record<string, IPageNotificationGroup> = {};
  for (const item of items) {
    const key = groupKey(item);
    if (!groups[key]) {
      groups[key] = { title: key, notifications: [] };
    }
    groups[key].notifications.push(toPageNotification(item));
  }
  return groups;
}

function mergeNotification(
  existing: Record<string, IPageNotificationGroup>,
  item: NotificationItem,
): Record<string, IPageNotificationGroup> {
  const key = groupKey(item);
  const next = { ...existing };
  const group = next[key]
    ? { ...next[key], notifications: [...next[key].notifications] }
    : { title: key, notifications: [] };

  const strId = String(item.id);
  if (!group.notifications.some((n) => n.id === strId)) {
    group.notifications.unshift(toPageNotification(item));
  }
  next[key] = group;
  return next;
}

const NO_DISMISS_TYPES = new Set(['secrets_detected']);

export function useNotificationStream(): void {
  const { setNotificationGroups } = usePageNotifications();
  const alertToaster = usePageAlertToaster();
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    let es: EventSource | undefined;

    const startStream = async () => {
      // Buffer SSE events that arrive before the REST load completes.
      const buffer: NotificationItem[] = [];
      let restLoaded = false;

      es = new EventSource(apmeSseUrl('/notifications/stream'));

      const handleSseItem = (item: NotificationItem) => {
        setNotificationGroups((prev) => mergeNotification(prev, item));

        const timeout = NO_DISMISS_TYPES.has(item.type) ? undefined : 8000;
        alertToaster.addAlert({
          key: `notif-${item.id}`,
          title: item.title,
          children: item.message,
          variant: item.variant,
          timeout,
          actionClose: undefined,
        });

        markNotificationRead(item.id).catch(() => {});
      };

      es.onmessage = (event) => {
        if (!mountedRef.current) return;
        let item: NotificationItem;
        try {
          item = JSON.parse(event.data as string) as NotificationItem;
        } catch {
          return;
        }

        if (!restLoaded) {
          buffer.push(item);
          return;
        }

        handleSseItem(item);
      };

      es.onerror = () => {
        // EventSource auto-reconnects — nothing to do
      };

      const emitBufferedAlert = (buffered: NotificationItem) => {
        const timeout = NO_DISMISS_TYPES.has(buffered.type) ? undefined : 8000;
        alertToaster.addAlert({
          key: `notif-${buffered.id}`,
          title: buffered.title,
          children: buffered.message,
          variant: buffered.variant,
          timeout,
          actionClose: undefined,
        });
        markNotificationRead(buffered.id).catch(() => {});
      };

      try {
        const resp = await listNotifications(100, 0);
        if (!mountedRef.current) return;

        const restIds = new Set(resp.items.map((n) => n.id));
        const merged = [...resp.items];
        const pending = buffer.splice(0);
        for (const buffered of pending) {
          if (!restIds.has(buffered.id)) {
            merged.unshift(buffered);
          }
        }

        // Set the initial groups first, then flip restLoaded so any SSE
        // event arriving after this point merges into an already-populated
        // store instead of racing with the replace.
        setNotificationGroups((prev) => {
          const base = buildGroups(merged);
          for (const [key, group] of Object.entries(prev)) {
            if (!base[key]) {
              base[key] = group;
            } else {
              for (const n of group.notifications) {
                if (!base[key].notifications.some((existing) => existing.id === n.id)) {
                  base[key].notifications.unshift(n);
                }
              }
            }
          }
          return base;
        });
        // Flip the flag BEFORE draining any late-arriving buffered items, so
        // any SSE message that slips in between the initial drain and here
        // (or between now and the follow-up drain below) is routed through
        // handleSseItem directly instead of getting stuck in `buffer`.
        restLoaded = true;

        for (const buffered of pending) {
          if (!restIds.has(buffered.id)) {
            emitBufferedAlert(buffered);
          }
        }

        // Second drain: if any SSE events arrived while we were merging/committing
        // the REST response (e.g. during the `setNotificationGroups` updater or
        // between `buffer.splice(0)` and flipping `restLoaded`), pick them up now
        // and route them through the normal handler so nothing is lost.
        if (buffer.length > 0) {
          const late = buffer.splice(0);
          for (const item of late) {
            if (!restIds.has(item.id)) {
              handleSseItem(item);
            }
          }
        }
      } catch {
        if (!mountedRef.current) return;

        // Flip restLoaded first so any SSE events arriving while we process the
        // buffered items go through handleSseItem directly rather than getting
        // stuck in `buffer`.
        restLoaded = true;

        const pending = buffer.splice(0);
        if (pending.length > 0) {
          setNotificationGroups((prev) => {
            let next = { ...prev };
            for (const item of pending) {
              next = mergeNotification(next, item);
            }
            return next;
          });
          for (const buffered of pending) {
            emitBufferedAlert(buffered);
          }
        }

        // Second drain for any events that landed during the merge above.
        if (buffer.length > 0) {
          const late = buffer.splice(0);
          for (const item of late) {
            handleSseItem(item);
          }
        }
      }
    };

    void startStream();

    return () => {
      mountedRef.current = false;
      es?.close();
    };
  }, [setNotificationGroups, alertToaster]);
}
