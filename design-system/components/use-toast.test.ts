import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { toast, useToast } from "./use-toast";

// useToast's dispatch/reducer/listeners/memoryState are module-private
// (by design — see use-toast.ts's own upstream-shadcn provenance), so
// every test here drives the reducer only through the public toast()/
// useToast() surface. That module state is a singleton shared across
// every test in this file, so each test must leave it empty for the
// next one.
afterEach(async () => {
  const { result } = renderHook(() => useToast());

  act(() => {
    result.current.dismiss();
  });

  await waitFor(() => expect(result.current.toasts).toHaveLength(0));
});

describe("useToast reducer", () => {
  it("adds a new toast to the front of the list (ADD_TOAST)", () => {
    const { result } = renderHook(() => useToast());

    act(() => {
      toast({ title: "First" });
    });
    act(() => {
      toast({ title: "Second" });
    });

    expect(result.current.toasts.map((t) => t.title)).toEqual(["Second", "First"]);
  });

  it("caps the toast list at the configured limit, dropping the oldest", () => {
    const { result } = renderHook(() => useToast());

    act(() => {
      toast({ title: "One" });
      toast({ title: "Two" });
      toast({ title: "Three" });
      toast({ title: "Four" });
    });

    expect(result.current.toasts).toHaveLength(3);
    expect(result.current.toasts.map((t) => t.title)).toEqual(["Four", "Three", "Two"]);
  });

  it("assigns each toast a unique id", () => {
    const { result } = renderHook(() => useToast());

    act(() => {
      toast({ title: "A" });
      toast({ title: "B" });
    });

    const [latest, earliest] = result.current.toasts;
    expect(latest.id).not.toBe(earliest.id);
  });

  it("merges a partial update onto the matching toast via the returned update() handle (UPDATE_TOAST)", () => {
    const { result } = renderHook(() => useToast());
    let handle!: ReturnType<typeof toast>;

    act(() => {
      handle = toast({ title: "Original", description: "before" });
    });
    act(() => {
      handle.update({ description: "after" });
    });

    expect(result.current.toasts[0]).toMatchObject({ title: "Original", description: "after" });
  });

  it("marks a toast closed without removing it when dismissed by id (DISMISS_TOAST)", () => {
    const { result } = renderHook(() => useToast());
    let handle!: ReturnType<typeof toast>;

    act(() => {
      handle = toast({ title: "Dismiss me" });
    });
    act(() => {
      handle.dismiss();
    });

    expect(result.current.toasts[0]).toMatchObject({ id: handle.id, open: false });
  });

  it("closes every toast when the hook's dismiss() is called with no id", () => {
    const { result } = renderHook(() => useToast());

    act(() => {
      toast({ title: "One" });
      toast({ title: "Two" });
    });
    act(() => {
      result.current.dismiss();
    });

    expect(result.current.toasts.every((t) => t.open === false)).toBe(true);
  });

  it("removes a dismissed toast from state after the removal delay elapses (REMOVE_TOAST)", async () => {
    const { result } = renderHook(() => useToast());

    act(() => {
      toast({ title: "Transient" });
    });
    act(() => {
      result.current.dismiss();
    });

    await waitFor(() => expect(result.current.toasts).toHaveLength(0));
  });
});
