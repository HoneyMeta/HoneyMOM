<template>
  <div class="login hm-grid-bg">
    <div class="side">
      <div class="brand"><img src="/brand/mark.svg" alt="" /><div><b>{{ $t('common.honeymom') }}</b><small>{{ $t('topBar.manufacturingOperationsManagement') }}</small></div></div>
      <h1>{{ $t('login.everyPartEveryHeatEvery') }}<br />{{ $t('login.visible') }}<span class="ai">{{ $t('login.andExplainedByAi') }}</span>{{ $t('home.k3429') }}</h1>
      <ul class="pts">
        <li><i />{{ $t('login.productionQualityMaintenanceInventor') }}</li>
        <li><i />{{ $t('login.realTimeOpcUaModbus') }}</li>
        <li><i />{{ $t('login.handAnyDataToAi') }}</li>
      </ul>
      <img class="hero" src="/art/login-hero.webp" alt="" aria-hidden="true" draggable="false" />
      <div class="foot">{{ $t('login.honeymeta') }}</div>
    </div>
    <div class="box hm-glass">
      <div class="lang"><LangSwitch /></div>
      <template v-if="setup.needed">
        <h2>{{ $t('login.createAdministrator') }}</h2>
        <p class="sub">{{ $t('login.firstRunNoAccounts') }}</p>
        <el-form @submit.prevent="createAdmin" label-position="top" data-test="setup-admin">
          <el-form-item :label="$t('common.name')"><el-input v-model="sa.name" size="large" /></el-form-item>
          <el-form-item :label="$t('adminRoles.username')"><el-input v-model="sa.username" size="large" autocomplete="username" /></el-form-item>
          <el-form-item :label="$t('login.passwordMin8')"><el-input v-model="sa.password" type="password" size="large" show-password autocomplete="new-password" /></el-form-item>
          <el-form-item :label="$t('login.confirmPassword')"><el-input v-model="sa.password2" type="password" size="large" show-password autocomplete="new-password" @keyup.enter="createAdmin" /></el-form-item>
          <el-button type="primary" size="large" class="go" :loading="busy" @click="createAdmin">{{ $t('login.createAdministrator') }}</el-button>
        </el-form>
      </template>
      <template v-else>
      <h2>{{ $t('adminAudit.signIn') }}</h2>
      <p class="sub">{{ $t('login.foundryMachiningIntegratedPlants') }}</p>
      <el-form @submit.prevent="go" label-position="top">
        <el-form-item :label="$t('adminRoles.username')"><el-input v-model="u" size="large" autofocus autocomplete="username" /></el-form-item>
        <el-form-item :label="$t('login.password')"><el-input v-model="p" type="password" size="large" show-password autocomplete="current-password" @keyup.enter="go" /></el-form-item>
        <el-button type="primary" size="large" class="go" :loading="busy" @click="go">{{ $t('adminAudit.signIn') }}</el-button>
      </el-form>
      <div v-if="setup.demo !== false" class="demo">{{ $t('login.demoAccountsAdminAdministratorManage') }}</div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { $t } from '../i18n';
import { useRoute, useRouter } from 'vue-router';
import { call, loadSession, post, session } from '../api';
import LangSwitch from '../components/LangSwitch.vue';

const u = ref('admin'), p = ref(''), busy = ref(false);
const router = useRouter(), route = useRoute();
// First run (production mode, no accounts yet): GET /api/setup/admin -> { needed, demo, lang }; create the first administrator, then the set-up wizard
const setup = ref<{ needed?: boolean; demo?: boolean }>({});
const sa = ref({ name: '', username: 'admin', password: '', password2: '' });
onMounted(() => { call('GET', '/api/setup/admin', undefined, true).then((r: any) => { setup.value = r || {}; }).catch(() => {}); });
async function createAdmin() {
  const f = sa.value;
  if (!f.username || f.password.length < 8) { ElMessage.error($t('login.passwordMin8')); return; }
  if (f.password !== f.password2) { ElMessage.error($t('login.passwordsDoNotMatch')); return; }
  busy.value = true;
  try {
    const r = await post('/api/setup/admin', { username: f.username, password: f.password, name: f.name || f.username });
    session.token = r.token; localStorage.setItem('mes_token', r.token);
    await loadSession();
    router.replace('/impl/setup');
  } finally { busy.value = false; }
}
async function go() {
  busy.value = true;
  try {
    const r = await post('/api/login', { username: u.value, password: p.value });
    session.token = r.token; localStorage.setItem('mes_token', r.token);
    await loadSession();
    router.replace((route.query.next as string) || (innerWidth < 700 ? '/m' : '/'));
  } finally { busy.value = false; }
}
</script>

<style scoped>
.login { min-height: 100vh; display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(360px, 440px); align-items: center; gap: 48px; padding: 48px clamp(20px, 6vw, 96px); }
.side { max-width: 620px; }
.brand { display: flex; align-items: center; gap: 12px; } .brand img { width: 44px; height: 44px; }
.brand b { display: block; font-size: 20px; letter-spacing: .06em; } .brand small { color: var(--hm-text-3); letter-spacing: .2em; font-size: 11.5px; }
h1 { font-size: clamp(26px, 3vw, 38px); line-height: 1.35; margin: 40px 0 22px; font-weight: 700; color: var(--hm-text); letter-spacing: .01em; }
.ai { background: var(--hm-ai-grad); -webkit-background-clip: text; background-clip: text; color: transparent; }
.pts { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 12px; color: var(--hm-text-2); font-size: 14px; }
.pts li { display: flex; align-items: center; gap: 10px; } .pts i { width: 8px; height: 8px; border-radius: 2px; background: var(--hm-ai-grad); transform: rotate(45deg); flex: none; }
.foot { margin-top: 20px; font-size: 12px; color: var(--hm-text-3); }
.hero { display: block; width: min(100%, 640px); height: auto; margin: 28px 0 0 -12px; user-select: none; animation: hm-fade-up .6s var(--hm-ease) both; }
@media (max-height: 820px) { .hero { max-height: 34vh; width: auto; } }
.box { border-radius: 16px; padding: 30px 30px 22px; box-shadow: var(--hm-e3); background: var(--hm-glass-strong); }
h2 { margin: 0; font-size: 20px; } .sub { margin: 4px 0 18px; color: var(--hm-text-3); font-size: 12.5px; }
.go { width: 100%; margin-top: 4px; }
.lang { display: flex; justify-content: flex-end; margin: -12px -12px 6px 0; }
.demo { margin-top: 16px; font-size: 12px; color: var(--hm-text-3); background: var(--hm-surface-2); border: 1px solid var(--hm-border); border-radius: 8px; padding: 8px 10px; line-height: 1.7; }
@media (max-width: 900px) { .login { grid-template-columns: 1fr; padding: 24px 16px; gap: 20px; } h1 { margin: 20px 0 14px; font-size: 22px; } .pts, .foot, .hero { display: none; } }
</style>
