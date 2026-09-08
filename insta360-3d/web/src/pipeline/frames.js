/**
 * Keyframe extraction from a 360 video.
 *
 * Decoding happens on the main thread because HTMLVideoElement is not
 * available in workers; each keyframe is rasterised straight to the working
 * resolution so nothing full-size is ever kept.
 */

export function loadVideo(video, src) {
  return new Promise((resolve, reject) => {
    const onReady = () => {
      cleanup();
      resolve({
        width: video.videoWidth,
        height: video.videoHeight,
        duration: video.duration,
      });
    };
    const onError = () => {
      cleanup();
      reject(new Error('Could not decode this video. Export an equirectangular MP4 (H.264) and try again.'));
    };
    const cleanup = () => {
      video.removeEventListener('loadedmetadata', onReady);
      video.removeEventListener('error', onError);
    };
    video.addEventListener('loadedmetadata', onReady);
    video.addEventListener('error', onError);
    video.src = src;
    video.load();
  });
}

function seek(video, time) {
  return new Promise((resolve, reject) => {
    const onSeeked = () => { cleanup(); resolve(); };
    const onError = () => { cleanup(); reject(new Error('Seeking failed while reading keyframes.')); };
    const cleanup = () => {
      video.removeEventListener('seeked', onSeeked);
      video.removeEventListener('error', onError);
    };
    video.addEventListener('seeked', onSeeked);
    video.addEventListener('error', onError);
    video.currentTime = Math.max(0, time);
  });
}

/**
 * @returns {Promise<{frames: ImageData[], times: number[], width: number, height: number}>}
 */
export async function extractKeyframes(video, {
  spacing = 0.35,
  maxFrames = 48,
  width = 768,
  startTime = 0,
  signal = null,
  onProgress = null,
} = {}) {
  const height = width >> 1;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });

  const duration = Number.isFinite(video.duration) ? video.duration : 0;
  const usable = Math.max(0, duration - startTime - 0.05);
  const count = Math.max(2, Math.min(maxFrames, Math.floor(usable / spacing) + 1));

  const frames = [];
  const times = [];
  for (let i = 0; i < count; i++) {
    if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
    const time = startTime + i * spacing;
    await seek(video, time);
    ctx.drawImage(video, 0, 0, width, height);
    frames.push(ctx.getImageData(0, 0, width, height));
    times.push(video.currentTime);
    onProgress?.(i + 1, count);
  }
  return { frames, times, width, height };
}

/** Luminance at a reduced resolution, for the motion estimator. */
export function grayFromImageData(image, targetWidth) {
  const targetHeight = targetWidth >> 1;
  const { width, height, data } = image;
  const out = new Float32Array(targetWidth * targetHeight);
  const fx = width / targetWidth;
  const fy = height / targetHeight;

  for (let y = 0; y < targetHeight; y++) {
    const y0 = Math.floor(y * fy);
    const y1 = Math.min(height, Math.max(y0 + 1, Math.floor((y + 1) * fy)));
    for (let x = 0; x < targetWidth; x++) {
      const x0 = Math.floor(x * fx);
      const x1 = Math.min(width, Math.max(x0 + 1, Math.floor((x + 1) * fx)));
      let sum = 0;
      let count = 0;
      for (let yy = y0; yy < y1; yy++) {
        for (let xx = x0; xx < x1; xx++) {
          const p = (yy * width + xx) * 4;
          sum += 0.299 * data[p] + 0.587 * data[p + 1] + 0.114 * data[p + 2];
          count++;
        }
      }
      out[y * targetWidth + x] = sum / (count * 255);
    }
  }
  return out;
}
