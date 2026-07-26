// Single-slot busy-tag state. Replaces the setBusy("tag") / busy === "tag"
// string pairs that were duplicated across every ops page.
import { useCallback, useState } from "preact/hooks";

export function useBusy(initial = "") {
  const [busy, setBusy] = useState(initial);
  const is = useCallback((tag) => busy === tag, [busy]);
  const run = useCallback(async (tag, fn) => {
    setBusy(tag);
    try {
      return await fn();
    } finally {
      setBusy("");
    }
  }, []);
  return { busy, setBusy, is, any: busy !== "", run };
}
