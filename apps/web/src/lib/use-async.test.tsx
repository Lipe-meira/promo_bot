import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useAsync } from "./use-async";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useAsync", () => {
  it("goes from loading to ready", async () => {
    const { result } = renderHook(() => useAsync(async () => "data"));

    expect(result.current.status).toBe("loading");
    await waitFor(() => expect(result.current).toMatchObject({ status: "ready", data: "data" }));
  });

  it("reports a failure as an error state", async () => {
    const failure = new Error("boom");
    const { result } = renderHook(() =>
      useAsync(async () => {
        throw failure;
      }),
    );

    await waitFor(() => expect(result.current).toMatchObject({ status: "error", error: failure }));
  });

  it("drops a slow answer that arrives after a newer request", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const queue = [first, second];
    const { result } = renderHook(() => useAsync(() => queue.shift()!.promise));

    act(() => result.current.reload());
    await act(async () => second.resolve("fresh"));
    await waitFor(() => expect(result.current).toMatchObject({ status: "ready", data: "fresh" }));
    await act(async () => first.resolve("stale"));

    expect(result.current).toMatchObject({ status: "ready", data: "fresh" });
  });

  it("drops a slow failure that arrives after a newer success", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const queue = [first, second];
    const { result } = renderHook(() => useAsync(() => queue.shift()!.promise));

    act(() => result.current.reload());
    await act(async () => second.resolve("fresh"));
    await waitFor(() => expect(result.current).toMatchObject({ status: "ready", data: "fresh" }));
    await act(async () => first.reject(new Error("late failure")));

    expect(result.current).toMatchObject({ status: "ready", data: "fresh" });
  });

  it("keeps the data on screen while a reload is in flight", async () => {
    const next = deferred<string>();
    const answers = [Promise.resolve("v1"), next.promise];
    const { result } = renderHook(() => useAsync(() => answers.shift()!));
    await waitFor(() => expect(result.current).toMatchObject({ status: "ready", data: "v1" }));

    act(() => result.current.reload());

    expect(result.current).toMatchObject({ status: "ready", data: "v1" });
    await act(async () => next.resolve("v2"));
    await waitFor(() => expect(result.current).toMatchObject({ data: "v2" }));
  });

  it("ignores an answer that arrives after unmount", async () => {
    const pending = deferred<string>();
    const { result, unmount } = renderHook(() => useAsync(() => pending.promise));

    unmount();
    await act(async () => pending.resolve("late"));

    expect(result.current.status).toBe("loading"); // never updated after unmount
  });
});
