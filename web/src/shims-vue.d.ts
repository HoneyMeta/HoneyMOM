// Lets tsc --noEmit understand .vue SFCs (unused by the vite build, type checking only)
declare module '*.vue' {
  import type { DefineComponent } from 'vue';
  const component: DefineComponent<object, object, any>;
  export default component;
}
