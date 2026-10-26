import { useCallback, useEffect, useMemo, useState } from 'react';
import { DEMO_CAPABILITIES, defaultStrategy, modelsFor, runOptions } from '../contract/capabilities';
import type { Capabilities } from '../contract/v1.gen';
import { getPref, setPref } from '../lib/prefs';

const TIER: Record<string, string> = { low: 'Low cost', medium: 'Medium cost', high: 'Higher cost' };
const SPEED: Record<string, string> = { fast: 'Fast', medium: 'Medium', slow: 'Slower' };
/** "Low cost · Fast" for a strategy row. */
export const tierLabel = (cost?: string, speed?: string) => [cost && TIER[cost], speed && SPEED[speed]].filter(Boolean).join(' · ');

/**
 * Strategy/model picker state (contract 1.4.0). The live API serves GET /api/capabilities; mock mode
 * (and any error) uses DEMO_CAPABILITIES. A choice is a short UI pref (ids only); an id the server no longer
 * offers, or one marked unavailable, falls back to the default. `options()` is read at send time.
 */
export function useStrategy(load?: () => Promise<Capabilities>) {
  const [caps, setCaps] = useState<Capabilities>(DEMO_CAPABILITIES);
  const [strategyPick, setStrategyPick] = useState(() => getPref('strategy'));
  const [modelPick, setModelPick] = useState(() => getPref('model'));
  useEffect(() => {
    if (!load) return;
    let on = true;
    void load().then((c) => { if (on) setCaps(c); }, () => undefined);
    return () => { on = false; };
  }, [load]);
  const strategy = useMemo(() => {
    const s = caps.strategies.find((x) => x.id === strategyPick && x.available !== false);
    return s ?? defaultStrategy(caps) ?? caps.strategies[0];
  }, [caps, strategyPick]);
  const models = useMemo(() => (strategy ? modelsFor(caps, strategy.id) : []), [caps, strategy]);
  const model = models.find((m) => m.id === modelPick && m.available !== false) ?? models.find((m) => m.id === caps.defaults.model) ?? models[0];
  const setStrategy = useCallback((id: string) => { setStrategyPick(id); setPref('strategy', id); }, []);
  const setModel = useCallback((id: string) => { setModelPick(id); setPref('model', id); }, []);
  const sId = strategy?.id, mId = model?.id;
  const options = useCallback(() => {
    const o = runOptions(caps, sId, mId);
    return Object.keys(o).length ? o : undefined;
  }, [caps, sId, mId]);
  const isDefault = !!strategy && strategy.id === caps.defaults.strategy && (!model || model.id === caps.defaults.model);
  return { caps, strategy, model, models, setStrategy, setModel, options, isDefault };
}
export type StrategyState = ReturnType<typeof useStrategy>;
