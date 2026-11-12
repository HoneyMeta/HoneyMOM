// Motion helpers: number tick-up, reduce-motion check.
import { onUnmounted, ref, watch, type Ref } from 'vue';

export const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Animate a number smoothly from old to new value (600ms, ease-out). Jumps straight to the new value if the user set "reduce motion". */
export function useTickUp(source: Ref<number | null | undefined> | (() => number | null | undefined), duration = 600) {
  const shown = ref<number | null>(null);
  let raf = 0;
  const get = typeof source === 'function' ? source : () => source.value;
  watch(get, (to) => {
    cancelAnimationFrame(raf);
    if (to === null || to === undefined || isNaN(Number(to))) { shown.value = to as any; return; }
    const from = shown.value;
    if (from === null || from === undefined || reducedMotion() || from === to) { shown.value = Number(to); return; }
    const t0 = performance.now();
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / duration);
      const e = 1 - Math.pow(1 - k, 3);
      shown.value = from + (Number(to) - from) * e;
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
  }, { immediate: true });
  onUnmounted(() => cancelAnimationFrame(raf));
  return shown;
}
