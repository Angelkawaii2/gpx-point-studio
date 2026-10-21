const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const catalog=JSON.parse(fs.readFileSync('web/locales.json','utf8'));
const script=fs.readFileSync('web/i18n.js','utf8');
async function main(){
 const reference=Object.keys(catalog['zh-CN']).sort();
 for(const [locale,values] of Object.entries(catalog)){
  assert.deepEqual(Object.keys(values).sort(),reference,locale+' missing translation keys');
  for(const [key,text] of Object.entries(values)){
   const variables=s=>Array.from(s.matchAll(/\{(\w+)\}/g),m=>m[1]).sort();
   if(key==='播放预计'||key==='导出点位诊断')continue;
   assert.deepEqual(variables(text),variables(key),locale+' placeholders: '+key);
  }
  const selector={value:''};
  const root={nodeType:1,childNodes:[],hasAttribute:()=>false};
  const window={dispatchEvent(){}};
  const ctx={window,navigator:{language:locale},localStorage:{getItem:()=>locale,setItem(){}},fetch:async()=>({ok:true,json:async()=>catalog}),document:{documentElement:{},body:root,getElementById:()=>selector},Node:{TEXT_NODE:3,ELEMENT_NODE:1},CustomEvent:class{},MutationObserver:class{observe(){}}};
  vm.createContext(ctx);vm.runInContext(script,ctx);await window.i18n.ready;
  assert.equal(window.i18n.locale,locale);
  const translated=window.i18n.translate('导出完成：25 张 PNG，已打包 ZIP。');
  assert(translated.includes('25'),locale+' dynamic count missing');
  if(['en','de','fr'].includes(locale))assert(!/[\u4e00-\u9fff]/.test(translated),locale+' untranslated dynamic message');
  assert.equal(window.i18n.translate('显示当前范围 ↗'),catalog[locale]['显示当前范围 ↗']);
  assert(window.i18n.t('{hours} 小时 {minutes} 分钟 {seconds} 秒',{hours:1,minutes:2,seconds:3}).includes('3'));
  window.i18n.setLocale('en');assert.equal(window.i18n.locale,'en');
 }
 console.log('PASS: six complete catalogs, placeholders, dynamic status translation, duration units and locale switching');
}
main().catch(error=>{console.error(error);process.exitCode=1});
