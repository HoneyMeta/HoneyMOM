<template>
  <nav class="hm-nav" :class="{ collapsed }" :aria-label="$t('sideNav.mainMenu')" data-test="side-nav">
    <div v-if="!collapsed" class="search">
      <el-icon class="si"><Search /></el-icon>
      <input ref="inp" v-model="q" class="sq" :placeholder="$t('sideNav.filterMenu')" :aria-label="$t('sideNav.filterMenu')" data-test="nav-search" @keydown.enter.prevent="goFirst" @keydown.esc="q = ''" @keydown.down.prevent="focusFirst" />
      <kbd v-if="!q" class="kh">/</kbd>
      <button v-else class="clr" :aria-label="$t('aiDock.clear')" @click="q = ''"><el-icon><Close /></el-icon></button>
    </div>

    <el-scrollbar class="scroll">
      <!-- Search results -->
      <div v-if="!collapsed && q" class="results" data-test="nav-results">
        <a v-for="it in hits" :key="it.path" class="item" :href="it.path" :class="{ active: isActive(it.path), off: off(it) }" @click.prevent="go(it)">
          <span class="lbl" v-html="hl(it.label)" /><span class="grp">{{ it.group }}</span>
        </a>
        <div v-if="!hits.length" class="none">{{ $t('sideNav.noMatchingMenuItemsPress') }} <kbd>Ctrl</kbd>+<kbd>K</kbd> {{ $t('sideNav.toSearchRecordsOrAsk') }}</div>
      </div>

      <!-- Expanded -->
      <template v-else-if="!collapsed">
        <section v-for="g in nav" :key="g.key" class="grp-sec" :class="{ open: openSet.has(g.key), cur: g.key === curGroup }">
          <button class="gh" :aria-expanded="openSet.has(g.key)" @click="toggle(g.key)">
            <el-icon class="gi"><component :is="g.icon" /></el-icon><span class="gl">{{ g.label }}</span>
            <el-icon class="chev"><ArrowRight /></el-icon>
          </button>
          <div v-show="openSet.has(g.key)" class="items">
            <a v-for="it in g.items" :key="it.path" class="item" :href="it.path" :class="{ active: isActive(it.path), off: off(it) }" :title="off(it) ? offTitle(it) : ''" @click.prevent="go(it)">
              <span class="lbl">{{ it.label }}</span><el-icon v-if="off(it)" class="lock"><Lock /></el-icon>
            </a>
          </div>
        </section>
      </template>

      <!-- Collapsed: icon rail + hover flyout -->
      <template v-else>
        <el-popover v-for="g in nav" :key="g.key" placement="right-start" :width="210" trigger="hover" :show-after="80" :hide-after="120" popper-class="hm-nav-pop" :offset="6">
          <template #reference>
            <button class="rail" :class="{ cur: g.key === curGroup }" :aria-label="g.label" @click="go(g.items[0])"><el-icon><component :is="g.icon" /></el-icon></button>
          </template>
          <div class="pop-h">{{ g.label }}</div>
          <a v-for="it in g.items" :key="it.path" class="item" :href="it.path" :class="{ active: isActive(it.path), off: off(it) }" @click.prevent="go(it)">
            <span class="lbl">{{ it.label }}</span><el-icon v-if="off(it)" class="lock"><Lock /></el-icon>
          </a>
        </el-popover>
      </template>
    </el-scrollbar>

    <button class="fold" :aria-label="collapsed ? $t('sideNav.expandMenu') : $t('sideNav.collapseMenu')" :title="collapsed ? $t('sideNav.expandMenu') : $t('sideNav.collapseMenu')" data-test="nav-fold" @click="$emit('toggle')">
      <el-icon><Expand v-if="collapsed" /><Fold v-else /></el-icon><span v-if="!collapsed">{{ $t('sideNav.collapseMenu') }}</span>
    </button>
  </nav>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { aiOffText, aiOn } from '../ai';
import { session } from '../api';
import { ALL_NAV, findNav, navAllowed, visibleNav, type NavItem } from './nav';

const props = defineProps<{ collapsed: boolean }>();
const nav = computed(() => (session.user, visibleNav()));       // Menu filtered by permissions
const emit = defineEmits<{ toggle: []; navigate: [] }>();
const route = useRoute(), router = useRouter();
const q = ref(''), inp = ref<HTMLInputElement>();
const K = 'hm_nav_open';
const openSet = reactive(new Set<string>((() => { try { return JSON.parse(localStorage.getItem(K) || '["home","prod","quality","equip"]'); } catch { return ['home']; } })()));
const curGroup = computed(() => findNav(route.path)?.groupKey || (route.path.startsWith('/agents') ? 'ai' : ''));
watch(curGroup, (g) => { if (g && !openSet.has(g)) { openSet.add(g); save(); } }, { immediate: true });
function save() { try { localStorage.setItem(K, JSON.stringify([...openSet])); } catch { /* */ } }
function toggle(k: string) { openSet.has(k) ? openSet.delete(k) : openSet.add(k); save(); }

const isActive = (p: string) => route.path === p || (p === '/agents' && route.path.startsWith('/agents/'));
const off = (it: NavItem) => !!it.feature && !aiOn(it.feature);
const offTitle = (it: NavItem) => aiOffText(it.feature);
function go(it: NavItem) { q.value = ''; router.push(it.path); emit('navigate'); }

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, '');
const hits = computed(() => {
  const k = norm(q.value);
  if (!k) return [];
  return ALL_NAV.filter(navAllowed).map((it) => {
    const l = norm(it.label), kw = norm(it.kw || '') + norm(it.path) + norm(it.group);
    const score = l.startsWith(k) ? 3 : l.includes(k) ? 2 : kw.includes(k) ? 1 : 0;
    return { it, score };
  }).filter((x) => x.score).sort((a, b) => b.score - a.score).map((x) => x.it).slice(0, 30);
});
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' } as any)[c]);
function hl(label: string) {
  const k = q.value.trim();
  const i = k ? label.toLowerCase().indexOf(k.toLowerCase()) : -1;
  return i < 0 ? esc(label) : esc(label.slice(0, i)) + '<mark>' + esc(label.slice(i, i + k.length)) + '</mark>' + esc(label.slice(i + k.length));
}
function goFirst() { if (hits.value[0]) go(hits.value[0]); }
function focusFirst() { (document.querySelector('.hm-nav .results .item') as HTMLElement | null)?.focus(); }

// "/" focuses the menu search (when not in an input)
function onKey(e: KeyboardEvent) {
  if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
  const t = e.target as HTMLElement;
  if (t && (t.closest('input, textarea, [contenteditable=true]'))) return;
  if (props.collapsed) return;
  e.preventDefault(); inp.value?.focus();
}
onMounted(() => document.addEventListener('keydown', onKey));
onUnmounted(() => document.removeEventListener('keydown', onKey));
defineExpose({ focusSearch: () => inp.value?.focus() });
</script>

<style scoped>
.hm-nav { display: flex; flex-direction: column; min-height: 0; height: 100%; background: var(--hm-glass-strong); backdrop-filter: var(--hm-glass-blur); border-right: 1px solid var(--hm-border); }
.search { position: relative; margin: 10px 10px 6px; }
.si { position: absolute; left: 9px; top: 50%; transform: translateY(-50%); color: var(--hm-text-3); font-size: 14px; }
.sq { width: 100%; height: 30px; border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); background: var(--hm-surface-2); padding: 0 26px 0 30px; font: inherit; font-size: var(--hm-fs-sm); color: var(--hm-text); outline: none; transition: border-color var(--hm-dur-1), box-shadow var(--hm-dur-1), background var(--hm-dur-1); }
.sq:focus { border-color: var(--hm-primary); background: #fff; box-shadow: 0 0 0 3px rgba(20, 99, 214, .12); }
.kh { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); }
.clr { position: absolute; right: 4px; top: 50%; transform: translateY(-50%); border: 0; background: transparent; color: var(--hm-text-3); cursor: pointer; display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; }
.clr:hover { background: var(--hm-surface-3); }
kbd { font-family: var(--hm-font-mono); font-size: 10px; border: 1px solid var(--hm-border-strong); border-bottom-width: 2px; border-radius: 3px; padding: 0 4px; background: #fff; color: var(--hm-text-3); line-height: 14px; }
.scroll { flex: 1; min-height: 0; }
.scroll :deep(.el-scrollbar__view) { padding: 2px 8px 10px; }

.grp-sec { margin-top: 2px; }
.gh { width: 100%; display: flex; align-items: center; gap: 9px; border: 0; background: transparent; cursor: pointer; padding: 7px 8px; border-radius: var(--hm-r-md);
  font: inherit; font-size: var(--hm-fs-sm); color: var(--hm-text-2); font-weight: 600; letter-spacing: .02em; }
.gh:hover { background: var(--hm-surface-3); color: var(--hm-text); }
.gi { font-size: 16px; color: var(--hm-text-3); } .cur .gi { color: var(--hm-primary); } .cur .gh { color: var(--hm-text); }
.gl { flex: 1; text-align: left; }
.chev { font-size: 11px; color: var(--hm-text-3); transition: transform var(--hm-dur-2) var(--hm-ease); } .open .chev { transform: rotate(90deg); }
.items { padding: 1px 0 4px 18px; position: relative; }
.items::before { content: ''; position: absolute; left: 16px; top: 2px; bottom: 6px; width: 1px; background: var(--hm-border); }
.item { display: flex; align-items: center; gap: 6px; padding: 5px 8px 5px 14px; border-radius: var(--hm-r-sm); color: var(--hm-text-2); font-size: var(--hm-fs-sm); text-decoration: none !important;
  position: relative; line-height: 20px; cursor: pointer; }
.item:hover { background: var(--hm-surface-3); color: var(--hm-text); }
.item:focus-visible { box-shadow: var(--hm-focus); }
.item.active { background: var(--hm-primary-soft); color: var(--hm-primary-press); font-weight: 600; }
.item.active::before { content: ''; position: absolute; left: -2px; top: 6px; bottom: 6px; width: 3px; border-radius: 2px; background: var(--hm-primary); }
.item.off { color: var(--hm-text-3); } .item .lbl { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lock { font-size: 12px; color: var(--hm-text-3); }
.results .item { padding-left: 10px; } .results .grp { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); }
.results :deep(mark) { background: rgba(217, 130, 43, .2); color: inherit; border-radius: 2px; padding: 0 1px; }
.none { font-size: var(--hm-fs-xs); color: var(--hm-text-3); padding: 10px 8px; line-height: 1.8; }

.rail { width: 40px; height: 38px; margin: 2px auto; display: grid; place-items: center; border: 0; background: transparent; border-radius: var(--hm-r-md); color: var(--hm-text-2); cursor: pointer; font-size: 18px; }
.rail:hover { background: var(--hm-surface-3); color: var(--hm-text); }
.rail.cur { background: var(--hm-primary-soft); color: var(--hm-primary); }
.collapsed .scroll :deep(.el-scrollbar__view) { padding: 8px 0; display: flex; flex-direction: column; align-items: center; }

.fold { display: flex; align-items: center; gap: 8px; margin: 6px 8px 8px; padding: 6px 10px; border: 0; background: transparent; border-radius: var(--hm-r-md); color: var(--hm-text-3); cursor: pointer; font: inherit; font-size: var(--hm-fs-xs); }
.fold:hover { background: var(--hm-surface-3); color: var(--hm-text); }
.collapsed .fold { justify-content: center; margin: 6px auto 8px; width: 40px; padding: 6px 0; }
</style>

<style>
.hm-nav-pop.el-popper { padding: 6px !important; border-radius: 10px !important; }
.hm-nav-pop .pop-h { font-size: 11px; color: var(--hm-text-3); padding: 4px 8px 6px; letter-spacing: .06em; }
.hm-nav-pop .item { display: flex; align-items: center; gap: 6px; padding: 5px 8px; border-radius: 5px; color: var(--hm-text-2); font-size: 13px; text-decoration: none; cursor: pointer; }
.hm-nav-pop .item:hover { background: var(--hm-surface-3); color: var(--hm-text); }
.hm-nav-pop .item.active { background: var(--hm-primary-soft); color: var(--hm-primary-press); font-weight: 600; }
.hm-nav-pop .item.off { color: var(--hm-text-3); } .hm-nav-pop .item .lbl { flex: 1; }
</style>
