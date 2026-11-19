import { useCallback, useEffect, useRef, useState } from 'react';
import { request } from '../../lib/workspace';
import type { CatalogItem } from './SourceForm';
export function useCatalog() {
  const [modules, setModules] = useState<CatalogItem[]>([]);
  const [campaigns, setCampaigns] = useState<CatalogItem[]>([]);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++generation.current;
    const results = await Promise.allSettled([
      request<{ modules: CatalogItem[] }>('/api/modules'),
      request<{ campaigns: CatalogItem[] }>('/api/campaigns'),
    ]);
    if (current !== generation.current) return;
    if (results[0].status === 'fulfilled') setModules(results[0].value.modules ?? []);
    if (results[1].status === 'fulfilled') setCampaigns(results[1].value.campaigns ?? []);
    setError(
      results
        .filter((r) => r.status === 'rejected')
        .map((r) => String((r as PromiseRejectedResult).reason))
        .join('；'),
    );
  }, []);
  useEffect(() => {
    void refresh();
    return () => {
      generation.current++;
    };
  }, [refresh]);
  return { modules, campaigns, error, refresh };
}
