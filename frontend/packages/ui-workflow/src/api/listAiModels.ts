import {
  apmeApiUrl,
  getApmeApiAdapter,
  type ApmeApiAdapter,
} from './apmeApiAdapter';

export interface AiModelInfo {
  id: string;
  provider: string;
  name: string;
}

/**
 * List Gateway AI models (for CheckOptionsForm). Pass the `useApmeApi()`
 * value when calling from React; the module default applies otherwise.
 */
export async function listAiModels(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<AiModelInfo[]> {
  const { fetch: f } = adapter;
  const res = await f(apmeApiUrl('/ai/models', adapter));
  if (!res.ok) {
    throw new Error(`Failed to list AI models: ${res.status}`);
  }
  return (await res.json()) as AiModelInfo[];
}
