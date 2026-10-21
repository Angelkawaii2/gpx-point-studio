/* Runtime UI localization; map-label language is intentionally independent. */
(() => {
  const supported=['zh-CN','zh-TW','zh-HK','en','de','fr'];
  const detect=value=>{const v=(value||'').toLowerCase();return v.startsWith('zh-hk')?'zh-HK':v.startsWith('zh-tw')||v.startsWith('zh-hant')?'zh-TW':v.startsWith('zh')?'zh-CN':v.startsWith('de')?'de':v.startsWith('fr')?'fr':'en'};
  let locale=detect(navigator.language),catalog={},patterns=[],observer;
  try{const saved=localStorage.getItem('gpxStudio.uiLanguage');if(supported.includes(saved))locale=saved}catch{}
  const textSources=new WeakMap(),attributeSources=new WeakMap();
  const escape=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  function compile(){patterns=[];for(const key of Object.keys(catalog['zh-CN']||{})){const names=[...key.matchAll(/\{(\w+)\}/g)].map(m=>m[1]);if(!names.length)continue;const parts=key.split(/\{\w+\}/g);patterns.push({key,names,regex:new RegExp('^'+parts.map(escape).join('([\\s\\S]*?)')+'$')})}}
  function t(key,values={}){let result=catalog[locale]?.[key]??catalog.en?.[key]??key;return result.replace(/\{(\w+)\}/g,(all,name)=>Object.hasOwn(values,name)?String(values[name]):all)}
  function translate(value,depth=0){if(!value||depth>5)return value;const text=value.trim();let result;if(Object.hasOwn(catalog[locale]||{},text))result=t(text);else{for(const rule of patterns){const match=text.match(rule.regex);if(!match)continue;const values={};rule.names.forEach((name,i)=>values[name]=translate(match[i+1],depth+1));result=t(rule.key,values);break}}if(result==null)return value;return value.slice(0,value.indexOf(text))+result+value.slice(value.indexOf(text)+text.length)}
  function text(node){if(node.parentElement?.closest('script,style,[data-no-i18n]')||node.parentElement?.closest('#uiLanguage'))return;let info=textSources.get(node);if(!info||node.nodeValue!==info.output)info={source:node.nodeValue};const output=translate(info.source);info.output=output;textSources.set(node,info);if(node.nodeValue!==output)node.nodeValue=output}
  const attrs=['aria-label','aria-valuetext','title','placeholder','alt'];
  function attributes(element){let sources=attributeSources.get(element);if(!sources){sources=new Map();attributeSources.set(element,sources)}for(const name of attrs){if(!element.hasAttribute(name))continue;const current=element.getAttribute(name);let info=sources.get(name);if(!info||current!==info.output)info={source:current};info.output=translate(info.source);sources.set(name,info);if(current!==info.output)element.setAttribute(name,info.output)}}
  function walk(root){if(root.nodeType===Node.TEXT_NODE){text(root);return}if(root.nodeType!==Node.ELEMENT_NODE)return;attributes(root);for(const node of root.childNodes)walk(node)}
  function apply(){document.documentElement.lang=locale;walk(document.body);const select=document.getElementById('uiLanguage');if(select)select.value=locale}
  function setLocale(value){if(!supported.includes(value))return;locale=value;try{localStorage.setItem('gpxStudio.uiLanguage',value)}catch{}apply();window.dispatchEvent(new CustomEvent('languagechange',{detail:{locale}}))}
  const ready=fetch('/locales.json').then(r=>{if(!r.ok)throw Error('Unable to load UI translations');return r.json()}).then(data=>{catalog=data;compile()});
  window.i18n={get locale(){return locale},supported,t,translate,setLocale,ready,init(){apply();document.getElementById('uiLanguage').onchange=e=>setLocale(e.target.value);observer=new MutationObserver(records=>{for(const record of records){if(record.type==='characterData')text(record.target);else if(record.type==='attributes')attributes(record.target);else for(const node of record.addedNodes)walk(node)}});observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:attrs})}};
})();
