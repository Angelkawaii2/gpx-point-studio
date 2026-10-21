const $=id=>document.getElementById(id);
const urls=Object.fromEntries(['default','dark','light'].map(s=>[s,`/api/tiles/${s}/{z}/{x}/{y}.png`]));
let map=L.map('map',{preferCanvas:true}).setView([31.23,121.47],10),layer,route,cloud,marker,track=null,style='default',index=0,start=0,end=0,playing=false,lastTick=0,playOffset=0,previewTimer;
function setStyle(s){style=s;if(layer)map.removeLayer(layer);layer=L.tileLayer(urls[s],{minZoom:1,maxZoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'+(s==='default'?'':' © CARTO')}).addTo(map);document.querySelectorAll('[data-style]').forEach(b=>b.classList.toggle('active',b.dataset.style===s));schedulePreview()}
setStyle('default');
async function api(path,data){const r=await fetch(path,{method:'POST',body:JSON.stringify(data),headers:{'Content-Type':'application/json'}});if(!r.ok)throw Error((await r.json()).error);return r.json()}
function config(){return {...drawingConfig(),id:track.id,index,zoom:+$('zoom').value,width:+$('width').value,height:+$('height').value,offset:playOffset,export_speed:+$('exportSpeed').value,fps:+$('fps').value,style}}
function label(i){const p=track.points[i];return p.time?new Date(p.time).toLocaleString():`点位 ${i+1}`}
function sliderValue(i){return track.points[i].offset}
function nearestOffset(v){let lo=0,hi=track.points.length-1;while(lo<hi){let m=(lo+hi)>>1;if(track.points[m].offset<v)lo=m+1;else hi=m}return lo>0&&Math.abs(track.points[lo-1].offset-v)<Math.abs(track.points[lo].offset-v)?lo-1:lo}
function positionAt(offset){const points=track.points;let lo=0,hi=points.length;while(lo<hi){const m=(lo+hi)>>1;if(points[m].offset<=offset)lo=m+1;else hi=m}const i=Math.max(0,lo-1),a=points[i],b=points[Math.min(i+1,points.length-1)],gap=b.offset-a.offset,ratio=gap>0&&a.segment===b.segment?(offset-a.offset)/gap:0;return {lat:a.lat+(b.lat-a.lat)*ratio,index:i,...interpolateLon(a,b,ratio)}}
function interpolateLon(a,b,ratio){const d=((b.lon-a.lon+180)%360+360)%360-180;return {lon:((a.lon+d*ratio+180)%360+360)%360-180}}
function update(){if(!track)return;const p=positionAt(playOffset);marker.setLatLng([p.lat,p.lon]);$('cursor').value=playOffset;$('start').value=sliderValue(start);$('end').value=sliderValue(end);$('current').textContent=track.timed?new Date(track.points[0].timestamp*1000+playOffset*1000).toLocaleString():`顺序时间 ${playOffset.toFixed(1)} s`;$('pointInfo').textContent=`${index+1} / ${track.points.length} 点`;const duration=track.points.at(-1).offset||1;$('selection').style.left=`${sliderValue(start)/duration*100}%`;$('selection').style.right=`${100-sliderValue(end)/duration*100}%`;updateExportEstimate();schedulePreview()}
function seek(offset){playOffset=Math.max(sliderValue(start),Math.min(sliderValue(end),offset));index=nearestOffset(playOffset);update()}
function select(i){index=Math.max(start,Math.min(end,i));playOffset=track.points[index].offset;update()}
let previewInFlight=false,previewPending=false;
function schedulePreview(){if(!track)return;previewPending=true;if(previewTimer||previewInFlight)return;previewTimer=setTimeout(renderPreview,playing?160:100)}
async function renderPreview(){previewTimer=null;if(!track)return;previewPending=false;previewInFlight=true;const snapshot=config();try{const r=await fetch('/api/preview',{method:'POST',body:JSON.stringify(snapshot)});if(!r.ok)throw Error((await r.json()).error);const blob=await r.blob();if(snapshot.id===track?.id&&snapshot.style===style){const old=$('preview').src;$('preview').src=URL.createObjectURL(blob);if(old.startsWith('blob:'))URL.revokeObjectURL(old);$('preview').hidden=false;$('empty').hidden=true;$('previewLabel').textContent=`${snapshot.width} × ${snapshot.height} · Z${snapshot.zoom}`}}catch(e){$('status').textContent='预览失败：'+e.message}finally{previewInFlight=false;if(previewPending)schedulePreview()}}

function nearest(latlng){let best=Infinity,result=0;track.points.forEach((p,i)=>{const d=map.latLngToLayerPoint(latlng).distanceTo(map.latLngToLayerPoint([p.lat,p.lon]));if(d<best){best=d;result=i}});return result}
function fit(){if(route)map.fitBounds(route.getBounds(),{padding:[32,32],maxZoom:17})}
$('fit').onclick=fit;
map.on('click',e=>{if(track){stop();select(nearest(e.latlng))}});
let importing=false;
async function importFile(file){if(!file||importing)return;if(!/\.gpx$/i.test(file.name)){$('status').textContent='请选择 .gpx 格式的轨迹文件。';return}importing=true;stop();$('status').textContent='正在读取 GPX…';try{const r=await fetch('/api/import',{method:'POST',body:file});const data=await r.json();if(!r.ok)throw Error(data.error);track=data;start=index=0;end=track.points.length-1;playOffset=0;[route,cloud,marker].forEach(l=>l&&map.removeLayer(l));const segments=[];track.points.forEach(p=>{if(!segments[p.segment])segments[p.segment]=[];segments[p.segment].push([p.lat,p.lon])});route=L.polyline(segments.filter(Boolean),{color:$('lineColor').value,weight:+$('lineWidth').value}).addTo(map);cloud=L.layerGroup(track.points.map((p,i)=>L.circleMarker([p.lat,p.lon],{radius:3,color:'#b64f36',weight:1,fillOpacity:.65}).on('click',e=>{L.DomEvent.stopPropagation(e);stop();select(i)}))).addTo(map);marker=L.marker([track.points[0].lat,track.points[0].lon],{draggable:true,icon:positionIcon()}).addTo(map);marker.on('drag',e=>{stop();select(nearest(e.target.getLatLng()))});marker.on('dragend',()=>update());for(const id of ['start','end','cursor']){ $(id).max=track.points.at(-1).offset||1;$(id).step='any'}$('first').textContent=label(0);$('last').textContent=label(end);$('count').textContent=track.points.length;$('export').disabled=false;$('status').textContent='轨迹已导入，可点击地图选点。';$('timeNote').textContent=track.timed?'拖动首尾裁剪，蓝色游标选择当前时间；点击地图或拖动定位标记也可选点。':'GPX 时间缺失或非递增，时间轴按原始点位顺序显示；播放每秒前进一个点。';applyDrawing();fit();resetTimeline();update()}catch(e){$('status').textContent=e.message}finally{importing=false}}
$('file').onchange=e=>{importFile(e.target.files[0]);e.target.value=''};
for(const id of ['start','end','cursor'])$(id).oninput=()=>{if(!track)return;stop();let i=nearestOffset(+$(id).value);if(id==='start')start=Math.min(i,end);else if(id==='end')end=Math.max(i,start);else {seek(+$(id).value);return}seek(playOffset)};
function stop(){playing=false;$('play').textContent='▶ 播放'}
$('play').onclick=()=>{if(!track)return;if(playing)return stop();if(playOffset>=sliderValue(end))select(start);playing=true;lastTick=performance.now();$('play').textContent='Ⅱ 暂停';requestAnimationFrame(tick)};
function tick(now){if(!playing)return;playOffset=Math.min(sliderValue(end),playOffset+(now-lastTick)/1000*+$('speed').value);lastTick=now;index=nearestOffset(playOffset);update();if(playOffset>=sliderValue(end)){stop();return}requestAnimationFrame(tick)}
$('prev').onclick=()=>{if(track){stop();select(index-1)}};$('next').onclick=()=>{if(track){stop();select(index+1)}};
document.querySelectorAll('[data-style]').forEach(b=>b.onclick=()=>setStyle(b.dataset.style));
$('zoom').oninput=()=>{$('zoomValue').textContent=$('zoom').value;schedulePreview()};for(const id of ['width','height'])$(id).oninput=schedulePreview;$('scope').onchange=update;
$('export').onclick=async()=>{if(!track)return;stop();$('export').disabled=true;$('download').hidden=true;$('exportProgress').hidden=false;$('progress').value=0;$('progressPercent').textContent='0%';$('progressFrames').textContent='准备导出…';$('progressEta').textContent='等待渲染';try{const job=await api('/api/export',{...config(),start:$('scope').value==='all'?0:start,end:$('scope').value==='all'?track.points.length-1:end});while(true){const r=await fetch('/api/jobs/'+job.id);if(!r.ok)throw Error('无法读取导出进度');const state=await r.json(),fraction=state.status==='complete'?1:Math.min(.99,state.done/state.total);$('progress').value=fraction;$('progressPercent').textContent=`${Math.floor(fraction*100)}%`;$('progressFrames').textContent=`${state.done.toLocaleString()} / ${state.total.toLocaleString()} 张`;$('progressEta').textContent=state.phase==='packing'?'正在完成 ZIP 打包…':`已用 ${formatDuration(state.elapsed)} · ${state.eta==null?'估算剩余时间中…':'剩余约 '+formatDuration(state.eta)}`;$('status').textContent=`正在后端渲染 ${state.done} / ${state.total}`;if(state.status==='error')throw Error(state.error);if(state.status==='complete'){$('download').href='/api/download/'+job.id;$('download').hidden=false;$('download').click();$('status').textContent=`导出完成：${state.total} 张 PNG，已打包 ZIP。`;$('progressEta').textContent=`已完成 · 序列时长 ${formatDuration(state.timing.sequence_duration)}`;break}await new Promise(r=>setTimeout(r,500))}}catch(e){$('status').textContent='导出失败：'+e.message;$('progressEta').textContent='导出失败'}finally{$('export').disabled=false}};


// Count nested drag entries so moving over child elements does not flicker.
let dragDepth=0;
const dropOverlay=$('dropOverlay');
function isFileDrag(e){return Array.from(e.dataTransfer?.types||[]).includes('Files')}
function clearDrag(){dragDepth=0;dropOverlay.hidden=true}
window.addEventListener('dragenter',e=>{if(!isFileDrag(e))return;e.preventDefault();dragDepth++;dropOverlay.hidden=false});
window.addEventListener('dragover',e=>{if(!isFileDrag(e))return;e.preventDefault();e.dataTransfer.dropEffect=importing?'none':'copy'});
window.addEventListener('dragleave',e=>{if(!isFileDrag(e)&&dragDepth===0)return;dragDepth=Math.max(0,dragDepth-1);if(!dragDepth)clearDrag()});
window.addEventListener('drop',e=>{if(!isFileDrag(e))return;e.preventDefault();clearDrag();const files=Array.from(e.dataTransfer.files);if(files.length!==1){$('status').textContent='请一次拖入一个 GPX 文件。';return}importFile(files[0])});
window.addEventListener('dragend',clearDrag);
window.addEventListener('blur',clearDrag);
window.addEventListener('keydown',e=>{if(e.key==='Escape')clearDrag()});

// API keys stay in the backend: only configuration status is returned.
function showKeyStatus(data){$('keyStatus').textContent=data.carto_key_configured?'已配置 key（保存在本机 .env）':'未配置 key；Dark / Light 需配置 key，默认 OSM 可直接使用'}
fetch('/api/config').then(r=>{if(!r.ok)throw Error('读取失败');return r.json()}).then(showKeyStatus).catch(()=>{$('keyStatus').textContent='无法读取 key 配置，请检查后端是否已更新'});
async function updateKey(value){$('saveKey').disabled=$('clearKey').disabled=true;try{const data=await api('/api/config',{carto_api_key:value});showKeyStatus(data);$('cartoKey').value='';layer.redraw();schedulePreview()}catch(e){$('keyStatus').textContent=e.message}finally{$('saveKey').disabled=$('clearKey').disabled=false}}
$('saveKey').onclick=()=>{const value=$('cartoKey').value.trim();if(!value){$('keyStatus').textContent='请先输入 API key；删除已有配置请点击清除。';return}updateKey(value)};
$('clearKey').onclick=()=>updateKey('');

let timelineZoom=1;
const timelineViewport=$('timelineViewport'),timelineTrack=$('timelineTrack');
function elapsedLabel(seconds,precision=0){if(!track?.timed)return `#${Math.round(seconds)+1}`;const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=precision?(seconds%60).toFixed(precision):Math.floor(seconds%60);return h?`${h}:${String(m).padStart(2,'0')}:${String(s).padStart(precision?3+precision:2,'0')}`:`${m}:${String(s).padStart(precision?3+precision:2,'0')}`}
function drawRuler(){const ruler=$('timeRuler');ruler.replaceChildren();if(!track)return;const duration=track.points.at(-1).offset,count=Math.max(1,Math.min(160,Math.max(5,Math.round(timelineZoom*8)),track.timed?160:Math.max(1,duration))),precision=duration/count<.1?2:duration/count<1?1:0;for(let i=0;i<=count;i++){const tick=document.createElement('span');tick.style.left=`${i/count*100}%`;tick.textContent=elapsedLabel(duration*i/count,precision);ruler.appendChild(tick)}}
function zoomTimeline(value,anchor=timelineViewport.clientWidth/2){const next=Math.max(1,Math.min(32,value)),fraction=(timelineViewport.scrollLeft+anchor)/Math.max(1,timelineViewport.scrollWidth);timelineZoom=next;timelineTrack.style.width=`${next*100}%`;timelineViewport.scrollLeft=fraction*timelineViewport.scrollWidth-anchor;$('timelineZoomValue').textContent=`${Number(next.toFixed(1))}×`;$('timelineZoomOut').disabled=next<=1;$('timelineZoomIn').disabled=next>=32;drawRuler()}
function resetTimeline(){zoomTimeline(1,0);timelineViewport.scrollLeft=0}
$('timelineZoomIn').onclick=()=>zoomTimeline(timelineZoom*1.5);
$('timelineZoomOut').onclick=()=>zoomTimeline(timelineZoom/1.5);
$('timelineFit').onclick=resetTimeline;
timelineViewport.addEventListener('wheel',e=>{e.preventDefault();if(e.shiftKey){timelineViewport.scrollLeft+=e.deltaY||e.deltaX;return}if(Math.abs(e.deltaX)>Math.abs(e.deltaY)&&!e.ctrlKey){timelineViewport.scrollLeft+=e.deltaX;return}zoomTimeline(timelineZoom*Math.exp(-e.deltaY*.003),e.clientX-timelineViewport.getBoundingClientRect().left)},{passive:false});
timelineViewport.addEventListener('keydown',e=>{if(e.target!==timelineViewport)return;if(e.key==='+'||e.key==='='){e.preventDefault();zoomTimeline(timelineZoom*1.5)}else if(e.key==='-'){e.preventDefault();zoomTimeline(timelineZoom/1.5)}else if(e.key==='0'){e.preventDefault();resetTimeline()}});
resetTimeline();

function drawingConfig(){return {show_line:$('showLine').checked,line_width:+$('lineWidth').value,line_color:$('lineColor').value,show_point:$('showPoint').checked,point_diameter:+$('pointDiameter').value,point_color:$('pointColor').value,point_outline_width:+$('pointOutlineWidth').value,point_outline_color:$('pointOutlineColor').value}}
function positionIcon(){const p=drawingConfig(),size=p.point_diameter,border=Math.min(p.point_outline_width,Math.floor(size/2));return L.divIcon({className:'positionMarker',iconSize:[size,size],iconAnchor:[size/2,size/2],html:`<span style="width:${size}px;height:${size}px;background:${p.point_color};border:${border}px solid ${p.point_outline_color}"></span>`})}
function applyDrawing(){const p=drawingConfig();if(route)route.setStyle({color:p.line_color,weight:p.line_width,opacity:p.show_line&&p.line_width>0?1:0});if(cloud)cloud.eachLayer(dot=>dot.setStyle({color:p.line_color,fillColor:p.line_color,opacity:p.show_line?1:0,fillOpacity:p.show_line ? .65 : 0}));if(marker){marker.setIcon(positionIcon());const visible=p.show_point&&p.point_diameter>0;marker.setOpacity(visible?1:0);if(visible)marker.dragging.enable();else marker.dragging.disable()}schedulePreview()}
for(const id of ['showLine','lineWidth','lineColor','showPoint','pointDiameter','pointColor','pointOutlineWidth','pointOutlineColor'])$(id).addEventListener('input',applyDrawing);
function formatBytes(n){return n<1024?`${n} B`:n<1048576?`${(n/1024).toFixed(1)} KB`:`${(n/1048576).toFixed(1)} MB`}
async function refreshCache(){try{const r=await fetch('/api/cache');if(!r.ok)throw Error('缓存状态读取失败');const c=await r.json();$('cacheStats').textContent=`磁盘：${c.files} 张 · ${formatBytes(c.bytes)}\n本次运行命中：内存 ${c.memory_hits} / 磁盘 ${c.disk_hits}\n网络下载：${c.downloads} 张 · 请求中 ${c.active}\n失败：${c.errors} 次`;$('cacheError').textContent=c.last_error||''}catch(e){$('cacheError').textContent=e.message}}
$('refreshCache').onclick=refreshCache;
refreshCache();setInterval(()=>{if(!document.hidden)refreshCache()},2000);

function formatDuration(seconds){if(!Number.isFinite(seconds))return '—';const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=(seconds%60).toFixed(2);return `${h?String(h).padStart(2,'0')+':':''}${String(m).padStart(2,'0')}:${s.padStart(5,'0')}`}
function updateExportEstimate(){if(!track)return;const a=$('scope').value==='all'?0:start,b=$('scope').value==='all'?track.points.length-1:end,duration=track.points[b].offset-track.points[a].offset,speed=+$('exportSpeed').value,fps=+$('fps').value,count=Math.max(1,Math.ceil(duration/speed*fps));$('durationEstimate').textContent=formatDuration(duration/speed);$('durationFormula').textContent=`${formatDuration(duration)} ÷ ${speed}× · ${fps} fps${track.timed?'':'（时间缺失，按点序估算）'}`;$('outputCount').textContent=count.toLocaleString()}
function setExportSpeed(value){if(!Number.isFinite(value))return;const speed=Math.round(Math.max(1,Math.min(100,value))*10)/10;$('exportSpeed').value=speed;$('exportSpeedNumber').value=speed;updateExportEstimate()}
$('exportSpeed').oninput=()=>setExportSpeed(+$('exportSpeed').value);
$('exportSpeedNumber').oninput=()=>{if($('exportSpeedNumber').value!=='')setExportSpeed(+$('exportSpeedNumber').value)};
$('exportSpeedNumber').onchange=()=>setExportSpeed(+$('exportSpeedNumber').value||1);
$('fps').onchange=updateExportEstimate;
