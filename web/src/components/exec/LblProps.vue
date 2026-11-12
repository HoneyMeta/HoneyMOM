<template>
  <div class="lp">
    <div class="lp-head">
      <HmTag tone="primary">{{ TYPE_CN[el.type] || el.type }}</HmTag>
      <span class="muted small">{{ $t('lblProps.n0Of1', [index + 1, count]) }}</span>
      <span class="grow" />
      <el-tooltip :content="$t('lblProps.bringForward')" placement="top"><el-button size="small" text :disabled="index >= count - 1" @click="emit('order', 1)"><el-icon><Top /></el-icon></el-button></el-tooltip>
      <el-tooltip :content="$t('lblProps.sendBackward')" placement="top"><el-button size="small" text :disabled="index <= 0" @click="emit('order', -1)"><el-icon><Bottom /></el-icon></el-button></el-tooltip>
      <el-tooltip :content="$t('lblProps.duplicateCtrlD')" placement="top"><el-button size="small" text @click="emit('dup')"><el-icon><CopyDocument /></el-icon></el-button></el-tooltip>
      <el-tooltip :content="$t('lblProps.deleteDelete')" placement="top"><el-button size="small" text type="danger" @click="emit('remove')"><el-icon><Delete /></el-icon></el-button></el-tooltip>
    </div>

    <!-- Content -->
    <template v-if="el.type === 'text' || el.type === 'barcode'">
      <div class="fl full">
        <span class="lb">{{ el.type === 'text' ? $t('lblProps.text') : $t('lblProps.barcodeData') }}</span>
        <el-input v-model="el[key]" :type="el.type === 'text' ? 'textarea' : 'text'" :autosize="{ minRows: 1, maxRows: 4 }" size="small" class="hm-mono" data-test="lbl-prop-text" />
      </div>
      <div class="vars">
        <el-dropdown trigger="click" max-height="320px" @command="(k: string) => insertVar(k)">
          <el-button size="small" plain>{{ $t('varInput.insertVariable') }}<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-for="v in vars" :key="v.key" :command="v.key"><span class="hm-mono" v-text="brace(v.key)" /><span class="muted vs">{{ String(v.sample ?? '') }}</span></el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <span class="muted small" v-text="HINT" />
      </div>
    </template>

    <div class="pf">
      <label class="fl"><span class="lb">X mm</span><el-input-number v-model="el.x" :step="0.5" :precision="2" size="small" controls-position="right" /></label>
      <label class="fl"><span class="lb">Y mm</span><el-input-number v-model="el.y" :step="0.5" :precision="2" size="small" controls-position="right" /></label>
      <template v-if="el.type === 'line'">
        <label class="fl"><span class="lb">X2 mm</span><el-input-number v-model="el.x2" :step="0.5" :precision="2" size="small" controls-position="right" /></label>
        <label class="fl"><span class="lb">Y2 mm</span><el-input-number v-model="el.y2" :step="0.5" :precision="2" size="small" controls-position="right" /></label>
        <label class="fl"><span class="lb">{{ $t('lblProps.lineWidthMm') }}</span><el-input-number v-model="el.t" :min="0.1" :step="0.1" :precision="2" size="small" controls-position="right" /></label>
      </template>
      <template v-else-if="el.type === 'text'">
        <label class="fl"><span class="lb">{{ $t('lblProps.textHeightMm') }}</span><el-input-number v-model="el.h" :min="1" :step="0.5" :precision="2" size="small" controls-position="right" /></label>
        <label class="fl"><span class="lb">{{ $t('lblProps.boxWidthMm0Auto') }}</span><el-input-number v-model="el.w" :min="0" :step="0.5" :precision="2" size="small" controls-position="right" /></label>
        <label class="fl"><span class="lb">{{ $t('lblProps.rotation') }}</span>
          <el-select v-model="el.rotate" size="small"><el-option v-for="r in [0, 90, 180, 270]" :key="r" :value="r" :label="r + '°'" /></el-select></label>
        <div class="fl"><span class="lb">{{ $t('lblProps.alignmentNeedsBoxWidth') }}</span>
          <el-radio-group v-model="el.align" size="small"><el-radio-button value="left">{{ $t('lblProps.left') }}</el-radio-button><el-radio-button value="center">{{ $t('lblProps.center') }}</el-radio-button><el-radio-button value="right">{{ $t('lblProps.right') }}</el-radio-button></el-radio-group></div>
        <div class="fl"><span class="lb">{{ $t('lblProps.bold') }}</span><el-switch v-model="el.bold" /></div>
      </template>
      <template v-else-if="el.type === 'barcode'">
        <label class="fl"><span class="lb">{{ $t('lblProps.widthMm') }}</span><el-input-number v-model="el.w" :min="1" :step="0.5" :precision="2" size="small" controls-position="right" @change="square('w')" /></label>
        <label class="fl"><span class="lb">{{ $t('lblProps.heightMm') }}</span><el-input-number v-model="el.h" :min="1" :step="0.5" :precision="2" size="small" controls-position="right" @change="square('h')" /></label>
        <label class="fl"><span class="lb">{{ $t('lblProps.symbology') }}</span>
          <el-select v-model="el.symbology" size="small" @change="square('w')"><el-option value="code128" label="Code128" /><el-option value="qr" label="QR" /><el-option value="datamatrix" label="DataMatrix" /></el-select></label>
        <label v-if="el.symbology === 'qr'" class="fl"><span class="lb">{{ $t('lblProps.errorCorrectionLevel') }}</span>
          <el-select v-model="el.ecc" size="small" placeholder="M"><el-option v-for="e in ['L', 'M', 'Q', 'H']" :key="e" :value="e" :label="e" /></el-select></label>
        <div v-if="el.symbology !== 'qr' && el.symbology !== 'datamatrix'" class="fl"><span class="lb">{{ $t('lblProps.showTextBelow') }}</span><el-switch v-model="el.hr" /></div>
        <div v-if="el.symbology !== 'qr'" class="fl"><span class="lb">GS1</span><el-switch v-model="el.gs1" /></div>
      </template>
      <template v-else-if="el.type === 'box'">
        <label class="fl"><span class="lb">{{ $t('lblProps.widthMm') }}</span><el-input-number v-model="el.w" :min="0.5" :step="0.5" :precision="2" size="small" controls-position="right" /></label>
        <label class="fl"><span class="lb">{{ $t('lblProps.heightMm') }}</span><el-input-number v-model="el.h" :min="0.5" :step="0.5" :precision="2" size="small" controls-position="right" /></label>
        <label class="fl"><span class="lb">{{ $t('lblProps.lineWidthMm') }}</span><el-input-number v-model="el.t" :min="0.1" :step="0.1" :precision="2" size="small" controls-position="right" /></label>
        <div class="fl"><span class="lb">{{ $t('lblProps.solidFill') }}</span><el-switch v-model="el.fill" /></div>
      </template>
      <template v-else-if="el.type === 'image'">
        <label class="fl"><span class="lb">{{ $t('lblProps.widthMm') }}</span><el-input-number v-model="el.w" :min="1" :step="0.5" :precision="2" size="small" controls-position="right" /></label>
        <label class="fl"><span class="lb">{{ $t('lblProps.heightMm') }}</span><el-input-number v-model="el.h" :min="1" :step="0.5" :precision="2" size="small" controls-position="right" /></label>
        <div class="fl full">
          <span class="lb">{{ $t('lblProps.imagePrintedInBlackAnd') }}</span>
          <div class="row">
            <el-button size="small" @click="file?.click()">{{ $t('lblProps.uploadImage') }}</el-button>
            <el-button size="small" :disabled="!ratio" @click="keepRatio">{{ $t('lblProps.fitHeightToAspectRatio') }}</el-button>
          </div>
          <input ref="file" type="file" accept="image/png,image/jpeg,image/gif,image/bmp" class="hide" @change="onFile" />
          <span v-if="el.src" class="muted small">{{ fmtNum(Math.round(String(el.src).length * 0.75 / 1024)) }} KB</span>
        </div>
      </template>
    </div>
    <p v-if="el.type === 'barcode'" class="muted small tip">{{ $t('lblProps.barcodesExcludeTheQuietZone') }}</p>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Label designer: property form for the selected element (edits the element object directly).
import { ElMessage } from 'element-plus';
import { computed, ref } from 'vue';
import { fmtNum } from '../../design';

const props = defineProps<{ el: any; vars: { key: string; sample: any }[]; index: number; count: number }>();
const emit = defineEmits<{ remove: []; dup: []; order: [dir: number] }>();
const brace = (k: string) => '{' + '{' + k + '}' + '}';
const HINT = $t('lblProps.syntax') + brace($t('lblProps.variable')) + $t('lblProps.or') + brace($t('lblProps.variableDefault'));
const TYPE_CN: Record<string, string> = { text: $t('common.text'), barcode: $t('lblProps.barcode'), line: $t('lblProps.line'), box: $t('lblProps.box'), image: $t('common.image') };
const key = computed(() => (props.el.type === 'text' ? 'text' : 'data'));
const file = ref<HTMLInputElement>();
const ratio = ref(0);

function insertVar(k: string) { props.el[key.value] = String(props.el[key.value] ?? '') + `{{${k}}}`; }
function square(which: 'w' | 'h') {
  const el = props.el;
  if (['qr', 'datamatrix'].includes(el.symbology)) { const s = Number(which === 'w' ? el.w : el.h) || 20; el.w = s; el.h = s; delete el.hr; }
}
function onFile(ev: Event) {
  const f = (ev.target as HTMLInputElement).files?.[0];
  if (!f) return;
  if (f.size > 600 * 1024) { ElMessage.warning($t('lblProps.imageTooLarge600Kb')); return; }
  const rd = new FileReader();
  rd.onload = () => {
    const src = String(rd.result || '');
    const img = new Image();
    img.onload = () => { ratio.value = img.naturalHeight / Math.max(1, img.naturalWidth); props.el.src = src; };
    img.onerror = () => ElMessage.error($t('lblProps.unrecognizedImageFormat'));
    img.src = src;
  };
  rd.readAsDataURL(f);
  (ev.target as HTMLInputElement).value = '';
}
function keepRatio() { if (ratio.value) props.el.h = +(Number(props.el.w || 20) * ratio.value).toFixed(1); }
</script>

<style scoped>
.lp { display: flex; flex-direction: column; gap: 10px; }
.lp-head { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.lp-head .el-button { margin: 0; padding: 4px 6px; }
.pf { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 10px; }
.fl { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.fl.full { grid-column: 1 / -1; }
.fl :deep(.el-input-number), .fl :deep(.el-select) { width: 100%; }
.lb { font-size: var(--hm-fs-2xs); color: var(--hm-text-2); }
.vars { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.vs { margin-left: 10px; font-size: var(--hm-fs-2xs); }
.row { display: flex; gap: 6px; flex-wrap: wrap; }
.hide { display: none; }
.tip { margin: 0; line-height: 1.5; }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); } .grow { flex: 1; }
</style>
