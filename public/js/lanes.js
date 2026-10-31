/* Illustrative sea-lane topology. Node coordinates always come from PortWatch
   chokepoint records; connecting geometry is schematic routing, not AIS tracks. */
export const SEA_LANE_EDGES = [
  ['chokepoint8','chokepoint1',[[37,10],[35,25]]],
  ['chokepoint1','chokepoint4',[[25,35],[18,40]]],
  ['chokepoint4','chokepoint6',[[12,51],[17,62],[23,61]]],
  ['chokepoint6','chokepoint5',[[17,69],[7,83],[5,96]]],
  ['chokepoint7','chokepoint4',[[-10,44],[4,49]]],
  ['chokepoint8','chokepoint7',[[20,-16],[-5,-12],[-25,12]]],
  ['chokepoint8','chokepoint9',[[44,-8]]],
  ['chokepoint9','chokepoint10',[[56,6]]],
  ['chokepoint3','chokepoint28',[[43,33]]],
  ['chokepoint3','chokepoint1',[[39,25],[35,27]]],
  ['chokepoint5','chokepoint11',[[4,106],[13,112],[20,117]]],
  ['chokepoint11','chokepoint12',[[28,124]]],
  ['chokepoint12','chokepoint13',[[38,134]]],
  ['chokepoint13','chokepoint26',[[48,160],[57,178]]],
  ['chokepoint11','chokepoint14'],
  ['chokepoint14','chokepoint27'],
  ['chokepoint27','chokepoint25'],
  ['chokepoint25','chokepoint20'],
  ['chokepoint20','chokepoint15'],
  ['chokepoint15','chokepoint16'],
  ['chokepoint16','chokepoint18'],
  ['chokepoint5','chokepoint19'],
  ['chokepoint19','chokepoint20'],
  ['chokepoint20','chokepoint18'],
  ['chokepoint11','chokepoint17'],
  ['chokepoint17','chokepoint12'],
  ['chokepoint7','chokepoint21',[[-40,38],[-55,0]]],
  ['chokepoint21','chokepoint2',[[-48,-79],[-10,-80]]],
  ['chokepoint2','chokepoint22',[[15,-83]]],
  ['chokepoint22','chokepoint23'],
  ['chokepoint23','chokepoint24'],
  ['chokepoint24','chokepoint8',[[25,-50],[34,-20]]],
];
export const REGION_ROUTES = {
  Taiwan:['chokepoint11','chokepoint14'], China:['chokepoint11','chokepoint5'], 'Hong Kong':['chokepoint11'],
  Korea:['chokepoint12','chokepoint11'], Japan:['chokepoint12','chokepoint14'], Malaysia:['chokepoint5'],
  Vietnam:['chokepoint5'], India:['chokepoint5','chokepoint4'], 'Middle East':['chokepoint6','chokepoint4'],
  Israel:['chokepoint1','chokepoint4'], Europe:['chokepoint1','chokepoint4'], Germany:['chokepoint1','chokepoint4'],
  Netherlands:['chokepoint1','chokepoint4'], Mexico:['chokepoint2'],
};
export function relevantRouteWeights(exposure){
  const weights=new Map();
  for(const g of exposure?.geographies||[])for(const id of REGION_ROUTES[g.region]||[])weights.set(id,(weights.get(id)||0)+Number(g.mentions||0));
  const sum=[...weights.values()].reduce((a,b)=>a+b,0);
  return new Map([...weights].map(([id,v])=>[id,sum?v/sum:0]));
}
const rad=Math.PI/180,deg=180/Math.PI;
const vector=([lat,lon])=>[Math.cos(lat*rad)*Math.cos(lon*rad),Math.sin(lat*rad),Math.cos(lat*rad)*Math.sin(lon*rad)];
export function greatCircle(a,b,steps=16){
  const v=vector(a),w=vector(b),dot=Math.max(-1,Math.min(1,v[0]*w[0]+v[1]*w[1]+v[2]*w[2])),omega=Math.acos(dot),sin=Math.sin(omega);
  const out=[];
  for(let i=0;i<=steps;i++){
    const t=i/steps,u=sin<1e-6?1-t:Math.sin((1-t)*omega)/sin,z=sin<1e-6?t:Math.sin(t*omega)/sin;
    const x=v[0]*u+w[0]*z,y=v[1]*u+w[1]*z,q=v[2]*u+w[2]*z;
    out.push([Math.atan2(y,Math.hypot(x,q))*deg,Math.atan2(q,x)*deg]);
  }
  return out;
}
export function laneGraph(chokepoints){
  const byId=new Map((chokepoints||[]).filter(x=>Number.isFinite(x.lat)&&Number.isFinite(x.lon)).map(x=>[x.id,x]));
  return SEA_LANE_EDGES.filter(([a,b])=>byId.has(a)&&byId.has(b)).map(([a,b,waypoints=[]])=>({a,b,points:[[byId.get(a).lat,byId.get(a).lon],...waypoints,[byId.get(b).lat,byId.get(b).lon]]}));
}
export function localCorridor(point,graph,byId){
  const neighbors=graph.flatMap(e=>e.a===point.id?[e.b]:e.b===point.id?[e.a]:[]).map(id=>byId.get(id)).filter(Boolean);
  const c=[point.lat,point.lon];
  if(!neighbors.length)return [c,c];
  const toward=n=>{const p=[n.lat,n.lon],distance=Math.acos(Math.max(-1,Math.min(1,vector(c).reduce((a,v,i)=>a+v*vector(p)[i],0))))*deg;return greatCircle(c,p,20)[Math.max(1,Math.min(19,Math.round(20*Math.min(1,7/Math.max(1,distance)))))]};
  const end=toward(neighbors[0]);
  const start=neighbors[1]?toward(neighbors[1]):[Math.max(-85,2*c[0]-end[0]),((2*c[1]-end[1]+540)%360)-180];
  return [start,c,end];
}
