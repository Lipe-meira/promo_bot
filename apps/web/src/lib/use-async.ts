"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type AsyncState<T> =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; data: T };

/**
 * Runs `load` on mount and on `reload()`. A response that arrives after a newer request (or after
 * unmount) is dropped, so a slow answer can never overwrite a fresher one. Nothing is cached
 * outside the component that calls it.
 */
export function useAsync<T>(load: () => Promise<T>): AsyncState<T> & { reload: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ status: "loading" });
  const loadRef = useRef(load);
  loadRef.current = load;
  const generation = useRef(0);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const mine = ++generation.current;
    // A reload keeps showing the data already on screen until the fresh answer replaces it.
    setState((previous) => (previous.status === "ready" ? previous : { status: "loading" }));
    loadRef.current().then(
      (data) => {
        if (generation.current === mine) setState({ status: "ready", data });
      },
      (error: unknown) => {
        if (generation.current === mine) setState({ status: "error", error });
      },
    );
    return () => {
      generation.current++;
    };
  }, [tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { ...state, reload };
}
