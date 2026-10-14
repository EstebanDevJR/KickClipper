// Puente con kick.com sin servidor intermedio.
//
// Una web de terceros no puede leer la API de Kick (CORS), así que se usa un
// marcador (bookmarklet): el usuario lo pulsa estando en la página del VOD, el
// código corre en kick.com con su propia sesión, lee los datos del VOD (URL
// HLS, canal, hora de inicio) y abre esta app pasándoselos en la URL. La
// pestaña de Kick queda como "puente": si el navegador bloquea por CORS la
// descarga directa del vídeo o hace falta el chat, la app le pide esas
// peticiones por postMessage. El puente solo atiende a la ventana que abrió y
// solo hace peticiones GET a kick.com y al host del vídeo.

const KICK_ORIGIN = "https://kick.com";

/** Código del marcador (se genera con la URL de esta app). */
export function bookmarklet(appUrl) {
  const code = `(()=>{const A=${JSON.stringify(appUrl)};const m=location.pathname.match(/\\/videos\\/([0-9a-f-]{36})/i);if(!m){alert('KickClipper: abre la página de un VOD de Kick (kick.com/canal/videos/…) y vuelve a pulsar el marcador.');return}fetch('/api/v1/video/'+m[1],{headers:{Accept:'application/json'}}).then(r=>r.json()).then(v=>{const l=v.livestream||{},c=l.channel||{};const d={src:v.source,title:l.session_title||'',channel:c.slug||'',channel_id:c.id||null,start:l.start_time||v.created_at||'',duration:(l.duration||0)/1e3,thumb:l.thumbnail||'',url:location.href};const H=new URL(v.source).hostname;const O=new URL(A).origin;const w=window.open(A+'#kick='+encodeURIComponent(JSON.stringify(d)),'_blank');if(!w){alert('KickClipper: permite las ventanas emergentes de kick.com');return}addEventListener('message',async e=>{if(e.source!==w||e.origin!==O||!e.data||e.data.kc!=='fetch')return;const q=e.data;try{const u=new URL(q.url,location.href);if(!(u.hostname===H||/(^|\\.)kick\\.com$/.test(u.hostname)))throw new Error('host no permitido');const r=await fetch(u,{headers:q.headers||{}});const b=await r.arrayBuffer();w.postMessage({kc:'fetch-res',id:q.id,status:r.status,headers:[...r.headers],body:b},O,[b])}catch(err){w.postMessage({kc:'fetch-res',id:q.id,error:String(err)},O)}})}).catch(e=>alert('KickClipper: no se pudo leer el VOD ('+e+')'))})()`;
  return `javascript:${encodeURIComponent(code)}`;
}

/** Datos del VOD que dejó el marcador en el hash (#kick=…), o null. */
export function readKickPayload(loc = location) {
  const m = loc.hash.match(/#kick=(.+)$/);
  if (!m) return null;
  try {
    const data = JSON.parse(decodeURIComponent(m[1]));
    if (!data.src) return null;
    return data;
  } catch {
    return null;
  }
}

export class KickBridge {
  constructor(win = window) {
    this.win = win;
    this.seq = 0;
    this.pending = new Map();
    this.direct = new Map(); // host → ¿funciona fetch directo?
    win.addEventListener("message", (e) => {
      if (e.origin !== KICK_ORIGIN || !e.data || e.data.kc !== "fetch-res") return;
      const p = this.pending.get(e.data.id);
      if (!p) return;
      this.pending.delete(e.data.id);
      if (e.data.error) p.reject(new Error(e.data.error));
      else p.resolve(new Response(e.data.body, { status: e.data.status || 200, headers: e.data.headers || [] }));
    });
  }

  get available() {
    try { return !!this.win.opener && !this.win.opener.closed; } catch { return false; }
  }

  /** Petición GET hecha por la pestaña de Kick. */
  viaKick(url, headers = {}, timeoutMs = 60000) {
    if (!this.available) return Promise.reject(new Error("La pestaña de Kick está cerrada: vuelve a pulsar el marcador"));
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error("El puente con Kick no respondió")); }, timeoutMs);
      this.pending.set(id, {
        resolve: (r) => { clearTimeout(timer); resolve(r); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this.win.opener.postMessage({ kc: "fetch", id, url: String(url), headers }, KICK_ORIGIN);
    });
  }

  /** fetch compatible (para mediabunny): directo si CORS lo permite, si no por el puente. */
  fetchFn = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const host = new URL(url, location.href).host;
    const headers = plainHeaders(init.headers || (typeof input === "object" ? input.headers : null));
    if (this.direct.get(host) !== false) {
      try {
        const res = await fetch(url, { ...init, headers });
        this.direct.set(host, true);
        return res;
      } catch (err) {
        if (!this.available || init.signal?.aborted) throw err;
        this.direct.set(host, false); // CORS: a partir de ahora, por el puente
      }
    }
    return this.viaKick(url, headers);
  };

  /** JSON de la API de Kick (solo posible a través del puente). */
  async kickJson(path) {
    const res = await this.viaKick(new URL(path, KICK_ORIGIN).href, { Accept: "application/json" }, 15000);
    if (!res.ok) throw new Error(`Kick respondió ${res.status}`);
    return res.json();
  }
}

function plainHeaders(h) {
  if (!h) return {};
  if (typeof Headers !== "undefined" && h instanceof Headers) return Object.fromEntries(h.entries());
  if (Array.isArray(h)) return Object.fromEntries(h);
  return { ...h };
}
