// Apertura del vídeo fuente: archivo local, URL directa (MP4/WebM) o HLS
// (los VODs de Kick son HLS). Con HLS se elige la variante más ligera para
// analizar (solo audio si existe, o la de menor bitrate) y la de mayor calidad
// para renderizar, así el análisis de un VOD largo descarga lo mínimo.

import * as MB from "mediabunny";

export async function openSource({ file = null, url = null, fetchFn = null, name = "" } = {}) {
  const src = file
    ? new MB.BlobSource(file)
    : new MB.UrlSource(url, { ...(fetchFn ? { fetchFn } : {}), parallelism: 4, maxCacheSize: 96 * 2 ** 20 });
  const input = new MB.Input({
    source: src,
    formats: MB.ALL_FORMATS,
    formatOptions: { hls: { offsetTimestampsByDateTime: false } },
  });
  if (!(await input.canRead())) throw new Error("Formato de vídeo no compatible");
  const format = await input.getFormat();
  const isHls = format === MB.HLS || /hls/i.test(format?.name || "");

  const videoTrack = await input.getPrimaryVideoTrack();
  const audioTrack = await input.getPrimaryAudioTrack();
  if (!videoTrack) throw new Error("El archivo no tiene vídeo");

  let analysisAudio = audioTrack;
  let analysisVideo = videoTrack;
  if (isHls) {
    analysisAudio = await cheapestAudio(input) || audioTrack;
    analysisVideo = await videoNear(input, 480) || videoTrack;
  }

  const [width, height] = [await videoTrack.getDisplayWidth(), await videoTrack.getDisplayHeight()];
  let duration = await input.getDurationFromMetadata([videoTrack]).catch(() => null);
  if (!duration) duration = await input.computeDuration([videoTrack]);
  let t0 = 0;
  try { t0 = await videoTrack.getFirstTimestamp(); } catch { t0 = 0; }
  if (!Number.isFinite(t0) || t0 < 0) t0 = 0;

  return {
    input, isHls, videoTrack, audioTrack, analysisAudio, analysisVideo,
    width, height, duration: duration - t0 > 0 ? duration - t0 : duration, t0,
    name: name || file?.name || url?.split("/").pop() || "video",
    canDecode: await videoTrack.canDecode(),
    canDecodeAudio: audioTrack ? await audioTrack.canDecode() : false,
    dispose: () => input.dispose?.(),
  };
}

async function bitrate(track) {
  return (await track.getBitrate().catch(() => null)) || (await track.getAverageBitrate().catch(() => null)) || 0;
}

/** Pista de audio cuyo segmento HLS pesa menos (audio solo > variante más baja). */
async function cheapestAudio(input) {
  const audios = await input.getAudioTracks();
  let best = null, bestCost = Infinity;
  for (const a of audios) {
    if (!(await a.canDecode().catch(() => false))) continue;
    const videos = await a.getPairableVideoTracks().catch(() => []);
    let cost = await bitrate(a);
    // Si el audio va muxado con vídeo (TS de Kick), se descarga también ese vídeo
    if (videos.length) {
      const vb = await Promise.all(videos.map(bitrate));
      cost += Math.min(...vb.map((b) => b || 5e6));
    }
    if (cost < bestCost) { best = a; bestCost = cost; }
  }
  return best;
}

/** Variante de vídeo con altura más cercana a `target` (para análisis visual barato). */
async function videoNear(input, target) {
  const videos = await input.getVideoTracks();
  let best = null, bestD = Infinity;
  for (const v of videos) {
    if (!(await v.canDecode().catch(() => false))) continue;
    const h = await v.getDisplayHeight();
    const d = Math.abs(h - target) + (h < 300 ? 1000 : 0);
    if (d < bestD) { best = v; bestD = d; }
  }
  return best;
}
