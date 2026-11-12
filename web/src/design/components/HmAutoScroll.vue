<template>
  <div ref="box" class="hm-autoscroll" :class="{ running: overflow && !still }" :style="{ height: height ? (typeof height === 'number' ? height + 'px' : height) : undefined }"
    @mouseenter="hover = true" @mouseleave="hover = false" data-test="autoscroll">
    <div class="track" :style="{ transform: overflow && !still ? `translate3d(0, ${-offset}px, 0)` : undefined }">
      <div ref="first"><slot /></div>
      <div v-if="overflow && !still" aria-hidden="true" class="copy" :style="{ paddingTop: gap + 'px' }"><slot /></div>
    </div>
  </div>
</template>

<script setup lang="ts">
// Auto-scroll container (long tables, scrolling notices on shop-floor displays): scrolls up at a steady pace when content overflows, seamless loop; pauses on hover;
// falls back to a normal scrollbar with system "reduce motion" or :paused. Does nothing when content fits.
//   <HmAutoScroll :speed="28" height="100%"> <div v-for="r in rows" class="row">…</div> </HmAutoScroll>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { reducedMotion } from '../motion';

const props = withDefaults(defineProps<{ speed?: number; height?: number | string; gap?: number; paused?: boolean; pauseOnHover?: boolean }>(),
  { speed: 24, gap: 24, paused: false, pauseOnHover: true });
const box = ref<HTMLElement>(), first = ref<HTMLElement>();
const overflow = ref(false), offset = ref(0), hover = ref(false);
const still = computed(() => props.paused || reducedMotion());
let raf = 0, last = 0, ro: ResizeObserver | null = null, contentH = 0;

function measure() {
  if (!box.value || !first.value) return;
  contentH = first.value.offsetHeight;
  overflow.value = contentH > box.value.clientHeight + 2;
  if (!overflow.value) offset.value = 0;
}
function step(t: number) {
  const dt = last ? Math.min(100, t - last) : 0;
  last = t;
  if (overflow.value && !still.value && !(props.pauseOnHover && hover.value)) {
    offset.value += (props.speed * dt) / 1000;
    const loop = contentH + props.gap;
    if (offset.value >= loop) offset.value -= loop;
  }
  raf = requestAnimationFrame(step);
}
onMounted(() => {
  ro = new ResizeObserver(() => measure());
  if (box.value) ro.observe(box.value);
  if (first.value) ro.observe(first.value);
  nextTick(measure);
  raf = requestAnimationFrame(step);
});
watch(() => props.paused, () => nextTick(measure));
onUnmounted(() => { cancelAnimationFrame(raf); ro?.disconnect(); });
defineExpose({ reset: () => { offset.value = 0; } });
</script>

<style scoped>
.hm-autoscroll { position: relative; overflow: auto; min-height: 0; }
.hm-autoscroll.running { overflow: hidden; -webkit-mask-image: linear-gradient(transparent, #000 14px, #000 calc(100% - 14px), transparent); mask-image: linear-gradient(transparent, #000 14px, #000 calc(100% - 14px), transparent); }
.track { will-change: transform; }
</style>
