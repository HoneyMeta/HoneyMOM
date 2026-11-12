<template>
  <header class="hm-ph" :class="{ art: !!art }" data-tour="page-head">
    <div class="main">
      <div class="tl">
        <h2>{{ title }}</h2>
        <slot name="meta" />
        <HelpButton />
      </div>
      <p v-if="sub || $slots.sub" class="sub"><slot name="sub">{{ sub }}</slot></p>
      <div v-if="art && $slots.default" class="acts in"><slot /></div>
    </div>
    <div v-if="!art && $slots.default" class="acts"><slot /></div>
    <img v-if="art" class="pa" :src="`/art/head-${art}.webp`" alt="" aria-hidden="true" draggable="false" />
  </header>
</template>

<script setup lang="ts">
// Page header: title + "?" guided tour + description + right-side actions (default slot).
// art: module illustration for overview pages (equipment / quality / production / material / foundry / energy, see public/art/manifest.json); the header then becomes a light banner with actions below the description.
// <HmPageHead title="Quality Overview" sub="Last 7 days" art="quality"><el-radio-group …/></HmPageHead>
import { onMounted, onUnmounted } from 'vue';
import HelpButton from '../../tour/HelpButton.vue';
import { tour } from '../../tour/engine';

defineProps<{ title: string; sub?: string; art?: string }>();
onMounted(() => { tour.heads++; });
onUnmounted(() => { tour.heads = Math.max(0, tour.heads - 1); });
</script>

<style scoped>
.hm-ph { display: flex; align-items: center; gap: var(--hm-sp-3); margin-bottom: var(--hm-sp-4); min-height: calc(40px * var(--hm-scale)); flex-wrap: wrap; }
.main { flex: 1 1 360px; min-width: 0; }
.tl { display: flex; align-items: center; gap: var(--hm-sp-2); flex-wrap: wrap; }
h2 { margin: 0; font-size: var(--hm-fs-xl); font-weight: var(--hm-fw-semibold); letter-spacing: .01em; color: var(--hm-text); line-height: 1.3; }
.sub { margin: 2px 0 0; color: var(--hm-text-3); font-size: var(--hm-fs-xs); line-height: 1.6; max-width: 980px; }
.acts { display: flex; align-items: center; gap: var(--hm-sp-2); flex-wrap: wrap; justify-content: flex-end; }
.acts.in { justify-content: flex-start; margin-top: var(--hm-sp-3); }
/* Banner (with module illustration) */
.hm-ph.art { position: relative; overflow: hidden; padding: var(--hm-sp-4) var(--hm-sp-5); min-height: calc(112px * var(--hm-scale)); border-radius: var(--hm-r-lg);
  background: linear-gradient(100deg, var(--hm-surface) 0%, var(--hm-surface) 46%, rgba(255, 255, 255, .55) 100%), var(--hm-surface-2); border: 1px solid var(--hm-border); box-shadow: var(--hm-e1); }
.hm-ph.art .main { position: relative; z-index: 1; max-width: 62%; }
.pa { position: absolute; right: var(--hm-sp-4); bottom: 0; height: 100%; max-height: calc(132px * var(--hm-scale)); width: auto; object-fit: contain; object-position: right bottom;
  -webkit-mask-image: linear-gradient(90deg, transparent 0, #000 22%); mask-image: linear-gradient(90deg, transparent 0, #000 22%); pointer-events: none; user-select: none; }
@media (max-width: 1100px) { .pa { display: none; } .hm-ph.art .main { max-width: none; } }
</style>
