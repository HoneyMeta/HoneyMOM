import { $t, isZh } from '../i18n';
// Collected tags → display name, unit, decimals. Used by equipment cards and the tag table; raw tag paths aren't shown (raw names appear only in small print in the tag table).
// Tags not in the table: build a display name from English words in the path (Spindle/Load → Spindle Load); if still unknown, show the last segment.
type Def = [label: string, unit?: string, digits?: number];
const TAGS: Record<string, Def> = {
  'Common/PowerKW': [$t('common.power'), 'kW', 1], 'Electrical/Power': [$t('common.power'), 'kW', 0], 'Bath/Temperature': [$t('tagLabels.ironTemperature'), '°C', 0], 'Heat/Phase': [$t('tagLabels.meltingStage')], 'Heat/Number': [$t('tagLabels.heatNo')],
  'Bath/Weight': [$t('tagLabels.ironWeight'), 'kg', 0], 'Monitoring/Spindle/Load': [$t('common.spindleLoad'), '%', 0], 'Production/PartsProduced': [$t('tagLabels.machined'), $t('common.pcs'), 0], 'Tools/ActiveTool': [$t('tagLabels.currentToolPosition')],
  'Quality/BoreDiameter': [$t('tagLabels.boreDiameter'), 'mm', 3], 'Production/MoldCount': [$t('tagLabels.moldsMade'), $t('tagLabels.molds'), 0], 'Pouring/LastTemp': [$t('common.pouringTemp'), '°C', 0], 'Production/WaitingMolds': [$t('tagLabels.awaitingPour'), $t('tagLabels.molds'), 0],
  'Sand/Moisture': [$t('tagLabels.sandMoisture'), '%', 2], 'Sand/Compactability': [$t('tagLabels.compactability'), '%', 1], 'Result/Last': [$t('tagLabels.lastResult')], 'Result/OK': [$t('common.good'), $t('common.pcs'), 0], 'Result/NG': [$t('common.ng'), $t('common.pcs'), 0],
  'Brush/Wear': [$t('tagLabels.brushWear'), '%', 0], 'Production/PartsDone': [$t('common.completed'), $t('common.pcs'), 0], 'Turbine/Current': [$t('tagLabels.blastWheelCurrent'), 'A', 1], 'Batch/Count': [$t('common.lot'), $t('tagLabels.lots'), 0],
  'Treatment/Count': [$t('tagLabels.nodularizationCount'), $t('common.times'), 0], 'Ladle/Temperature': [$t('tagLabels.ladleTemperature'), '°C', 0], 'Ladle/ResidualMg': [$t('heats.residualMg'), '%', 3], 'Line/Molds': [$t('tagLabels.moldsOnLine'), $t('tagLabels.molds'), 0],
  'Line/ShakeoutTemp': [$t('tagLabels.shakeoutTemperature'), '°C', 0], 'Received/Count': [$t('common.receive'), $t('common.pcs'), 0], 'Crew/OnDuty': [$t('tagLabels.onShift'), $t('tagLabels.people'), 0], 'Crew/Busy': [$t('tagLabels.busy'), $t('tagLabels.people'), 0], 'Stock/Count': [$t('common.stock'), $t('common.pcs'), 0],
  'Stock/Utilization': [$t('tagLabels.locationUtilization'), '%', 1],
};
const WORDS: Record<string, string> = {
  common: '', monitoring: '', production: '', quality: '', electrical: '', status: $t('common.status'), state: $t('common.status'), power: $t('common.power'), powerkw: $t('common.power'), energy: $t('onboardCommon.energy'), energykwh: $t('onboardCommon.energy'),
  temperature: $t('onboardCommon.temperature'), temp: $t('onboardCommon.temperature'), pressure: $t('tagLabels.pressure'), speed: $t('tagLabels.speed'), load: $t('onboardCommon.load'), spindle: $t('tagLabels.spindle'), feed: $t('tagLabels.feed'), current: $t('tagLabels.current'), voltage: $t('tagLabels.voltage'), count: $t('onboardCommon.counter'), parts: $t('common.part'),
  partsproduced: $t('tagLabels.machined'), alarm: $t('common.alarm'), alarms: $t('common.alarm'), mode: $t('tagLabels.mode'), program: $t('tagLabels.program'), tool: $t('tools.tool'), tools: $t('tools.tool'), activetool: $t('tagLabels.currentToolPosition'), life: $t('common.life'), wear: $t('tagLabels.wear'),
  bath: $t('tagLabels.iron'), weight: $t('tagLabels.weight'), heat: $t('common.heat'), phase: $t('common.phase'), number: $t('common.no2'), sand: $t('tagLabels.sand'), moisture: $t('tagLabels.moisture'), ladle: $t('common.ladle'), line: $t('tagLabels.line'), result: $t('common.result'), ok: $t('common.good'), ng: $t('common.ng'),
  last: $t('common.latest'), batch: $t('common.lot'), turbine: $t('tagLabels.blastWheel'), brush: $t('tagLabels.brush'), stock: $t('common.stock'), utilization: $t('tagLabels.utilization'), crew: $t('common.teams'), busy: $t('tagLabels.busy'), onduty: $t('tagLabels.onShift'), received: $t('common.receive'),
  position: $t('common.position'), x: 'X', y: 'Y', z: 'Z', battery: $t('tagLabels.energy'), vibration: $t('tagLabels.vibration'), flow: $t('tagLabels.flow'), level: $t('tagLabels.level'), door: $t('tagLabels.door'), cycle: $t('station.cycleTime'), cycletime: $t('station.cycleTime'), oee: 'OEE', running: $t('common.running'),
};
/** Tag names may carry a namespace prefix (enterprise/plant/area/line/equipment/…): use only the recognized trailing part */
function key(name: string) {
  const parts = String(name).split('/');
  for (let i = 0; i < parts.length; i++) { const k = parts.slice(i).join('/'); if (TAGS[k]) return k; }
  return '';
}
export function tagDef(name: string): { label: string; unit: string; digits?: number } {
  const k = key(name);
  if (k) { const d = TAGS[k]; return { label: d[0], unit: d[1] || '', digits: d[2] }; }
  const segs = String(name).split('/').slice(-2);
  const zh = segs.map((s) => { const w = s.toLowerCase().replace(/[^a-z0-9]/g, ''); return w in WORDS ? WORDS[w] : null; });
  if (zh.every((x) => x !== null) && zh.join('')) return { label: zh.filter(Boolean).join(isZh ? '' : ' '), unit: '' };
  return { label: segs[segs.length - 1], unit: '' };
}
export const tagLabel = (name: string) => tagDef(name).label;
