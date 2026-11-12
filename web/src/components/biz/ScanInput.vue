<template>
  <div class="scan" :class="{ 'scan-big': big }">
    <el-input ref="inp" v-model="val" :size="big ? 'large' : 'default'" :placeholder="placeholder" clearable data-test="scan-input" inputmode="text"
      autocomplete="off" @keyup.enter="emitScan(val)">
      <template #prefix><el-icon><Postcard /></el-icon></template>
      <template #append>
        <el-button data-test="scan-camera" :title="$t('scanInput.scanWithCamera')" @click="openCam"><el-icon><Camera /></el-icon><span class="bt">{{ $t('common.scan') }}</span></el-button>
      </template>
    </el-input>
    <div class="hint">{{ hint }}</div>

    <el-dialog v-model="cam" :title="$t('common.scan')" width="min(520px, 96vw)" append-to-body destroy-on-close @closed="stopCam" class="scan-dlg">
      <div class="vwrap">
        <video ref="video" data-test="scan-video" playsinline muted autoplay />
        <div class="frame" />
        <div v-if="camErr" class="err">{{ camErr }}</div>
      </div>
      <div class="status" data-test="scan-status">{{ status }}</div>
      <template #footer>
        <label class="pick">
          <input ref="file" type="file" accept="image/*" capture="environment" data-test="scan-image-input" @change="fromFile" />
          <el-button @click="file?.click()"><el-icon><Picture /></el-icon>{{ $t('scanInput.photoRecognizeFromImage') }}</el-button>
        </label>
        <el-button @click="cam = false">{{ $t('common.close') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Scan input: ① barcode scanner (keyboard wedge: fast input + Enter, listened globally, works even when focus isn't in another input) ② camera (prefers the browser's BarcodeDetector,
// otherwise loads the locally bundled ZXing /vendor/zxing-library-0.21.3.min.js, no CDN) ③ take photo / pick image to decode (mobile browsers deny the camera without HTTPS).
import { nextTick, onMounted, onUnmounted, ref } from 'vue';

const props = withDefaults(defineProps<{ placeholder?: string; big?: boolean; global?: boolean; hint?: string }>(), {
  placeholder: $t('scanInput.scanAPartCodeWo'), big: false, global: true, hint: $t('scanInput.supportsBarcodeScannersCameraAnd'),
});
const emit = defineEmits<{ scan: [code: string] }>();
const val = ref(''), cam = ref(false), status = ref(''), camErr = ref('');
const video = ref<HTMLVideoElement>(), file = ref<HTMLInputElement>(), inp = ref<any>();
let stream: MediaStream | null = null, timer = 0, stopped = true;

function emitScan(code: string) {
  const c = (code || '').trim();
  if (!c) return;
  val.value = c;
  emit('scan', c);
}
defineExpose({ set: (v: string) => { val.value = v; }, focus: () => inp.value?.focus() });

// ---- Barcode scanner: a burst of fast input (gap < 150ms between chars) + Enter ----
let buf = '', last = 0;
function onKey(e: KeyboardEvent) {
  const t = e.target as HTMLElement;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;      // Inside an input box, the input handles it itself
  const now = performance.now();
  if (now - last > 150) buf = '';         // Scanners usually type < 30ms per char; relaxed to 150ms so slow PCs still capture everything
  last = now;
  if (e.key === 'Enter') { if (buf.length >= 6) { emitScan(buf); e.preventDefault(); } buf = ''; return; }
  if (e.key.length === 1) buf += e.key;
}
onMounted(() => { if (props.global) window.addEventListener('keydown', onKey, true); });
onUnmounted(() => { window.removeEventListener('keydown', onKey, true); stopCam(); });

// ---- Decoder ----
const FORMATS = ['qr_code', 'code_128', 'code_39', 'ean_13', 'data_matrix'];
async function nativeDetector() {
  const BD: any = (window as any).BarcodeDetector;
  if (!BD) return null;
  try {
    const fm = await BD.getSupportedFormats();
    const use = FORMATS.filter((f) => fm.includes(f));
    return use.length ? new BD({ formats: use }) : null;
  } catch { return null; }
}
function loadZxing(): Promise<any> {
  const w: any = window;
  if (w.ZXing) return Promise.resolve(w.ZXing);
  return new Promise((ok, bad) => {
    const s = document.createElement('script');
    s.src = '/vendor/zxing-library-0.21.3.min.js';
    s.onload = () => ok(w.ZXing); s.onerror = () => bad(new Error($t('scanInput.failedToLoadTheScanner')));
    document.head.appendChild(s);
  });
}
function zxHints(Z: any) {
  const h = new Map();
  h.set(Z.DecodeHintType.POSSIBLE_FORMATS, [Z.BarcodeFormat.QR_CODE, Z.BarcodeFormat.CODE_128, Z.BarcodeFormat.CODE_39, Z.BarcodeFormat.EAN_13, Z.BarcodeFormat.DATA_MATRIX]);
  h.set(Z.DecodeHintType.TRY_HARDER, true);
  return h;
}

async function openCam() {
  cam.value = true; camErr.value = ''; status.value = $t('scanInput.openingCamera');
  await nextTick();
  if (!navigator.mediaDevices?.getUserMedia) {
    camErr.value = $t('scanInput.thisPageCanTAccess'); status.value = ''; return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1280 } }, audio: false });
  } catch (e: any) {
    camErr.value = $t('scanInput.canTOpenTheCamera') + (e?.message || e) + $t('scanInput.usePhotoRecognizeFromImage'); status.value = ''; return;
  }
  const v = video.value!;
  stopped = false;
  const det = await nativeDetector();
  if (det) {
    v.srcObject = stream; await v.play().catch(() => undefined);
    status.value = $t('scanInput.pointAtABarcodeQr');
    const tick = async () => {
      if (stopped) return;
      try { const r = await det.detect(v); if (r?.length) { done(r[0].rawValue, 'native'); return; } } catch { /* frame not ready yet */ }
      timer = window.setTimeout(tick, 120);
    };
    tick();
    return;
  }
  try {
    const Z = await loadZxing();
    status.value = $t('scanInput.pointAtABarcodeQr2');
    v.srcObject = stream; await v.play().catch(() => undefined);
    // Grab frames ourselves and feed ZXing (more controllable than the library's continuous decode): draw to canvas → luminance → binarize → multi-format decode
    const reader = new Z.MultiFormatReader(); reader.setHints(zxHints(Z));
    const cv = document.createElement('canvas'), g = cv.getContext('2d', { willReadFrequently: true })!;
    const tick = () => {
      if (stopped) return;
      if (v.readyState >= 2 && v.videoWidth) {
        cv.width = v.videoWidth; cv.height = v.videoHeight; g.drawImage(v, 0, 0);
        try {
          const bmp = new Z.BinaryBitmap(new Z.HybridBinarizer(new Z.HTMLCanvasElementLuminanceSource(cv)));
          const res = reader.decode(bmp); done(res.getText(), 'zxing'); return;
        } catch { /* nothing decoded in this frame */ }
      }
      timer = window.setTimeout(tick, 150);
    };
    tick();
  } catch (e: any) { camErr.value = String(e?.message || e); }
}
function done(code: string, how: string) {
  status.value = $t('scanInput.recognized0', [code]);
  (window as any).__lastScan = { code, how };
  stopCam(); cam.value = false; emitScan(code);
}
function stopCam() {
  stopped = true; clearTimeout(timer);
  stream?.getTracks().forEach((t) => t.stop()); stream = null;
}
async function fromFile(ev: Event) {
  const f = (ev.target as HTMLInputElement).files?.[0];
  if (!f) return;
  status.value = $t('scanInput.recognizingImage'); camErr.value = '';
  const url = URL.createObjectURL(f);
  try {
    const det = await nativeDetector();
    if (det) {
      const bmp = await createImageBitmap(f);
      const r = await det.detect(bmp);
      if (r?.length) { done(r[0].rawValue, 'native-image'); return; }
    }
    const Z = await loadZxing();
    const reader = new Z.BrowserMultiFormatReader(zxHints(Z));
    const res = await reader.decodeFromImageUrl(url);
    done(res.getText(), 'zxing-image');
  } catch { camErr.value = $t('scanInput.noBarcodeFoundInThe'); status.value = ''; }
  finally { URL.revokeObjectURL(url); (ev.target as HTMLInputElement).value = ''; }
}
</script>

<style scoped>
.scan { width: 100%; }
.scan .bt { margin-left: 4px; }
.hint { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); margin-top: 4px; }
.scan-big :deep(.el-input__wrapper) { min-height: 52px; font-size: var(--hm-fs-lg); }
.scan-big :deep(.el-input-group__append .el-button) { min-height: 52px; font-size: var(--hm-fs-md); }
.vwrap { position: relative; background: #0b1220; border-radius: var(--hm-r-md); overflow: hidden; aspect-ratio: 4 / 3; }
.vwrap video { width: 100%; height: 100%; object-fit: cover; display: block; }
.frame { position: absolute; inset: 18% 14%; border: 2px solid rgba(255, 255, 255, .85); border-radius: var(--hm-r-md); box-shadow: 0 0 0 999px rgba(11, 18, 32, .35); }
.err { position: absolute; inset: auto 12px 12px; background: var(--hm-fault-soft); color: var(--hm-fault-ink); padding: 8px 10px; border-radius: var(--hm-r-sm); font-size: var(--hm-fs-xs); }
.status { margin-top: 8px; font-size: var(--hm-fs-xs); color: var(--hm-text-2); min-height: 18px; }
.pick { margin-right: 8px; } .pick input { display: none; }
</style>
