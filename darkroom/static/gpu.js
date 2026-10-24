// Linear 16-bit RGB textures stay on the GPU throughout slider/compare changes.
const vertex = `#version 300 es
in vec2 position;
void main() { gl_Position = vec4(position, 0., 1.); }
`;
const fragment = `#version 300 es
precision highp float;
precision highp usampler2D;
uniform usampler2D source;
uniform usampler2D detail;
uniform vec2 outputSize, fullSize, offset, origin, detailOrigin;
uniform float scale, splitPosition;
uniform bool hasDetail, before, split, histogram;
uniform vec4 light;
uniform vec3 color;
out vec4 result;
vec3 sample16(usampler2D tex, vec2 uv) {
  ivec2 size = textureSize(tex, 0);
  vec2 p = uv * vec2(size) - .5;
  ivec2 a = ivec2(floor(p)); vec2 f = fract(p);
  vec3 c00 = vec3(texelFetch(tex, clamp(a, ivec2(0), size-1), 0).rgb);
  vec3 c10 = vec3(texelFetch(tex, clamp(a+ivec2(1,0), ivec2(0), size-1), 0).rgb);
  vec3 c01 = vec3(texelFetch(tex, clamp(a+ivec2(0,1), ivec2(0), size-1), 0).rgb);
  vec3 c11 = vec3(texelFetch(tex, clamp(a+ivec2(1,1), ivec2(0), size-1), 0).rgb);
  return mix(mix(c00,c10,f.x),mix(c01,c11,f.x),f.y)/65535.;
}
void main() {
  vec2 screen = vec2(gl_FragCoord.x, outputSize.y-gl_FragCoord.y);
  vec2 p = histogram ? screen/outputSize*fullSize : (screen-offset)/scale+origin;
  if (any(lessThan(p,vec2(0))) || any(greaterThanEqual(p,fullSize))) {
    result=vec4(vec3(16.,18.,18.)/255.,1.); return;
  }
  vec3 x = sample16(source, p/fullSize);
  vec2 dp = p-detailOrigin;
  if (hasDetail && !histogram && all(greaterThanEqual(dp,vec2(0))) && all(lessThan(dp,vec2(textureSize(detail,0))))) {
    x = sample16(detail, dp/vec2(textureSize(detail,0)));
  }
  bool original = !histogram && (before || (split && screen.x < outputSize.x*splitPosition));
  vec4 l = original ? vec4(0) : light;
  vec3 c = original ? vec3(0) : color;
  float warmth = c.x/100., tint = c.y/100.;
  x *= exp2(l.x) * exp2(vec3(.45*warmth+.15*tint,-.3*tint,-.45*warmth+.15*tint));
  float lum = dot(x,vec3(.2126,.7152,.0722));
  float shadow = pow(1.-clamp(lum/.5,0.,1.),2.);
  float highlight = pow(clamp((lum-.2)/.8,0.,1.),2.);
  x *= exp2(l.w/100.*shadow+l.z/100.*highlight);
  x = clamp(x,0.,1.);
  x = mix(1.055*pow(x,vec3(1./2.4))-.055,12.92*x,lessThanEqual(x,vec3(.0031308)));
  x = (x-.5)*exp2(l.y/100.)+.5;
  lum = dot(x,vec3(.2126,.7152,.0722));
  x = vec3(lum)+(x-vec3(lum))*(1.+c.z/100.);
  result = vec4(clamp(x,0.,1.),1.);
}
`;

export class GpuRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = this.gl = canvas.getContext("webgl2", { alpha: false, antialias: false, preserveDrawingBuffer: true });
    if (!gl) throw new Error("WebGL2 unavailable");
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    this.renderer = debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : "unknown";
    this.software = /swiftshader|llvmpipe|softpipe|software/i.test(this.renderer);
    this.hardwareKnown = Boolean(debug) && !this.software;
    const compile = (type, source) => {
      const shader = gl.createShader(type); gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) { const log = gl.getShaderInfoLog(shader); gl.deleteShader(shader); throw new Error(log); }
      return shader;
    };
    this.program = gl.createProgram();
    const shaders = [compile(gl.VERTEX_SHADER, vertex), compile(gl.FRAGMENT_SHADER, fragment)];
    shaders.forEach((s) => gl.attachShader(this.program, s)); gl.linkProgram(this.program);
    shaders.forEach((s) => gl.deleteShader(s));
    if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(this.program));
    gl.useProgram(this.program);
    const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, -1,1, 1,-1, 1,1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(this.program, "position");
    gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    this.locations = {};
    for (const name of ["source","detail","outputSize","fullSize","offset","origin","detailOrigin","scale","splitPosition","hasDetail","before","split","histogram","light","color"]) this.locations[name] = gl.getUniformLocation(this.program, name);
    gl.uniform1i(this.locations.source, 0); gl.uniform1i(this.locations.detail, 1);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    this.detailTexture = null; this.texture = null; this.target = null;
  }
  upload(data) {
    const gl = this.gl;
    if (data.width > gl.getParameter(gl.MAX_TEXTURE_SIZE) || data.height > gl.getParameter(gl.MAX_TEXTURE_SIZE)) throw new Error("GPU texture limit");
    const texture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB16UI, data.width, data.height, 0, gl.RGB_INTEGER, gl.UNSIGNED_SHORT, data.pixels);
    if (gl.getError() !== gl.NO_ERROR) { gl.deleteTexture(texture); throw new Error("GPU texture upload failed"); }
    return texture;
  }
  load(data, photo) {
    const next = this.upload(data);
    this.gl.deleteTexture(this.texture); this.texture = next;
    this.photo = photo; this.width = data.width; this.height = data.height;
    this.clearDetail();
    if (this.target) { this.gl.deleteFramebuffer(this.target.framebuffer); this.gl.deleteTexture(this.target.texture); this.target = null; }
  }
  clearDetail() { this.gl.deleteTexture(this.detailTexture); this.detailTexture = null; }
  loadDetail(data) {
    const texture = this.upload(data);
    this.clearDetail(); this.detailTexture = texture; this.detailOrigin = [data.x, data.y];
  }
  draw(settings, view, { before = false, split = false, splitPosition = .5, histogram = false } = {}) {
    if (!this.texture) return;
    const gl = this.gl, u = this.locations;
    const width = histogram ? this.width : this.canvas.width, height = histogram ? this.height : this.canvas.height;
    gl.useProgram(this.program); gl.viewport(0,0,width,height);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D,this.texture);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D,this.detailTexture || this.texture);
    gl.uniform2f(u.outputSize,width,height); gl.uniform2f(u.fullSize,this.photo.width,this.photo.height);
    gl.uniform2fv(u.offset,view.offset); gl.uniform2fv(u.origin,view.origin);
    gl.uniform2fv(u.detailOrigin,this.detailOrigin || [0,0]); gl.uniform1f(u.scale,view.scale);
    gl.uniform1i(u.hasDetail,Boolean(this.detailTexture)); gl.uniform1i(u.before,before); gl.uniform1i(u.split,split);
    gl.uniform1i(u.histogram,histogram); gl.uniform1f(u.splitPosition,splitPosition);
    gl.uniform4f(u.light,settings.exposure,settings.contrast,settings.highlights,settings.shadows);
    gl.uniform3f(u.color,settings.temperature,settings.tint,settings.saturation);
    gl.drawArrays(gl.TRIANGLES,0,6);
  }
  histogram(settings, view) {
    const gl = this.gl;
    if (!this.target) {
      const texture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D,texture);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);
      gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA8,this.width,this.height,0,gl.RGBA,gl.UNSIGNED_BYTE,null);
      const framebuffer = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER,framebuffer);
      gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,texture,0);
      if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
        gl.bindFramebuffer(gl.FRAMEBUFFER,null); gl.deleteFramebuffer(framebuffer); gl.deleteTexture(texture);
        throw new Error("Histogram framebuffer unavailable");
      }
      this.target = { texture, framebuffer };
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER,this.target.framebuffer);
    try {
      this.draw(settings,view,{histogram:true});
      const pixels = new Uint8Array(this.width*this.height*4);
      gl.readPixels(0,0,this.width,this.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
      if (gl.getError() !== gl.NO_ERROR) throw new Error("Histogram GPU readback failed");
      return pixels;
    } finally { gl.bindFramebuffer(gl.FRAMEBUFFER,null); }
  }
}
