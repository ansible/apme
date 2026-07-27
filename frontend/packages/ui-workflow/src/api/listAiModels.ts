import { apmeApiUrl, getApmeApiAdapter } from './apmeApiAdapter';

export interface AiModelInfo {
  id: string;
  provider: string;
  name: string;
}

/** List Gateway AI models (for CheckOptionsForm). */
export async function listAiModels(): Promise<AiModelInfo[]> {
  const { fetch: f } = getApmeApiAdapter();
  const res = await f(apmeApiUrl('/ai/models'));
  if (!res.ok) {
    throw new Error(`Failed to list AI models: ${res.status}`);
  }
  return (await res.json()) as AiModelInfo[];
}
