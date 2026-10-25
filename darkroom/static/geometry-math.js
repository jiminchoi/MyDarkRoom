// Row-major homographies shared by preview and manual auto-crop. Matches geometry.py.
export const identity=()=>[1,0,0,0,1,0,0,0,1];
export function multiply(a,b){return a.map((_,i)=>{const r=Math.floor(i/3),c=i%3;return a[r*3]*b[c]+a[r*3+1]*b[c+3]+a[r*3+2]*b[c+6];});}
export function inverse(a){
 const [a0,b,c,d,e,f,g,h,i]=a,co=[e*i-f*h,c*h-b*i,b*f-c*e,f*g-d*i,a0*i-c*g,c*d-a0*f,d*h-e*g,b*g-a0*h,a0*e-b*d],det=a0*co[0]+b*co[3]+c*co[6];
 if(!Number.isFinite(det)||Math.abs(det)<1e-12)throw new Error("안전하게 변환할 수 없는 원근입니다.");return co.map(v=>v/det);
}
export function transform(a,[x,y]){const z=a[6]*x+a[7]*y+a[8];return [(a[0]*x+a[1]*y+a[2])/z,(a[3]*x+a[4]*y+a[5])/z,z];}
export function forwardMatrix([w,h],g){
 const s=Math.min(w,h),n=[1/s,0,-w/(2*s),0,1/s,-h/(2*s),0,0,1],angle=g.rotation*Math.PI/180,c=Math.cos(angle),t=Math.sin(angle);
 return multiply(inverse(n),multiply([1,0,0,0,1,0,g.horizontal,g.vertical,1],multiply([c,-t,0,t,c,0,0,0,1],multiply(g.upright,n))));
}
export function autoCrop(size,g){
 const [w,h]=size,m=forwardMatrix(size,g);
 if(m.every((v,i)=>Math.abs(v-identity()[i])<1e-9))return [0,0,1,1];
 const quad=[[2,2],[w-2,2],[w-2,h-2],[2,h-2]].map(p=>transform(m,p));
 if(quad.some(p=>p[2]<=0||p.some(v=>!Number.isFinite(v))))throw new Error("안전하게 자동 크롭할 수 없는 원근입니다.");
 const constraints=[];
 for(const polygon of [quad.map(p=>[p[0]/w,p[1]/h]),[[0,0],[1,0],[1,1],[0,1]]])for(let i=0;i<4;i++){
   const a=polygon[i],b=polygon[(i+1)%4],nx=b[1]-a[1],ny=a[0]-b[0];constraints.push([nx,ny,(Math.abs(nx)+Math.abs(ny))/2,nx*a[0]+ny*a[1]]);
 }
 let best=null;
 for(let i=0;i<6;i++)for(let j=i+1;j<7;j++)for(let k=j+1;k<8;k++){
   const rows=[constraints[i],constraints[j],constraints[k]];let inv;
   try{inv=inverse(rows.flatMap(r=>r.slice(0,3)));}catch{continue;}
   const b=rows.map(r=>r[3]),p=[0,1,2].map(r=>inv[r*3]*b[0]+inv[r*3+1]*b[1]+inv[r*3+2]*b[2]);
   if(p[2]>0&&constraints.every(r=>r[0]*p[0]+r[1]*p[1]+r[2]*p[2]<=r[3]+1e-8)){
     if(!best||p[2]>best[2]+1e-8||(Math.abs(p[2]-best[2])<=1e-8&&Math.hypot(p[0]-.5,p[1]-.5)<Math.hypot(best[0]-.5,best[1]-.5)))best=p;
   }
 }
 if(!best||best[2]<.01)throw new Error("자동 크롭 영역이 너무 작습니다. 각도·원근을 줄여주세요.");
 const [x,y,s]=best;return [Math.max(0,x-s/2),Math.max(0,y-s/2),s,s];
}
// Python/Pillow crop boundaries use ties-to-even rounding.
const roundPixel=v=>{const i=Math.floor(v);return v-i===.5?i+(i%2):Math.round(v);};
export function gpuGeometry(size,g,editing=false){
 const [w,h]=size,crop=editing?[0,0,1,1]:g.crop;
 const x=roundPixel(crop[0]*w),y=roundPixel(crop[1]*h),right=Math.min(w,Math.max(x+1,roundPixel((crop[0]+crop[2])*w))),bottom=Math.min(h,Math.max(y+1,roundPixel((crop[1]+crop[3])*h)));
 return {inverse:inverse(forwardMatrix(size,g)),cropOrigin:[x,y],size:[right-x,bottom-y]};
}
