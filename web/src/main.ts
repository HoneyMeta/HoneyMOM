import 'element-plus/dist/index.css';
import './design/tokens.css';
import './design/base.css';
import * as Icons from '@element-plus/icons-vue';
import ElementPlus from 'element-plus';
import elEn from 'element-plus/es/locale/lang/en';
import elZhCn from 'element-plus/es/locale/lang/zh-cn';
import { createApp } from 'vue';
import { i18n, isZh } from './i18n';
import App from './App.vue';
import { setAiRouter } from './ai';
import { installDesign } from './design';
import { router } from './router';
import { installTour } from './tour';
import { installPerm } from './perm';

document.documentElement.classList.remove('dark');      // Light theme only
const app = createApp(App);
for (const [k, c] of Object.entries(Icons)) app.component(k, c as any);
setAiRouter(router);
installTour(router);
app.use(i18n).use(ElementPlus, { locale: isZh ? elZhCn : elEn }).use(installDesign).use(installPerm).use(router).mount('#app');
