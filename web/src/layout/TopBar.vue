<template>
  <header class="hm-top" data-test="top-bar">
    <div class="brand" :class="{ narrow: navCollapsed }">
      <button class="burger" :aria-label="$t('topBar.menu')" @click="$emit('burger')"><el-icon><Menu /></el-icon></button>
      <a class="logo" href="/" @click.prevent="router.push('/')" :aria-label="$t('topBar.honeymomHome')">
        <img src="/brand/mark.svg" alt="" width="28" height="28" />
        <span v-if="!navCollapsed" class="bt"><b>{{ $t('common.honeymom') }}</b><small :title="$t('topBar.manufacturingOperationsManagement')">{{ $t('topBar.subtitleShort') }}</small></span>
      </a>
    </div>

    <button class="cmd" data-test="cmd-bar" :aria-label="$t('topBar.searchAnythingOrAskAi')" @click="$emit('palette')">
      <el-icon class="ci"><Search /></el-icon>
      <span class="ph">{{ $t('topBar.searchAnythingOrAskAi2') }}</span>
      <span class="ai-mark" :class="{ off: !aiOn('ask') }" aria-hidden="true">AI</span>
      <kbd>Ctrl</kbd><kbd>K</kbd>
    </button>

    <div class="right">
      <el-tooltip :content="shell.dataClock ? $t('topBar.dataClockBasedOnThe') : $t('topBar.dataClockNoShopFloor')" placement="bottom">
        <span class="clock" data-test="data-clock" :class="{ live: shell.dataClock, stale: !shell.ok }">
          <i class="pd" :class="{ 'hm-pulse': shell.dataClock && shell.ok }" />
          <span class="t hm-num">{{ (shell.now || '').slice(11, 19) || '--:--:--' }}</span>
          <span class="d hm-num">{{ (shell.now || '').slice(5, 10) }}</span>
        </span>
      </el-tooltip>
      <el-tooltip :content="connTip" placement="bottom">
        <span class="conn" data-test="conn-status" :class="connCls" @click="router.push('/system')">
          <i class="pd" /><span class="cl">{{ $t('statusStrip.collection') }}</span><span class="hm-num">{{ shell.collect.online }}/{{ shell.collect.total }}</span>
        </span>
      </el-tooltip>
      <el-tooltip :content="$t('topBar.activeEquipmentAlarms')" placement="bottom">
        <button class="ib" :class="{ hot: shell.alarms > 0 }" data-test="alarm-count" :aria-label="$t('topBar.activeAlarms')" @click="router.push('/e/alarm')">
          <el-icon><WarningFilled /></el-icon><span class="hm-num cnt">{{ shell.alarms }}</span>
        </button>
      </el-tooltip>
      <el-tooltip v-if="shell.approvals !== null" :content="$t('topBar.approvalCenterItemsWaitingFor')" placement="bottom">
        <el-badge :value="shell.approvals || ''" :hidden="!shell.approvals" class="bdg" data-test="approval-badge">
          <button class="ib" :aria-label="$t('common.approvalCenter')" @click="router.push('/approvals')"><el-icon><Stamp /></el-icon><span class="apl">{{ $t('topBar.approvals') }}</span></button>
        </el-badge>
      </el-tooltip>
      <el-popover placement="bottom-end" :width="340" trigger="click" @show="loadNotices">
        <template #reference>
          <el-badge :value="shell.unread || ''" :hidden="!shell.unread" class="bdg"><button class="ib" :aria-label="$t('common.notifications')" data-test="notices"><el-icon><Bell /></el-icon></button></el-badge>
        </template>
        <div class="nt">
          <div class="nth"><b>{{ $t('common.notifications') }}</b><span class="grow" /><el-button link size="small" @click="readAll" v-if="shell.unread">{{ $t('topBar.markAllRead') }}</el-button></div>
          <div v-for="n in notices" :key="n.id" class="nti click" :class="{ unread: !n.read }" @click="openNotice(n)">
            <div><b>{{ n.title }}</b></div><div class="muted small">{{ (n.time || '').slice(5, 16) }} · {{ n.source || '' }}</div>
          </div>
          <div v-if="!notices.length" class="muted small" style="padding:8px">{{ $t('topBar.noNotifications') }}</div>
        </div>
      </el-popover>
      <LangSwitch compact />
      <button class="ai-btn" :class="{ on: aiState.dockOpen, off: !aiOn() }" :aria-pressed="aiState.dockOpen" data-test="ai-toggle"
        :title="aiState.dockOpen ? $t('topBar.collapseAiPanel') : $t('aiDock.openAiPanel')" @click="openAiDock(!aiState.dockOpen)">
        <span class="sp" aria-hidden="true" /><span>AI</span><em v-if="aiState.items.length" class="hm-num">{{ aiState.items.length }}</em>
      </button>
      <el-dropdown trigger="click" @command="onCmd">
        <button class="user" data-test="user-menu"><span class="av">{{ (session.user?.name || '?').slice(0, 1) }}</span><span class="un">{{ session.user?.name }}</span><el-icon class="dd"><ArrowDown /></el-icon></button>
        <template #dropdown>
          <el-dropdown-menu>
            <div class="uh"><b>{{ session.user?.name }}</b><span>{{ roleText }}</span><span class="co">{{ session.user?.company }}</span></div>
            <el-dropdown-item command="pw" divided><el-icon><Key /></el-icon>{{ $t('topBar.changePassword') }}</el-dropdown-item>
            <el-dropdown-item command="m"><el-icon><Iphone /></el-icon>{{ $t('topBar.mobile') }}</el-dropdown-item>
            <el-dropdown-item command="ai"><el-icon><MagicStick /></el-icon>{{ $t('commandPalette.aiFeatureSettings') }}</el-dropdown-item>
            <el-dropdown-item command="out" divided><el-icon><SwitchButton /></el-icon>{{ $t('commandPalette.signOut') }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { aiOn, aiState, openAiDock } from '../ai';
import { get, post, session } from '../api';
import { refreshShell, shell } from './shell';
import LangSwitch from '../components/LangSwitch.vue';

defineProps<{ navCollapsed: boolean }>();
defineEmits<{ palette: []; burger: [] }>();
const router = useRouter();
const notices = ref<any[]>([]);
const roleText = computed(() => (session.user?.role_list || []).map((r: string) => session.meta?.roles[r] || r).join($t('common.k42')));
const connCls = computed(() => (!shell.ok ? 'bad' : !shell.collect.total ? 'off' : shell.collect.online === shell.collect.total ? 'good' : shell.collect.online ? 'warn' : 'bad'));
const connTip = computed(() => !shell.ok ? $t('topBar.canTReachTheHoneymom') : shell.sources.filter((s) => s.enabled).map((s) => $t('common.n012', [s.name, s.status || $t('common.notConnected')])).join($t('common.k764')) || $t('topBar.noDataSourcesEnabled'));

async function loadNotices() { const r = await get('/api/notices', true).catch(() => ({ items: [], unread: 0 })); notices.value = r.items; shell.unread = r.unread; }
async function readAll() { await post('/api/notices/read', {}); loadNotices(); }
async function openNotice(x: any) { if (!x.read) await post('/api/notices/read', { ids: [x.id] }); if (x.link) router.push(x.link); loadNotices(); refreshShell(); }
async function onCmd(c: string) {
  if (c === 'out') { session.token = ''; session.user = null; localStorage.removeItem('mes_token'); router.push('/login'); }
  if (c === 'm') router.push('/m');
  if (c === 'ai') router.push('/system/ai');
  if (c === 'pw') {
    const { value: old } = await ElMessageBox.prompt($t('topBar.currentPassword'), $t('topBar.changePassword'), { inputType: 'password' });
    const { value: nw } = await ElMessageBox.prompt($t('topBar.newPasswordAtLeast6'), $t('topBar.changePassword'), { inputType: 'password' });
    await post('/api/me/password', { old, new: nw }); ElMessage.success($t('topBar.changed'));
  }
}
</script>

<style scoped>
.hm-top { grid-area: top; display: flex; align-items: center; gap: 12px; height: var(--hm-top-h); padding: 0 12px 0 0; background: var(--hm-glass-strong); backdrop-filter: var(--hm-glass-blur);
  border-bottom: 1px solid var(--hm-border); position: relative; z-index: var(--hm-z-shell); }
.hm-top::after { content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 1px; background: linear-gradient(90deg, transparent, rgba(11, 165, 196, .35) 30%, rgba(20, 99, 214, .35) 60%, transparent); pointer-events: none; }
.brand { width: var(--hm-nav-w); flex: none; display: flex; align-items: center; padding-left: 14px; height: 100%; transition: width var(--hm-dur-3) var(--hm-ease); }
.brand.narrow { width: var(--hm-nav-w-collapsed); padding-left: 16px; }
.burger { display: none; border: 0; background: transparent; font-size: 20px; color: var(--hm-text-2); cursor: pointer; margin-right: 6px; }
.logo { display: flex; align-items: center; gap: 10px; text-decoration: none !important; color: var(--hm-text); }
.logo img { width: 28px; height: 28px; flex: none; }
.bt { display: flex; flex-direction: column; line-height: 1.15; white-space: nowrap; }
.bt b { font-size: 15px; letter-spacing: .04em; font-weight: 700; } .bt small { font-size: 10.5px; color: var(--hm-text-3); letter-spacing: .12em; }

.cmd { flex: 1 1 auto; max-width: 560px; height: 34px; display: flex; align-items: center; gap: 8px; padding: 0 8px 0 12px; border: 1px solid var(--hm-border); border-radius: var(--hm-r-md);
  background: var(--hm-surface-2); cursor: text; font: inherit; color: var(--hm-text-3); transition: border-color var(--hm-dur-1), box-shadow var(--hm-dur-1), background var(--hm-dur-1); min-width: 0; }
.cmd:hover { border-color: var(--hm-border-strong); background: #fff; }
.cmd:focus-visible { border-color: var(--hm-primary); box-shadow: 0 0 0 3px rgba(20, 99, 214, .14); }
.ci { font-size: 15px; } .ph { flex: 1; text-align: left; font-size: var(--hm-fs-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ai-mark { font-size: 10px; font-weight: 800; letter-spacing: .08em; color: #fff; background: var(--hm-ai-grad); border-radius: 4px; padding: 0 5px; line-height: 16px; }
.ai-mark.off { background: var(--hm-surface-3); color: var(--hm-text-3); }
kbd { font-family: var(--hm-font-mono); font-size: 10.5px; border: 1px solid var(--hm-border-strong); border-bottom-width: 2px; border-radius: 4px; padding: 0 5px; background: #fff; color: var(--hm-text-2); line-height: 16px; }

.right { margin-left: auto; display: flex; align-items: center; gap: 4px; flex: none; }
.clock { display: inline-flex; align-items: baseline; gap: 6px; padding: 4px 10px; border-radius: var(--hm-r-md); background: var(--hm-surface-2); border: 1px solid var(--hm-border); margin-right: 4px; }
.clock .t { font-size: 15px; font-weight: 600; color: var(--hm-text); letter-spacing: .02em; } .clock .d { font-size: 11px; color: var(--hm-text-3); }
.pd { width: 7px; height: 7px; border-radius: 50%; background: var(--hm-offline); display: inline-block; align-self: center; color: rgba(21, 163, 74, .4); }
.clock.live .pd { background: var(--hm-run); } .clock.stale .pd { background: var(--hm-fault); }
.conn { display: inline-flex; align-items: center; gap: 5px; padding: 4px 9px; border-radius: var(--hm-r-pill); font-size: var(--hm-fs-xs); cursor: pointer; border: 1px solid var(--hm-border); background: #fff; color: var(--hm-text-2); }
.conn.good .pd { background: var(--hm-run); } .conn.warn .pd { background: var(--hm-idle); } .conn.bad .pd { background: var(--hm-fault); } .conn.bad { color: var(--hm-fault-ink); border-color: #f4c7c7; background: var(--hm-fault-soft); }
.cl { color: var(--hm-text-3); }
.ib { display: inline-flex; align-items: center; gap: 4px; height: 32px; min-width: 32px; justify-content: center; padding: 0 8px; border: 0; border-radius: var(--hm-r-md); background: transparent; color: var(--hm-text-2); cursor: pointer; font: inherit; font-size: var(--hm-fs-sm); }
.ib:hover { background: var(--hm-surface-3); color: var(--hm-text); }
.ib .el-icon { font-size: 17px; }
.ib.hot { color: var(--hm-fault-ink); } .ib.hot .cnt { font-weight: 700; }
.cnt { font-size: 13px; }
.bdg :deep(.el-badge__content) { top: 6px; right: 8px; }
.apl { font-size: var(--hm-fs-sm); }
.ai-btn { display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 12px 0 10px; margin-left: 4px; border-radius: var(--hm-r-pill); cursor: pointer; font: inherit; font-weight: 700; font-size: 12.5px; letter-spacing: .06em;
  color: var(--hm-primary-press); border: 1px solid transparent; background: linear-gradient(#fff, #fff) padding-box, var(--hm-ai-grad) border-box; }
.ai-btn .sp { width: 14px; height: 14px; background: var(--hm-ai-grad); clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%); }
.ai-btn.on { background: var(--hm-ai-grad) border-box; color: #fff; } .ai-btn.on .sp { background: #fff; }
.ai-btn.off { background: linear-gradient(#fff, #fff) padding-box, linear-gradient(var(--hm-border-strong), var(--hm-border-strong)) border-box; color: var(--hm-text-3); } .ai-btn.off .sp { background: var(--hm-text-3); }
.ai-btn em { font-style: normal; font-size: 10.5px; background: var(--hm-amber); color: #fff; border-radius: var(--hm-r-pill); padding: 0 5px; line-height: 15px; letter-spacing: 0; }
.user { display: inline-flex; align-items: center; gap: 7px; height: 34px; padding: 0 6px 0 4px; margin-left: 4px; border: 0; border-radius: var(--hm-r-pill); background: transparent; cursor: pointer; font: inherit; color: var(--hm-text); }
.user:hover { background: var(--hm-surface-3); }
.av { width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center; font-size: 13px; font-weight: 700; color: #fff; background: linear-gradient(135deg, #e09a45, var(--hm-amber)); }
.un { font-size: var(--hm-fs-sm); } .dd { font-size: 11px; color: var(--hm-text-3); }
.uh { display: flex; flex-direction: column; padding: 8px 16px 6px; font-size: 12px; color: var(--hm-text-3); gap: 1px; } .uh b { font-size: 13.5px; color: var(--hm-text); } .uh .co { font-size: 11px; }
.nt { max-height: 420px; overflow: auto; } .nth { display: flex; align-items: center; padding: 0 2px 6px; border-bottom: 1px solid var(--hm-border); }
.nti { padding: 6px 4px; border-bottom: 1px solid #f2f4f7; font-size: 13px; } .nti.unread b { color: var(--hm-primary-press); } .nti:hover { background: var(--hm-surface-2); }

@media (max-width: 1279px) { .apl, .un, .cl { display: none; } .clock .d { display: none; } }
@media (max-width: 1023px) { .brand { width: auto; padding-left: 10px; } .bt small { display: none; } .cmd .ph { display: none; } .cmd { flex: 0 0 auto; width: 74px; } kbd { display: none; } }
@media (max-width: 700px) { .burger { display: block; } .bt, .conn, .clock { display: none; } .cmd { width: 40px; padding: 0 10px; } .cmd .ai-mark { display: none; } }
</style>
