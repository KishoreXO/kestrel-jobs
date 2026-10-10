import { useEffect } from "react";

/** Runs an async loader on mount and whenever it changes (e.g. when its filters change). */
export function useLoader(load: () => Promise<unknown>) {
  useEffect(() => {
    void load();
  }, [load]);
}
