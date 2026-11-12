// HoneyMOM design system entry. app.use(installDesign) in main.ts: globally registers Hm* components and v-ai / v-ai-rows directives.
import type { App } from 'vue';
import { vAi, vAiRows } from '../ai/directive';
import HmCard from './components/HmCard.vue';
import HmDataTable from './components/HmDataTable.vue';
import HmEmpty from './components/HmEmpty.vue';
import HmKpi from './components/HmKpi.vue';
import HmNumber from './components/HmNumber.vue';
import HmPageHead from './components/HmPageHead.vue';
import HmSkeleton from './components/HmSkeleton.vue';
import HmStatus from './components/HmStatus.vue';
import HmTag from './components/HmTag.vue';
import HmToolbar from './components/HmToolbar.vue';
import HmAutoScroll from './components/HmAutoScroll.vue';

export { HmAutoScroll, HmCard, HmDataTable, HmEmpty, HmKpi, HmNumber, HmPageHead, HmSkeleton, HmStatus, HmTag, HmToolbar };
export * from './format';
export { reducedMotion, useTickUp } from './motion';
export { CH, PALETTE, STATE_COLOR, seq, targetLine } from './chart';

export function installDesign(app: App) {
  app.component('HmCard', HmCard).component('HmDataTable', HmDataTable).component('HmEmpty', HmEmpty).component('HmKpi', HmKpi)
    .component('HmNumber', HmNumber).component('HmPageHead', HmPageHead).component('HmSkeleton', HmSkeleton).component('HmStatus', HmStatus).component('HmTag', HmTag).component('HmToolbar', HmToolbar);
  app.component('HmAutoScroll', HmAutoScroll);
  app.directive('ai', vAi).directive('ai-rows', vAiRows);
}
