// Local shape-preserving Hermite interpolation, shared numerics with curves.py.
export function curvePoints(settings) {
  return settings.curve_points ?? [[0,(settings.curve_black??0)/100],[.25,(settings.curve_shadow??25)/100],[.5,(settings.curve_mid??50)/100],[.75,(settings.curve_light??75)/100],[1,(settings.curve_white??100)/100]];
}
export function validCurve(points) {
  return points===null || (Array.isArray(points)&&points.length>=2&&points.length<=32&&points[0]?.[0]===0&&points.at(-1)?.[0]===1&&points.every((p,i)=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite)&&p[1]>=0&&p[1]<=1&&(!i||p[0]-points[i-1][0]>=.002)));
}
export function curveLut(settings) {
  const p=curvePoints(settings),n=p.length,h=[],d=[],m=new Array(n).fill(0),lut=new Float32Array(256);
  for(let i=0;i<n-1;i++){h.push(p[i+1][0]-p[i][0]);d.push((p[i+1][1]-p[i][1])/h[i]);}
  m[0]=d[0];m[n-1]=d[n-2];
  for(let i=1;i<n-1;i++)if(d[i-1]*d[i]>0){const a=2*h[i]+h[i-1],b=h[i]+2*h[i-1];m[i]=(a+b)/(a/d[i-1]+b/d[i]);}
  let i=0;
  for(let j=0;j<256;j++){
    const x=j/255;while(i<n-2&&x>p[i+1][0])i++;
    const t=(x-p[i][0])/h[i];
    lut[j]=settings.curve_points==null ? p[i][1]*(1-t)+p[i+1][1]*t : Math.max(0,Math.min(1,(2*t**3-3*t*t+1)*p[i][1]+(t**3-2*t*t+t)*h[i]*m[i]+(-2*t**3+3*t*t)*p[i+1][1]+(t**3-t*t)*h[i]*m[i+1]));
  }
  return lut;
}
