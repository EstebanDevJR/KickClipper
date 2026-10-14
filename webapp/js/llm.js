// LLM local opcional para puntuar candidatas y escribir títulos/ganchos.
//
// - WebLLM (WebGPU): descarga un modelo pequeño una vez (~1 GB) y lo ejecuta en
//   la GPU del usuario.
// - IA integrada de Chrome (Prompt API, Gemini Nano) si el navegador la ofrece.
// Sin LLM, la app funciona igual con señales automáticas.

import { CATEGORIES, LIMITS, numberedLines, signalsText } from "./highlights.js";

const WEBLLM_URL = "https://cdn.jsdelivr.net/npm/@mlc-ai/web-llm@0.2.85/+esm";
export const WEBLLM_MODELS = {
  "Qwen3-1.7B-q4f16_1-MLC": "Qwen3 1.7B (~1,1 GB, recomendado)",
  "Qwen3-4B-q4f16_1-MLC": "Qwen3 4B (~2,5 GB, mejor criterio, GPU potente)",
  "Qwen2.5-1.5B-Instruct-q4f16_1-MLC": "Qwen2.5 1.5B (~1 GB)",
};

const GUIDE = `Sabes cómo funcionan los clips de streamers en TikTok, YouTube Shorts y Reels:
- Los primeros 3 segundos deciden si la gente se queda: el clip debe abrir con la frase o reacción más fuerte, o con una tensión clara. Nada de saludos ni relleno al inicio.
- Debe entenderse sin haber visto el stream: contexto mínimo y una sola idea.
- Necesita un remate (la jugada, la frase, el grito, la reacción) y terminar justo después.
- Funcionan: humor, reacciones exageradas, frases citables, drama, momentos épicos o vergonzosos de juego, historias cortas con giro, opiniones polémicas.
- No funcionan: relleno, leer el chat sin gracia, conversaciones a medias, chistes internos sin contexto.`;

const SCHEMA = {
  type: "object",
  properties: {
    score: { type: "number" }, hook: { type: "number" }, standalone: { type: "number" }, payoff: { type: "number" },
    start_line: { type: "integer" }, end_line: { type: "integer" },
    title: { type: "string" }, hook_text: { type: "string" }, description: { type: "string" },
    hashtags: { type: "array", items: { type: "string" } }, keywords: { type: "array", items: { type: "string" } },
    category: { type: "string" }, reason: { type: "string" },
  },
  required: ["score", "hook", "standalone", "payoff", "title", "hook_text", "description", "hashtags", "reason"],
};

export function buildPrompt(w, ctx, channel, limits = LIMITS) {
  const { lines, index } = numberedLines(w, ctx);
  w.lineIndex = index;
  return `Eres editor experto de clips virales de streamers.
${GUIDE}

Canal: ${channel || "?"}
Señales automáticas del fragmento: ${signalsText(w)}

Transcripción con número de línea y segundo relativo. Las líneas [ANTES] y [DESPUÉS] están fuera del fragmento propuesto; úsalas solo si hacen falta para el contexto o el remate:
${lines}

Elige en qué línea debe EMPEZAR el clip (start_line) para tener el mejor gancho sin perder el contexto imprescindible, y en cuál debe TERMINAR (end_line) justo tras el remate o la reacción. La duración resultante debe quedar entre ${limits.min} y ${limits.max} segundos.

Título, texto gancho, descripción y hashtags en el MISMO idioma de la transcripción.

Responde SOLO con JSON:
{"score": <0-10 potencial viral>, "hook": <0-10 fuerza de los 3 primeros segundos>, "standalone": <0-10 se entiende sin contexto>, "payoff": <0-10 calidad del remate>, "start_line": <int>, "end_line": <int>, "title": "<gancho, máx 60 caracteres>", "hook_text": "<3 a 6 palabras para la pantalla al inicio, distinto del título>", "description": "<1-2 frases con gancho, sin hashtags>", "hashtags": ["<3-5 sin #>"], "keywords": ["<hasta 5 palabras textuales de la transcripción para resaltar>"], "category": "<${CATEGORIES.join("|")}>", "reason": "<1 frase>"}`;
}

function parseJson(text) {
  if (!text) return null;
  const m = String(text).match(/\{[\s\S]*\}/);
  if (!m) return null;
  try { return JSON.parse(m[0]); } catch { return null; }
}

/** ¿Qué motores hay disponibles en este navegador? */
export async function llmOptions() {
  const out = { webgpu: false, chrome: false };
  try { out.webgpu = !!(navigator.gpu && await navigator.gpu.requestAdapter()); } catch { /* sin WebGPU */ }
  try {
    if (globalThis.LanguageModel?.availability) {
      const a = await globalThis.LanguageModel.availability({ expectedInputs: [{ type: "text", languages: ["es", "en"] }] });
      out.chrome = a && a !== "unavailable";
    }
  } catch { /* sin Prompt API */ }
  return out;
}

/** Crea el evaluador. kind: "webllm" | "chrome". */
export async function createLLM(kind, { model = "Qwen3-1.7B-q4f16_1-MLC", onProgress, limits = LIMITS } = {}) {
  if (kind === "chrome") {
    const base = await globalThis.LanguageModel.create({
      initialPrompts: [{ role: "system", content: "Respondes solo con JSON válido." }],
      expectedInputs: [{ type: "text", languages: ["es", "en"] }],
      expectedOutputs: [{ type: "text", languages: ["es"] }],
      monitor(m) { m.addEventListener("downloadprogress", (e) => onProgress?.(e.loaded, "Descargando IA de Chrome")); },
    });
    return {
      name: "IA de Chrome",
      async score(w, ctx, channel) {
        const s = await base.clone();
        try { return parseJson(await s.prompt(buildPrompt(w, ctx, channel, limits), { responseConstraint: SCHEMA })); }
        finally { s.destroy?.(); }
      },
    };
  }
  const webllm = await import(WEBLLM_URL);
  const engine = await webllm.CreateMLCEngine(model, {
    initProgressCallback: (p) => onProgress?.(p.progress ?? 0, p.text || "Cargando modelo"),
  });
  return {
    name: WEBLLM_MODELS[model] || model,
    async score(w, ctx, channel) {
      const reply = await engine.chat.completions.create({
        messages: [
          { role: "system", content: "Respondes solo con JSON válido, sin texto adicional." },
          { role: "user", content: buildPrompt(w, ctx, channel, limits) },
        ],
        temperature: 0.3,
        max_tokens: 450,
        response_format: { type: "json_object" },
        extra_body: { enable_thinking: false },
      });
      return parseJson(reply.choices?.[0]?.message?.content);
    },
  };
}
