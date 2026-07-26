// Shared hydrated / dirty / baseline / clearable-confirm skeleton for the
// config-editing pages (Settings, Resources→邮箱). Extracted with behavior
// strictly unchanged; every page-specific decision (messages, hydrate
// mapping, PUT payload, post-save rehydrate) stays in the page via callbacks.
//
// Invariants owned here (locked by SettingsPage.test.jsx / MailTab.test.jsx):
//  1. Save is a no-op until a GET succeeded (hydrated) and no load is running.
//  2. GET 401 → session cleared (auth401) and hydrated=false so Save stays blocked.
//  3. load({force:true}) while dirty asks window.confirm first; cancel = no GET.
//  4. Clearing a clearable field asks window.confirm on save; cancel = no PUT.
//  5. Successful save clears dirty and applies the page's rehydrate result.
//
// Option callbacks (all page-provided; see Settings/MailTab for usage):
//   loadRequest: async () => config          — GET + unwrap
//   hydrate: (config) => { form, baseline }  — map config into page state
//   onLoaded?: async (config, form)          — post-load side effects
//   onLoadError?: (e)                        — non-401 load failure feedback
//   confirmReload: { message, onCancel? }    — dirty-reload confirm copy
//   onSaveBlocked?: ()                       — Save clicked before hydrated
//   confirmClear?: (form, baseline) => { message } | null
//   onSaveCancelled?: ()                     — clearable confirm declined
//   saveRequest: async (form) => data        — build partial + PUT
//   onSaved?: (data, form) => { form?, baseline? } | void
//   onSaveError?: (e)                        — non-401 save failure feedback
import { useCallback, useRef, useState } from "preact/hooks";
import { auth401 } from "./http.js";
import { useBusy } from "./useBusy.js";

export function useConfigForm(options) {
  const { empty, initialBaseline } = options;
  const [form, setForm] = useState(empty);
  const [hydrated, setHydrated] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [baseline, setBaseline] = useState(initialBaseline);
  const busyApi = useBusy();
  const { setBusy, is: busyIs } = busyApi;

  // Callbacks read page state (e.g. primaryProvider) — keep them fresh via a
  // ref so load/save never close over a stale render.
  const opts = useRef(options);
  opts.current = options;

  const set = useCallback((partial) => {
    setDirty(true);
    setForm((p) => ({ ...p, ...partial }));
  }, []);

  const load = useCallback(
    async ({ force = false } = {}) => {
      const o = opts.current;
      if (force && dirty) {
        if (!window.confirm(o.confirmReload.message)) {
          o.confirmReload.onCancel?.();
          return;
        }
      }
      setBusy("load");
      try {
        const config = await o.loadRequest();
        const next = o.hydrate(config);
        setForm(next.form);
        setBaseline(next.baseline);
        setDirty(false);
        setHydrated(true);
        await o.onLoaded?.(config, next.form);
      } catch (e) {
        // Always clear hydrated on failure (incl. 401) so Save stays blocked.
        setHydrated(false);
        if (auth401(e)) return;
        o.onLoadError?.(e);
      } finally {
        setBusy("");
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dirty],
  );

  const save = useCallback(async () => {
    const o = opts.current;
    if (!hydrated || busyIs("load")) {
      o.onSaveBlocked?.();
      return;
    }
    const clearing = o.confirmClear?.(form, baseline);
    if (clearing) {
      if (!window.confirm(clearing.message)) {
        o.onSaveCancelled?.();
        return;
      }
    }
    setBusy("save");
    try {
      const data = await o.saveRequest(form);
      const next = o.onSaved?.(data, form);
      if (next && next.form !== undefined) {
        setForm((p) =>
          typeof next.form === "function" ? next.form(p) : next.form,
        );
      }
      if (next && next.baseline !== undefined) setBaseline(next.baseline);
      setDirty(false);
    } catch (e) {
      if (auth401(e)) {
        setHydrated(false);
        return;
      }
      o.onSaveError?.(e);
    } finally {
      setBusy("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrated, form, baseline, busyIs]);

  return {
    form,
    set,
    setForm,
    hydrated,
    setHydrated,
    dirty,
    setDirty,
    baseline,
    setBaseline,
    load,
    save,
    ...busyApi,
    busyIs,
  };
}
