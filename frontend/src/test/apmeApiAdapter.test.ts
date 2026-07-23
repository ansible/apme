import { describe, it, expect, afterEach } from 'vitest';
import {
  apmeApiUrl,
  apmeSseUrl,
  apmeWsUrl,
  createDefaultApmeApiAdapter,
  getApmeApiAdapter,
  setApmeApiAdapter,
} from '../api/apmeApiAdapter';

describe('apmeApiAdapter', () => {
  afterEach(() => {
    setApmeApiAdapter(createDefaultApmeApiAdapter());
  });

  it('defaults apiBase to /api/v1', () => {
    expect(apmeApiUrl('/health')).toBe('/api/v1/health');
    expect(apmeApiUrl('health')).toBe('/api/v1/health');
  });

  it('passes through absolute /api/ paths', () => {
    expect(apmeApiUrl('/api/v1/ws/session')).toBe('/api/v1/ws/session');
  });

  it('builds absolute ws URLs when origin differs from the page', () => {
    setApmeApiAdapter(
      createDefaultApmeApiAdapter({ origin: 'https://gateway.example' }),
    );
    expect(apmeWsUrl('/ws/session')).toBe(
      'wss://gateway.example/api/v1/ws/session',
    );
  });

  it('uses relative sse URLs for same-origin / Vite proxy', () => {
    setApmeApiAdapter(
      createDefaultApmeApiAdapter({ origin: window.location.origin }),
    );
    expect(apmeSseUrl('/notifications/stream')).toBe(
      '/api/v1/notifications/stream',
    );
  });

  it('builds absolute sse URLs when origin differs from the page', () => {
    setApmeApiAdapter(
      createDefaultApmeApiAdapter({ origin: 'http://gateway.example:8080' }),
    );
    expect(apmeSseUrl('/notifications/stream')).toBe(
      'http://gateway.example:8080/api/v1/notifications/stream',
    );
  });

  it('respects custom apiBase', () => {
    setApmeApiAdapter(
      createDefaultApmeApiAdapter({ apiBase: 'https://gw.example/api/v1' }),
    );
    expect(apmeApiUrl('/health')).toBe('https://gw.example/api/v1/health');
    expect(getApmeApiAdapter().apiBase).toBe('https://gw.example/api/v1');
  });

  it('trims a trailing slash on apiBase', () => {
    setApmeApiAdapter(
      createDefaultApmeApiAdapter({ apiBase: 'https://gw.example/api/v1/' }),
    );
    expect(apmeApiUrl('/health')).toBe('https://gw.example/api/v1/health');
    expect(apmeSseUrl('/notifications/stream')).toBe(
      'https://gw.example/api/v1/notifications/stream',
    );
  });

  it('builds ws URLs from an absolute apiBase', () => {
    setApmeApiAdapter(
      createDefaultApmeApiAdapter({
        apiBase: 'https://gw.example/api/v1',
        origin: window.location.origin,
      }),
    );
    expect(apmeWsUrl('/ws/session')).toBe(
      'wss://gw.example/api/v1/ws/session',
    );
  });
});
