// E-signature dialog (IATF 16949 critical release): when the backend returns 428 + detail.esign, api.ts calls esignPrompt; the user re-enters their login password and picks the signature meaning,
// then {esign: {password, meaning}} is added to the original request and resent. The signature goes into the e-signature log and audit log (who, when, meaning). See docs/dev/exec.md "E-Signature".
import { $t } from './i18n';
import { ElInput, ElMessage, ElMessageBox, ElRadioButton, ElRadioGroup } from 'element-plus';
import { h, reactive } from 'vue';
import { session } from './api';

const CSS = `.hm-esign-box{width:min(440px,94vw)}.hm-esign{display:flex;flex-direction:column;gap:10px}
.hm-esign-obj{font-weight:600;color:var(--hm-text);padding:8px 10px;background:var(--hm-surface-2);border-radius:var(--hm-r-sm)}
.hm-esign-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.hm-esign-k{color:var(--hm-text-2);min-width:56px}
.hm-esign-note{font-size:var(--hm-fs-2xs);color:var(--hm-text-2)}`;
if (typeof document !== 'undefined' && !document.getElementById('hm-esign-css')) {
  const s = document.createElement('style'); s.id = 'hm-esign-css'; s.textContent = CSS; document.head.appendChild(s);
}

export interface EsignInfo { entity: string; id: number; action: string; point: string; label: string; meanings: string[]; default?: string }

export async function esignPrompt(info: EsignInfo): Promise<{ password: string; meaning: string } | null> {
  const st = reactive({ password: '', meaning: info.default || info.meanings?.[0] || $t('common.approve3') });
  const who = session.user ? $t('common.n01', [session.user.name || '', session.user.username]) : '';
  try {
    await ElMessageBox({
      title: $t('esign.eSignature0', [info.point]),
      customClass: 'hm-esign-box',
      message: () => h('div', { class: 'hm-esign', 'data-test': 'esign-box' }, [
        h('div', { class: 'hm-esign-obj' }, info.label || ''),
        h('div', { class: 'hm-esign-row' }, [h('span', { class: 'hm-esign-k' }, $t('esign.signer')), h('span', who)]),
        h('div', { class: 'hm-esign-row' }, [h('span', { class: 'hm-esign-k' }, $t('esign.meaningOfSignature')),
          h(ElRadioGroup, { modelValue: st.meaning, size: 'small', 'onUpdate:modelValue': (v: any) => { st.meaning = v; } },
            () => (info.meanings || [$t('common.approve3')]).map((m) => h(ElRadioButton, { value: m, 'data-test': 'esign-meaning-' + m }, () => m)))]),
        h(ElInput, { modelValue: st.password, type: 'password', showPassword: true, placeholder: $t('esign.reEnterYourLoginPassword'), autocomplete: 'current-password',
          'data-test': 'esign-pw', 'onUpdate:modelValue': (v: string) => { st.password = v; } }),
        h('div', { class: 'hm-esign-note' }, $t('esign.yourSignatureIsEquivalentTo')),
      ]),
      showCancelButton: true,
      confirmButtonText: $t('esign.signSubmit'),
      cancelButtonText: $t('common.cancel'),
      closeOnClickModal: false,
      beforeClose: (action, _inst, done) => {
        if (action === 'confirm' && !st.password) { ElMessage.warning($t('esign.pleaseEnterYourPassword')); return; }
        done();
      },
    });
    return { password: st.password, meaning: st.meaning };
  } catch {
    return null;
  }
}
