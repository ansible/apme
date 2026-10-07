import { useEffect, useState } from 'react';
import { useApmeApi } from '../api/apmeApiAdapter';
import { getFeedbackEnabled } from '../services/api';

export function useFeedbackEnabled(): boolean {
  const api = useApmeApi();
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    let active = true;
    setEnabled(false);
    getFeedbackEnabled(api)
      .then((r) => { if (active) setEnabled(r.enabled); })
      .catch(() => { if (active) setEnabled(false); });
    return () => { active = false; };
  }, [api]);

  return enabled;
}
