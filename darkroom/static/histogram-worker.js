"use strict";
self.onmessage = ({data}) => {
  const pixels = new Uint8Array(data.pixels), bins = new Uint32Array(768);
  for (let i=0; i<pixels.length; i+=4) {
    bins[pixels[i]]++; bins[256+pixels[i+1]]++; bins[512+pixels[i+2]]++;
  }
  self.postMessage({revision:data.revision, bins, count:pixels.length/4}, [bins.buffer]);
};
