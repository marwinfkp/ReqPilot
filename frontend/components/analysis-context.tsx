"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { api, errorMessage } from "@/lib/api";
import type { AnalysisPayload } from "@/lib/types";

type AnalysisState = {
  analysis: AnalysisPayload | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
};

const AnalysisContext = createContext<AnalysisState>({ analysis: null, loading: true, error: null, reload: async () => {} });

/** Loads one analysis (by id or "latest") for all result pages under /analyses/[id]. */
export function AnalysisProvider({ id, children }: { id: string; children: React.ReactNode }) {
  const [analysis, setAnalysis] = useState<AnalysisPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setAnalysis(await api<AnalysisPayload>(`/api/analyses/${id}`));
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    setLoading(true);
    reload();
  }, [reload]);

  return <AnalysisContext.Provider value={{ analysis, loading, error, reload }}>{children}</AnalysisContext.Provider>;
}

export const useAnalysis = () => useContext(AnalysisContext);

/** For pages rendered inside the provider once the analysis has loaded. */
export function useLoadedAnalysis() {
  const state = useAnalysis();
  return { ...state, analysis: state.analysis as AnalysisPayload };
}
