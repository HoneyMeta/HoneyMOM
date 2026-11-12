<template>
  <div class="hm-shell" :class="{ 'nav-c': navC, 'dock-open': aiState.dockOpen, mobile, overlay: dockOverlay }" :style="vars">
    <a class="skip" href="#hm-main">{{ $t('layout.skipToMainContent') }}</a>
    <TopBar :nav-collapsed="navC && !mobile" @palette="shell.paletteOpen = true" @burger="shell.navDrawer = !shell.navDrawer" />
    <div class="nav-cell" :class="{ drawer: mobile, show: shell.navDrawer }">
      <SideNav :collapsed="navC && !mobile" @toggle="toggleNav" @navigate="shell.navDrawer = false" />
    </div>
    <div v-if="mobile && shell.navDrawer" class="nav-mask" @click="shell.navDrawer = false" />
    <main id="hm-main" class="hm-main hm-grid-bg" tabindex="-1">
      <div class="crumbs" :aria-label="$t('layout.youAreHere')" data-test="breadcrumbs">
        <template v-for="(c, i) in crumbs" :key="i">
          <span v-if="i" class="sl" aria-hidden="true">/</span>
          <a v-if="c.to && i < crumbs.length - 1" :href="c.to" @click.prevent="router.push(c.to)">{{ c.label }}</a>
          <span v-else :class="{ last: i === crumbs.length - 1 }">{{ c.label }}</span>
        </template>
        <span class="grow" />
        <span v-if="aiOn('ask')" class="hint"><kbd>Ctrl</kbd>{{ $t('layout.clickToHandDataTo') }}</span>
        <HelpButton v-if="!tour.heads" tour-id="shell-help" :label="$t('common.guidedTour')" />
      </div>
      <FeatureOff v-if="blockedFeature" :feature="blockedFeature" />
      <!-- Pages needing an AI feature: don't render until the switches are fetched (otherwise the page calls APIs and gets 403) -->
      <router-view v-else-if="!route.meta.feature || aiState.switches" :key="route.fullPath" />
    </main>
    <AiDock />
    <StatusStrip />
    <AiLayer />
    <CommandPalette v-model:open="shell.paletteOpen" />
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// App shell: top bar (brand, global search Ctrl+K, data clock, data collection, alarms, approvals, notifications, AI, user) / left menu / main area (breadcrumb + page) / right AI panel / bottom status bar.
// CSS grid layout: the collapsed AI panel takes only a thin strip (30px), expanded it takes aiState.dockWidth and the main area reflows, never covering content;
// below 1100 wide the expanded AI panel becomes an overlay; ≤ 700 (phone) the menu becomes a drawer.
import { computed, onMounted, onUnmounted, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { aiOn, aiState, loadAiSwitches } from '../ai';
import AiLayer from '../ai/AiLayer.vue';
import { ent } from '../api';
import AiDock from './AiDock.vue';
import CommandPalette from './CommandPalette.vue';
import FeatureOff from './FeatureOff.vue';
import { findNav, NAV } from './nav';
import { setNavCollapsed, shell, startShellPolling, stopShellPolling } from './shell';
import SideNav from './SideNav.vue';
import StatusStrip from './StatusStrip.vue';
import TopBar from './TopBar.vue';
import HelpButton from '../tour/HelpButton.vue';
import { tour } from '../tour/engine';

const route = useRoute(), router = useRouter();
const mobile = computed(() => shell.vw <= 700);
const tablet = computed(() => shell.vw < 1280);
const dockOverlay = computed(() => shell.vw < 1100);
const navC = computed(() => (route.path.startsWith('/agents/') ? true : tablet.value ? !shell.navTabletOpen : shell.navCollapsed));
const vars = computed(() => ({
  '--nav-col': mobile.value ? '0px' : navC.value ? 'var(--hm-nav-w-collapsed)' : 'var(--hm-nav-w)',
  '--dock-col': mobile.value ? '0px' : aiState.dockOpen && !dockOverlay.value ? aiState.dockWidth + 'px' : 'var(--hm-dock-tab-w)',
}));
function toggleNav() { if (tablet.value) shell.navTabletOpen = !shell.navTabletOpen; else setNavCollapsed(!shell.navCollapsed); }

// Required AI feature is off: show the "Disabled" page
const blockedFeature = computed(() => {
  const f = route.meta.feature as string | undefined;
  if (!f || !aiState.switches) return '';
  return aiOn(f) ? '' : f;
});

const crumbs = computed(() => {
  const p = route.path;
  if (p === '/') return [{ label: $t('common.home') }];
  const n = findNav(p);
  if (n) {
    const g = NAV.find((x) => x.key === n.groupKey)!;
    const out: { label: string; to?: string }[] = [{ label: g.label, to: g.items[0].path }, { label: n.label, to: n.path }];
    if (route.query.id) out.push({ label: $t('common.details2') });
    return out;
  }
  const m = p.match(/^\/e\/(\w+)/);
  if (m) return [{ label: $t('layout.data') }, { label: ent(m[1])?.label || m[1] }];
  if (p.startsWith('/agents/')) return [{ label: $t('layout.intelligence'), to: '/agents' }, { label: $t('common.agents'), to: '/agents' }, { label: $t('common.edit') }];
  return [{ label: p }];
});

function onKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'k') { e.preventDefault(); shell.paletteOpen = !shell.paletteOpen; }
}
watch(() => route.fullPath, () => { shell.navDrawer = false; document.getElementById('hm-main')?.scrollTo?.({ top: 0 }); });
onMounted(() => { loadAiSwitches(); startShellPolling(); document.addEventListener('keydown', onKey); });
onUnmounted(() => { stopShellPolling(); document.removeEventListener('keydown', onKey); });
</script>

<style scoped>
.hm-shell { display: grid; height: 100vh; height: 100dvh; overflow: hidden;
  grid-template-areas: "top top top" "nav main dock" "status status status";
  grid-template-rows: var(--hm-top-h) minmax(0, 1fr) var(--hm-status-h);
  grid-template-columns: var(--nav-col) minmax(0, 1fr) var(--dock-col);
  transition: grid-template-columns var(--hm-dur-3) var(--hm-ease); background: var(--hm-canvas); }
:global(body.hm-resizing) .hm-shell { transition: none; }
.skip { position: absolute; left: -999px; top: 4px; z-index: 5000; background: #fff; padding: 6px 10px; border-radius: 6px; }
.skip:focus { left: 8px; }
.nav-cell { grid-area: nav; min-height: 0; overflow: hidden; position: relative; z-index: calc(var(--hm-z-shell) - 1); }
.hm-main { grid-area: main; min-width: 0; min-height: 0; overflow: auto; position: relative; outline: none; }
.crumbs { display: flex; align-items: center; gap: 6px; height: calc(34px * var(--hm-scale)); padding: 0 20px; font-size: var(--hm-fs-xs); color: var(--hm-text-3); position: sticky; top: 0; z-index: 5;
  background: linear-gradient(var(--hm-canvas) 70%, rgba(243, 246, 250, 0)); }
.crumbs a { color: var(--hm-text-3); } .crumbs a:hover { color: var(--hm-primary); text-decoration: none; }
.crumbs .last { color: var(--hm-text-2); font-weight: 600; } .crumbs .sl { color: var(--hm-border-strong); }
.crumbs .grow { flex: 1; }
.hint { font-size: 11px; color: var(--hm-text-3); white-space: nowrap; }
kbd { font-family: var(--hm-font-mono); font-size: 10px; border: 1px solid var(--hm-border-strong); border-bottom-width: 2px; border-radius: 3px; padding: 0 3px; background: #fff; color: var(--hm-text-2); }
.hm-main :deep(.page) { padding-top: 2px; }

/* AI panel overlay (tablet) */
.overlay :deep(.dock.open) { position: fixed; top: var(--hm-top-h); bottom: var(--hm-status-h); right: 0; width: min(420px, 100vw); height: auto; box-shadow: var(--hm-e3); }
.overlay :deep(.dock.open .panel) { background: var(--hm-surface); backdrop-filter: none; }

/* Phone: menu drawer, AI panel full-screen overlay, collapsed strip hidden (use the AI button in the top bar) */
.nav-cell.drawer { position: fixed; top: var(--hm-top-h); bottom: 0; left: 0; width: 264px; transform: translateX(-100%); transition: transform var(--hm-dur-3) var(--hm-ease); z-index: 2500; box-shadow: var(--hm-e3); }
.nav-cell.drawer.show { transform: none; }
.nav-mask { position: fixed; inset: var(--hm-top-h) 0 0 0; background: rgba(15, 27, 45, .25); z-index: 2400; }
.mobile { grid-template-rows: var(--hm-top-h) minmax(0, 1fr) 0; }
.mobile :deep(.dock .edge) { display: none; }
.mobile :deep(.dock.open) { position: fixed; inset: var(--hm-top-h) 0 0 0; width: 100vw; height: auto; }
.mobile .crumbs { padding: 0 10px; } .mobile .hint { display: none; }
@media (max-width: 1279px) { .hint { display: none; } }
</style>
