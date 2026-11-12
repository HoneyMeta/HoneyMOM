<template>
  <div class="dv" :class="{ tall }" data-test="drawing-viewer">
    <div class="bar">
      <b class="t">{{ doc.title }}</b><HmTag v-if="doc.version" mono>{{ doc.version }}</HmTag>
      <span class="muted small">{{ KIND[doc.file_type] || doc.file_type }} · {{ doc.file_name }}</span>
      <span class="grow" />
      <template v-if="doc.file_type === 'dxf'">
        <el-button size="small" @click="zoom(1.25)" :title="$t('common.zoomIn')"><el-icon><ZoomIn /></el-icon></el-button>
        <el-button size="small" @click="zoom(0.8)" :title="$t('common.zoomOut')"><el-icon><ZoomOut /></el-icon></el-button>
        <el-button size="small" @click="reset" :title="$t('drawingViewer.fitToWindow')"><el-icon><FullScreen /></el-icon></el-button>
      </template>
      <el-button size="small" tag="a" :href="url" target="_blank" :title="$t('drawingViewer.openInNewWindow')"><el-icon><TopRight /></el-icon></el-button>
    </div>
    <div class="stage" ref="stage" @wheel.prevent="onWheel" @pointerdown="down" @pointermove="move" @pointerup="up" @pointerleave="up">
      <iframe v-if="doc.file_type === 'pdf'" :src="url + '#toolbar=0&view=FitH'" :title="$t('drawingViewer.pdfPreview')" data-test="pdf-frame" />
      <img v-else-if="doc.file_type === 'image'" :src="url" :alt="doc.title" data-test="img-preview" />
      <div v-else-if="doc.file_type === 'dxf'" class="svgw" data-test="dxf-svg" :style="{ transform: `translate(${tx}px, ${ty}px) scale(${k})` }" v-html="svg" />
      <HmEmpty v-else compact :title="$t('drawingViewer.noAttachments')" :desc="$t('drawingViewer.uploadAPdfImageOr')" />
      <div v-if="err" class="err">{{ err }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t, lang } from '../../i18n';
// Drawing preview: PDF (browser built-in viewer), images, DXF (server converts to SVG; wheel zoom, drag pan). No external services.
import { computed, ref, watch } from 'vue';
import { session, tok } from '../../api';

const props = withDefaults(defineProps<{ doc: any; tall?: boolean }>(), { tall: false });
const KIND: Record<string, string> = { pdf: 'PDF', image: $t('common.image'), dxf: $t('drawingViewer.dxfDrawing') };
const url = computed(() => `/api/kb/${props.doc.id}/file?token=${tok()}`);
const svg = ref(''), err = ref(''), k = ref(1), tx = ref(0), ty = ref(0);
let drag: any = null;

async function load() {
  svg.value = ''; err.value = ''; reset();
  if (props.doc?.file_type !== 'dxf') return;
  try {
    const r = await fetch(`/api/kb/${props.doc.id}/svg`, { headers: { 'X-Lang': lang, Authorization: 'Bearer ' + session.token } });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    svg.value = (await r.text()).replace(/<script[\s\S]*?<\/script>/gi, '');
  } catch (e: any) { err.value = $t('drawingViewer.dxfConversionFailed') + (e?.message || e); }
}
function zoom(f: number) { k.value = Math.min(20, Math.max(0.2, k.value * f)); }
function reset() { k.value = 1; tx.value = 0; ty.value = 0; }
function onWheel(e: WheelEvent) { if (props.doc?.file_type === 'dxf') zoom(e.deltaY < 0 ? 1.12 : 0.9); }
function down(e: PointerEvent) { if (props.doc?.file_type === 'dxf') drag = { x: e.clientX - tx.value, y: e.clientY - ty.value }; }
function move(e: PointerEvent) { if (drag) { tx.value = e.clientX - drag.x; ty.value = e.clientY - drag.y; } }
function up() { drag = null; }
watch(() => props.doc?.id, load, { immediate: true });
</script>

<style scoped>
.dv { display: flex; flex-direction: column; border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); background: var(--hm-surface); overflow: hidden; height: 420px; }
.dv.tall { height: min(72vh, 760px); }
.bar { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-bottom: 1px solid var(--hm-border); background: var(--hm-surface-2); font-size: var(--hm-fs-xs); flex-wrap: wrap; }
.bar .t { font-size: var(--hm-fs-sm); color: var(--hm-text); }
.stage { position: relative; flex: 1; overflow: hidden; background: #fff; cursor: grab; touch-action: none; }
.stage iframe { width: 100%; height: 100%; border: 0; }
.stage img { width: 100%; height: 100%; object-fit: contain; }
.svgw { width: 100%; height: 100%; transform-origin: center; }
.svgw :deep(svg) { width: 100%; height: 100%; display: block; }
.err { position: absolute; left: 12px; bottom: 12px; color: var(--hm-fault-ink); background: var(--hm-fault-soft); padding: 6px 10px; border-radius: var(--hm-r-sm); font-size: var(--hm-fs-xs); }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); }
</style>
