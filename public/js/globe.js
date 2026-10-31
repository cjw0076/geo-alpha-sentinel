import {greatCircle,laneGraph,localCorridor,relevantRouteWeights} from './lanes.js';

const SCALE=4, R=1.495, rad=Math.PI/180;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pos=(lat,lon,r=R)=>{const phi=(90-lat)*rad,theta=(lon+180)*rad;return [-r*Math.sin(phi)*Math.cos(theta),r*Math.cos(phi),r*Math.sin(phi)*Math.sin(theta)];};
const project=([lat,lon])=>[(lon+180)/360*960,(90-lat)/180*480];
const count=v=>Number(v)>0?Math.max(1,Math.round(Number(v)/SCALE)):0;
// Largest absolute shortfall (ships/day lost vs 365-day baseline) among Δ ≤ −25%; else the steepest Δ.
const anomaly=items=>items.filter(x=>Number(x.baseline_365d)>=10&&Number(x.delta_pct)<=-25).sort((a,b)=>(b.baseline_365d-b.mean_7d)-(a.baseline_365d-a.mean_7d))[0]||items.filter(x=>Number(x.baseline_365d)>=10&&Number.isFinite(Number(x.delta_pct))).sort((a,b)=>a.delta_pct-b.delta_pct)[0]||null;
function makeData(state){
  const items=(state.chokepoints?.items||[]).filter(x=>Number.isFinite(Number(x.lat))&&Number.isFinite(Number(x.lon)));
  const graph=laneGraph(items),byId=new Map(items.map(x=>[x.id,x])),weights=relevantRouteWeights(state.exposure);
  for(const name of state.exposure?.chokepoints_mentioned||[]){const item=items.find(x=>x.name.toLowerCase()===name.toLowerCase());if(item)weights.set(item.id,Math.max(weights.get(item.id)||0,.15));}
  const debug=items.map(x=>({id:x.id,live:count(x.mean_7d),ghost:count(x.baseline_365d)}));
  window.__globeDebug=debug;
  return {items,graph,byId,weights,debug,focus:anomaly(items)};
}
function legend(state){return `Particles ∝ 7-day mean daily transits · ghost trails ∝ 365-day baseline · 1 mark per ${SCALE} ships/day (minimum 1 when positive) · IMF PortWatch (AIS) as of ${esc(state.chokepoints?.as_of||'—')} · routes and motion schematic, not vessel tracks or speed`;}
function callout(host,point,onChokepoint){if(host.id==='signal-globe'||!point)return;const button=document.createElement('button');button.type='button';button.className='globe-callout';button.innerHTML=`<b>${esc(point.name.toUpperCase())}</b><strong>${Number(point.mean_7d).toFixed(1)} ships/day · ${Number(point.delta_pct)>=0?'+':''}${Number(point.delta_pct).toFixed(1)}%</strong><small>IMF PORTWATCH · SOURCE ↗</small>`;button.onclick=()=>onChokepoint?.(point);host.appendChild(button);}
function fallback(host,state,data,onChokepoint){
  const {items,graph,byId,weights,focus}=data,active=weights.size>0;
  const grid=[];for(let lat=-60;lat<=60;lat+=30){const y=project([lat,0])[1];grid.push(`<line x1="0" y1="${y}" x2="960" y2="${y}"/>`);}for(let lon=-150;lon<=150;lon+=30){const x=project([0,lon])[0];grid.push(`<line x1="${x}" y1="0" x2="${x}" y2="480"/>`);}
  const laneLines=graph.flatMap(e=>{const path=e.points.flatMap((p,i)=>i?greatCircle(e.points[i-1],p,14).slice(1):[p]);return path.slice(1).map((p,i)=>{const a=project(path[i]),b=project(p);return Math.abs(a[0]-b[0])>480?'':`<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}"/>`;});}).join('');
  const dots=items.map(x=>{const [cx,cy]=project([x.lat,x.lon]),alarm=x.delta_pct<=-25,live=count(x.mean_7d),ghost=count(x.baseline_365d),highlight=!active||weights.has(x.id);return `<g data-globe-id="${esc(x.id)}" tabindex="0" role="button" aria-label="${esc(x.name)} ${live} live marks ${ghost} baseline marks"><circle cx="${cx}" cy="${cy}" r="${Math.max(3,Math.min(8,3+Math.sqrt(Math.max(0,Number(x.mean_7d)||0))/3))}" fill="${alarm?'#ff3b3b':highlight?'#ff9f1c':'#6b5b46'}"/><title>${esc(x.name)} · ${Number(x.mean_7d).toFixed(1)}/day · ${Number(x.delta_pct).toFixed(1)}%</title></g>`;}).join('');
  host.innerHTML=`<svg viewBox="0 0 960 480" width="100%" height="100%" preserveAspectRatio="xMidYMid meet" role="img" aria-label="PortWatch chokepoints on an equirectangular map"><rect width="960" height="480" fill="#060606"/><g stroke="#24201a" stroke-width=".7">${grid.join('')}</g><g stroke="#604320" stroke-width=".8" opacity=".42">${laneLines}</g>${dots}</svg><div class="globe-legend">${legend(state)}</div>`;
  for(const node of host.querySelectorAll('[data-globe-id]')){const x=byId.get(node.dataset.globeId);node.addEventListener('click',()=>onChokepoint?.(x));node.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onChokepoint?.(x);}});}
  callout(host,focus,onChokepoint);return {resize(){},destroy(){host.innerHTML=''}};
}

// `still` (reduced motion): draw single frames on demand instead of the animation loop.
export async function mountGlobe(host,state,{onChokepoint,focus,still=false}={}){
  const data=makeData(state),{items,graph,byId,weights}=data;
  if(!window.WebGLRenderingContext)return fallback(host,state,data,onChokepoint);
  let THREE,OrbitControls;
  try{[THREE,{OrbitControls}]=await Promise.all([import('three'),import('three/addons/controls/OrbitControls.js')]);}catch{return fallback(host,state,data,onChokepoint);}
  let renderer;
  try{renderer=new THREE.WebGLRenderer({antialias:true,powerPreference:'high-performance'});}catch{return fallback(host,state,data,onChokepoint);}
  const width=Math.max(100,host.clientWidth),height=Math.max(220,host.clientHeight);
  renderer.setPixelRatio(Math.min(1.5,devicePixelRatio));renderer.setSize(width,height);renderer.setClearColor(0x030303);
  host.innerHTML='';host.appendChild(renderer.domElement);
  const scene=new THREE.Scene(),earth=new THREE.Group();scene.add(earth);
  const camera=new THREE.PerspectiveCamera(37,width/height,.1,100),target=focus||data.focus;
  const cameraDistance=host.id==='signal-globe'?3.4:4.7;
  camera.position.set(...(target?pos(target.lat,target.lon,cameraDistance):[0,0,cameraDistance]));camera.lookAt(0,0,0);
  const controls=new OrbitControls(camera,renderer.domElement);controls.enablePan=false;controls.enableDamping=true;controls.dampingFactor=.08;controls.minDistance=2.6;controls.maxDistance=7;controls.autoRotate=false;
  const sphere=new THREE.Mesh(new THREE.SphereGeometry(1.48,64,48),new THREE.MeshBasicMaterial({color:0x090806}));earth.add(sphere);
  const black=new THREE.DataTexture(new Uint8Array([0,0,0,255]),1,1);black.needsUpdate=true;
  const nightMat=new THREE.ShaderMaterial({uniforms:{uMap:{value:black},uLoaded:{value:0}},vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',fragmentShader:'uniform sampler2D uMap;uniform float uLoaded;varying vec2 vUv;void main(){vec3 tex=texture2D(uMap,vUv).rgb;float l=dot(tex,vec3(.24,.68,.08));float city=pow(max(l,0.),.62);vec3 ink=vec3(.026,.021,.014);vec3 gold=vec3(1.,.53,.16)*city*2.3;gl_FragColor=vec4(ink+gold*uLoaded,1.0);}'});
  const lights=new THREE.Mesh(new THREE.SphereGeometry(1.481,64,48),nightMat);earth.add(lights);
  const texture=new THREE.TextureLoader();texture.load('https://cdn.jsdelivr.net/npm/three-globe@2.31.0/example/img/earth-night.jpg',map=>{map.colorSpace=THREE.SRGBColorSpace;nightMat.uniforms.uMap.value=map;nightMat.uniforms.uLoaded.value=1;redraw();},undefined,()=>{});
  const gridMat=new THREE.LineBasicMaterial({color:0x4d351a,transparent:true,opacity:.16});
  for(let lat=-60;lat<=60;lat+=30){const pts=[];for(let lon=-180;lon<=180;lon+=4)pts.push(new THREE.Vector3(...pos(lat,lon,1.485)));earth.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),gridMat));}
  for(let lon=-180;lon<180;lon+=30){const pts=[];for(let lat=-89;lat<=89;lat+=4)pts.push(new THREE.Vector3(...pos(lat,lon,1.485)));earth.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),gridMat));}
  const atmosphere=new THREE.Mesh(new THREE.SphereGeometry(1.57,48,32),new THREE.ShaderMaterial({transparent:true,side:THREE.BackSide,depthWrite:false,vertexShader:'varying vec3 vN;void main(){vN=normalize(normalMatrix*normal);gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',fragmentShader:'varying vec3 vN;void main(){float a=pow(max(0.,.68-dot(vN,vec3(0.,0.,1.))),2.)*.20;gl_FragColor=vec4(.76,.39,.09,a);}' }));earth.add(atmosphere);

  // Schematic connecting lines are visually separate from observed PortWatch quantities.
  const networkVerts=[];
  for(const e of graph){const route=e.points.flatMap((p,i)=>i?greatCircle(e.points[i-1],p,12).slice(1):[p]);for(let i=1;i<route.length;i++)networkVerts.push(...pos(...route[i-1],1.49),...pos(...route[i],1.49));}
  const networkGeo=new THREE.BufferGeometry();networkGeo.setAttribute('position',new THREE.Float32BufferAttribute(networkVerts,3));earth.add(new THREE.LineSegments(networkGeo,new THREE.LineBasicMaterial({color:0x91621f,transparent:true,opacity:.12,depthWrite:false})));

  const ghostStrong=[],ghostDim=[],starts=[],ends=[],offsets=[],fades=[],sizes=[];
  const focused=weights.size>0;
  for(const x of items){
    const [a,c,b]=localCorridor(x,graph,byId),live=count(x.mean_7d),ghost=count(x.baseline_365d),isFocus=!focused||weights.has(x.id),bucket=isFocus?ghostStrong:ghostDim;
    for(let j=0;j<ghost;j++){
      const shift=(j-(ghost-1)/2)*.033;
      const route=[a,c,b].map(([lat,lon])=>[Math.max(-89,Math.min(89,lat+shift)),lon+shift*.45]);
      const samples=[...greatCircle(route[0],route[1],5),...greatCircle(route[1],route[2],5).slice(1)];
      for(let i=1;i<samples.length;i++)bucket.push(...pos(...samples[i-1],1.506),...pos(...samples[i],1.506));
    }
    for(let j=0;j<live;j++){
      starts.push(...pos(...a,1.516));ends.push(...pos(...b,1.516));offsets.push((j+.5)/live);fades.push(isFocus?1:.17);sizes.push(x.delta_pct<=-25?3.3:2.7);
    }
  }
  const line=(vertices,opacity)=>{if(!vertices.length)return;const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));earth.add(new THREE.LineSegments(g,new THREE.LineBasicMaterial({color:0xc38a36,transparent:true,opacity,depthWrite:false})));};
  line(ghostStrong,.15);line(ghostDim,.025);
  const particleGeo=new THREE.BufferGeometry();particleGeo.setAttribute('position',new THREE.Float32BufferAttribute(starts,3));particleGeo.setAttribute('aEnd',new THREE.Float32BufferAttribute(ends,3));particleGeo.setAttribute('aOffset',new THREE.Float32BufferAttribute(offsets,1));particleGeo.setAttribute('aFade',new THREE.Float32BufferAttribute(fades,1));particleGeo.setAttribute('aSize',new THREE.Float32BufferAttribute(sizes,1));
  const particleMat=new THREE.ShaderMaterial({transparent:true,depthWrite:false,uniforms:{uTime:{value:0},uPixelRatio:{value:renderer.getPixelRatio()}},vertexShader:`attribute vec3 aEnd;attribute float aOffset;attribute float aFade;attribute float aSize;uniform float uTime;uniform float uPixelRatio;varying float vFade;void main(){float t=fract(uTime*.045+aOffset);vec3 p=normalize(mix(position,aEnd,t))*1.518;vec4 mv=modelViewMatrix*vec4(p,1.0);gl_Position=projectionMatrix*mv;gl_PointSize=aSize*uPixelRatio*(4.5/max(1.5,-mv.z));vFade=aFade;}`,fragmentShader:`varying float vFade;void main(){float d=length(gl_PointCoord-.5);float a=smoothstep(.5,.18,d);gl_FragColor=vec4(1.0,.70,.26,a*vFade);}`});
  earth.add(new THREE.Points(particleGeo,particleMat));

  const beacons=[],raycaster=new THREE.Raycaster(),mouse=new THREE.Vector2();
  for(const x of items){const alert=x.delta_pct<=-25,volume=Math.max(0,Number(x.mean_7d)||0),radius=alert?.028:Math.min(.032,.011+Math.sqrt(volume)/1000);const mesh=new THREE.Mesh(new THREE.SphereGeometry(radius,9,8),new THREE.MeshBasicMaterial({color:alert?0xff3b3b:weights.size&&!weights.has(x.id)?0x6b5030:0xffaa37}));mesh.position.set(...pos(x.lat,x.lon,1.525));mesh.userData={point:x,alert};earth.add(mesh);beacons.push(mesh);}
  const tip=document.createElement('div');tip.className='globe-tooltip';tip.hidden=true;tip.style.position='absolute';host.appendChild(tip);
  const legendEl=document.createElement('div');legendEl.className='globe-legend';legendEl.textContent=legend(state);host.appendChild(legendEl);
  callout(host,data.focus,onChokepoint);
  function hitAt(e){const rect=renderer.domElement.getBoundingClientRect();mouse.set((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);raycaster.setFromCamera(mouse,camera);return raycaster.intersectObjects(beacons)[0]?.object?.userData?.point||null;}
  renderer.domElement.addEventListener('pointermove',e=>{const x=hitAt(e);if(!x){tip.hidden=true;return;}const box=renderer.domElement.getBoundingClientRect();tip.innerHTML=`${esc(x.name)}<br>${Number(x.mean_7d).toFixed(1)} ships/day · ${Number(x.delta_pct).toFixed(1)}%`;tip.style.left=`${e.clientX-box.left+10}px`;tip.style.top=`${e.clientY-box.top+10}px`;tip.hidden=false;});
  renderer.domElement.addEventListener('pointerleave',()=>tip.hidden=true);
  renderer.domElement.addEventListener('click',e=>{const x=hitAt(e);if(x)onChokepoint?.(x);});
  let closed=false,visible=true,frame=0;
  function paint(t){frame=0;if(closed||!visible||document.hidden)return;particleMat.uniforms.uTime.value=t/1000;for(const b of beacons)if(b.userData.alert)b.scale.setScalar(1+.25*Math.sin(t/500));controls.update();renderer.render(scene,camera);if(!still)frame=requestAnimationFrame(paint);}
  function redraw(){if(still&&!closed)renderer.render(scene,camera);}
  if(still){controls.enableDamping=false;controls.addEventListener('change',redraw);}
  function start(){if(!frame&&!closed&&visible&&!document.hidden)frame=requestAnimationFrame(paint);}
  function stop(){if(frame)cancelAnimationFrame(frame);frame=0;}
  const observer=new IntersectionObserver(entries=>{visible=Boolean(entries[0]?.isIntersecting);if(visible)start();else stop();},{threshold:.02});observer.observe(host);
  const visibility=()=>{if(document.hidden)stop();else start();};document.addEventListener('visibilitychange',visibility);
  function resize(){if(closed)return;const w=Math.max(100,host.clientWidth),h=Math.max(220,host.clientHeight);camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setSize(w,h);redraw();}
  const ro=new ResizeObserver(resize);ro.observe(host);start();
  return {resize,destroy(){closed=true;stop();observer.disconnect();ro.disconnect();document.removeEventListener('visibilitychange',visibility);controls.dispose();renderer.dispose();host.innerHTML='';}};
}
