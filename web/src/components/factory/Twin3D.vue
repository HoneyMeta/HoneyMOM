<template>
  <div ref="host" class="t3" data-test="twin3d">
    <div v-if="err" class="msg">{{ err }}</div>
    <div v-else-if="loading" class="msg">{{ $t('twin3D.building3dScene') }}</div>
    <div class="legend"><span v-for="(c, k) in LEG" :key="k"><i :style="{ background: c[1] }" />{{ c[0] }}</span></div>
  </div>
</template>

<script setup lang="ts">
import { $t, lang } from '../../i18n';
// 3D twin: uses the equipment's GLB if present (delivery package models/xxx.glb); otherwise auto-generates a parametric model from the 2D layout per equipment template (box / cylinder combos, ported from the virtual factory package's simplified models).
// Coordinates: glTF X = plant X, Y = height, Z = −plant Y; orientation a (CCW) = rotate a around +Y. The color patch under equipment = live status.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { session } from '../../api';

const props = withDefaults(defineProps<{ layout: any; live?: any[]; useModels?: boolean }>(), { live: () => [], useModels: true });
const emit = defineEmits<{ pick: [code: string] }>();
const host = ref<HTMLDivElement | null>(null), err = ref(''), loading = ref(true);
const LEG: Record<string, [string, string]> = { run: [$t('common.running'), '#1f9d55'], idle: [$t('common.idle'), '#c98a00'], down: [$t('common.down'), '#d33'], off: [$t('common.offline'), '#9aa5b4'] };
const TONE: Record<string, string> = { running: 'run', idle: 'idle', setup: 'idle', down: 'down', offline: 'off' };
let renderer: THREE.WebGLRenderer | null = null, scene: THREE.Scene, camera: THREE.PerspectiveCamera, controls: any, raf = 0, ro: ResizeObserver | null = null;
const pads = new Map<string, THREE.Mesh>();
const C = { grey: 0x9aa0a6, dark: 0x383d45, light: 0xdbe0e6, blue: 0x407ac0, orange: 0xd97326, yellow: 0xf2bf26, steel: 0x808c99, wood: 0xa6804d };

function box(g: THREE.Group, sx: number, sy: number, sz: number, x: number, y: number, z: number, col: number) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(Math.max(sx, 0.02), Math.max(sy, 0.02), Math.max(sz, 0.02)), new THREE.MeshStandardMaterial({ color: col, roughness: 0.7, metalness: 0.2 }));
  m.position.set(x, y, z); g.add(m);
}
function cyl(g: THREE.Group, r: number, h: number, x: number, y: number, z: number, col: number) {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 24), new THREE.MeshStandardMaterial({ color: col, roughness: 0.6, metalness: 0.2 }));
  m.position.set(x, y + h / 2, z); g.add(m);
}
/** Parametric equipment model (origin at bottom center, X along width, Z along depth). */
function parametric(kind: string, L: number, W: number, H: number) {
  const g = new THREE.Group(); const m = Math.min(L, W);
  switch (kind) {
    case 'cnc': box(g, L, 0.8, W, 0, 0.4, 0, C.dark); box(g, L * 0.96, 1.6, W * 0.9, 0, 1.6, 0, C.light); box(g, L * 0.4, 0.6, 0.03, -L * 0.12, 1.6, W * 0.45 + 0.02, C.blue); break;
    case 'molding': case 'die_casting': case 'injection':
      box(g, L, 1.1, W, 0, 0.55, 0, C.steel); box(g, Math.min(L * 0.12, 4), 2.6, W * 0.9, -L * 0.35, 1.3 + 0.5, 0, C.blue); box(g, L * 0.8, 0.12, W * 0.6, L * 0.05, 1.16, 0, C.dark); break;
    case 'conveyor': {
      box(g, L, 0.15, W * 0.8, 0, 0.8, 0, C.dark); box(g, L, 0.08, 0.06, 0, 0.92, W * 0.42, C.yellow); box(g, L, 0.08, 0.06, 0, 0.92, -W * 0.42, C.yellow);
      const n = Math.max(2, Math.floor(L / 3) + 1);
      for (let i = 0; i < n; i++) { const x = -L / 2 + 0.1 + (L - 0.2) * i / (n - 1); box(g, 0.08, 0.75, 0.08, x, 0.375, W * 0.35, C.steel); box(g, 0.08, 0.75, 0.08, x, 0.375, -W * 0.35, C.steel); }
      break;
    }
    case 'furnace': box(g, L, 0.6, W, 0, 0.3, 0, C.dark); cyl(g, m * 0.35, 1.6, 0, 0.6, 0, C.orange); break;
    case 'treatment': box(g, L, 0.4, W, 0, 0.2, 0, C.steel); cyl(g, m * 0.3, 1.4, -L * 0.2, 0.4, 0, C.orange); box(g, L * 0.3, 2.4, W * 0.3, L * 0.3, 1.6, 0, C.grey); break;
    case 'sand': box(g, L, 1.0, W, 0, 0.5, 0, C.steel); cyl(g, m * 0.3, 3, -L * 0.25, 1, 0, C.grey); cyl(g, m * 0.25, 2.5, L * 0.25, 1, 0, C.grey); break;
    case 'shotblast': box(g, L, 3.2, W, 0, 1.6, 0, C.steel); box(g, L * 0.5, 2.4, 0.05, 0, 1.4, W / 2 + 0.03, C.dark); break;
    case 'robot': box(g, L, 0.05, W, 0, 0.025, 0, C.dark); cyl(g, 0.35, 0.7, 0, 0.05, 0, C.orange); box(g, 0.3, 1.1, 0.3, 0, 1.3, 0, C.orange); box(g, 1.2, 0.25, 0.25, 0.6, 1.9, 0, C.orange); break;
    case 'inspection': box(g, L, 0.9, W, 0, 0.45, 0, C.light); box(g, 0.1, 1.4, W, -L * 0.45, 1.6, 0, C.steel); box(g, 0.1, 1.4, W, L * 0.45, 1.6, 0, C.steel); box(g, L, 0.15, 0.2, 0, 2.3, 0, C.steel); break;
    case 'warehouse':
      for (let lv = 0; lv < 5; lv++) { box(g, L, 0.08, W * 0.45, 0, 0.3 + lv * 1.6, W * 0.27, C.orange); box(g, L, 0.08, W * 0.45, 0, 0.3 + lv * 1.6, -W * 0.27, C.orange); }
      for (const x of [-L / 2 + 0.1, 0, L / 2 - 0.1]) { box(g, 0.12, 7, 0.12, x, 3.5, W * 0.48, C.blue); box(g, 0.12, 7, 0.12, x, 3.5, -W * 0.48, C.blue); }
      break;
    case 'agv': box(g, 1.2, 0.35, 0.8, 0, 0.2, 0, C.yellow); break;
    case 'buffer': box(g, L, 0.15, W, 0, 0.075, 0, C.wood); box(g, L * 0.8, 0.6, W * 0.8, 0, 0.45, 0, C.grey); break;
    case 'compressor': box(g, L, 1.6, W, 0, 0.8, 0, C.blue); cyl(g, m * 0.2, 0.6, L * 0.3, 1.6, 0, C.grey); break;
    case 'plc': box(g, Math.max(L, 0.3), 1.6, Math.max(W, 0.2), 0, 1.4, 0, C.grey); break;
    case 'cell': box(g, L, 0.9, W, 0, 0.45, 0, C.steel); box(g, L * 0.8, 1, W * 0.7, 0, 1.4, 0, C.light); break;
    default: box(g, L, H || 1.5, W, 0, (H || 1.5) / 2, 0, C.grey);
  }
  return g;
}

function build() {
  const el = host.value!; const lay = props.layout;
  if (!lay?.ok) { err.value = lay?.reason || $t('twin3D.noEquipmentLayoutYet'); loading.value = false; return; }
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  } catch { err.value = $t('twin3D.yourBrowserDoesnTSupport'); loading.value = false; return; }
  const w = el.clientWidth || 800, h = el.clientHeight || 480;
  renderer.setSize(w, h); renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  el.appendChild(renderer.domElement);
  scene = new THREE.Scene(); scene.background = new THREE.Color(0xf3f5f8);
  const [fw, fd] = lay.floor || [100, 50];
  camera = new THREE.PerspectiveCamera(45, w / h, 0.5, 4000);
  const hf = Math.tan((22.5 * Math.PI) / 180);                       // Fit the whole plant in view: use the farther distance of width / depth
  const dist = Math.max(fw / 2 / (hf * (w / h)), fd / 2 / hf) * 1.05;
  camera.position.set(fw * 0.5, dist * 0.78, -fd / 2 + dist * 0.62);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(fw / 2, 0, -fd / 2); controls.update();
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 1.2); sun.position.set(fw * 0.3, 80, 40); scene.add(sun);
  const floor = new THREE.Mesh(new THREE.BoxGeometry(fw, 0.05, fd), new THREE.MeshStandardMaterial({ color: 0xd5d9de }));
  floor.position.set(fw / 2, -0.025, -fd / 2); scene.add(floor);
  for (const a of lay.areas || []) {
    const b = a.bbox; if (!b) continue;
    const m = new THREE.Mesh(new THREE.PlaneGeometry(b[2] - b[0] - 0.3, b[3] - b[1] - 0.3), new THREE.MeshBasicMaterial({ color: 0x4078c0, transparent: true, opacity: 0.06 }));
    m.rotation.x = -Math.PI / 2; m.position.set((b[0] + b[2]) / 2, 0.01, -(b[1] + b[3]) / 2); scene.add(m);
  }
  const loader = new GLTFLoader(); loader.setRequestHeader({ 'X-Lang': lang, Authorization: 'Bearer ' + session.token });
  const cache = new Map<string, Promise<any>>();
  for (const d of lay.devices || []) {
    const holder = new THREE.Group(); holder.position.set(d.x, 0, -d.y); holder.rotation.y = (d.rot || 0) * Math.PI / 180; holder.userData.code = d.code;
    const pad = new THREE.Mesh(new THREE.PlaneGeometry(d.w + 0.4, d.d + 0.4), new THREE.MeshBasicMaterial({ color: 0x9aa5b4, transparent: true, opacity: 0.55 }));
    pad.rotation.x = -Math.PI / 2; pad.position.y = 0.02; holder.add(pad); pads.set(d.code, pad);
    const fallback = () => holder.add(parametric(d.kind3d || 'box', d.w, d.d, d.h));
    if (props.useModels && d.model3d && /\.glb$/i.test(d.model3d)) {
      const url = '/api/factory/files/models/' + d.model3d.split('/').pop();
      if (!cache.has(url)) cache.set(url, loader.loadAsync(url));
      cache.get(url)!.then((gltf) => {
        const obj = gltf.scene.clone(true);
        const bb = new THREE.Box3().setFromObject(obj); const sz = new THREE.Vector3(); bb.getSize(sz);
        const sx = sz.x > 0.01 ? d.w / sz.x : 1, sz_ = sz.z > 0.01 ? d.d / sz.z : 1;
        obj.scale.set(sx, (sx + sz_) / 2, sz_);
        const c = new THREE.Vector3(); bb.getCenter(c);
        obj.position.set(-c.x * sx, -bb.min.y * (sx + sz_) / 2, -c.z * sz_);
        holder.add(obj);
      }).catch(fallback);
    } else fallback();
    scene.add(holder);
  }
  paint();
  const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
  renderer.domElement.addEventListener('dblclick', (ev) => {
    const r = renderer!.domElement.getBoundingClientRect(); mouse.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(mouse, camera);
    const hit = ray.intersectObjects(scene.children, true).find((x) => { let o: any = x.object; while (o && !o.userData.code) o = o.parent; return !!o; });
    if (hit) { let o: any = hit.object; while (o && !o.userData.code) o = o.parent; emit('pick', o.userData.code); }
  });
  ro = new ResizeObserver(() => { const W = el.clientWidth, Hh = el.clientHeight; if (!W || !Hh || !renderer) return; renderer.setSize(W, Hh); camera.aspect = W / Hh; camera.updateProjectionMatrix(); });
  ro.observe(el);
  const loop = () => { raf = requestAnimationFrame(loop); controls.update(); renderer!.render(scene, camera); };
  loop();
  loading.value = false;
}
function paint() {
  const by = new Map((props.live || []).map((x: any) => [x.code, x]));
  for (const [code, pad] of pads) {
    const t = TONE[by.get(code)?.status || 'offline'] || 'off';
    (pad.material as THREE.MeshBasicMaterial).color.set(LEG[t][1]);
  }
}
watch(() => props.live, paint);
onMounted(() => setTimeout(build, 30));
onBeforeUnmount(() => { cancelAnimationFrame(raf); ro?.disconnect(); renderer?.dispose(); renderer?.domElement.remove(); });
</script>

<style scoped>
.t3 { position: relative; width: 100%; height: 100%; min-height: 360px; border-radius: var(--hm-r-md); overflow: hidden; background: #f3f5f8; }
.msg { position: absolute; inset: 0; display: grid; place-items: center; color: var(--hm-text-3); font-size: var(--hm-fs-sm); }
.legend { position: absolute; left: 10px; bottom: 8px; display: flex; gap: 10px; font-size: var(--hm-fs-xs); color: var(--hm-text-2); background: rgba(255, 255, 255, .85); padding: 3px 8px; border-radius: 6px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
</style>
