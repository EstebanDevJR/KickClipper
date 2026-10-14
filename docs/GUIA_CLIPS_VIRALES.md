# Guía: cómo se hacen los clips de streamers que funcionan

Resumen de lo que se sabe (y de lo que solo son reglas prácticas) sobre cómo
convertir un stream largo en clips con potencial viral, y de cómo lo aplica
KickClipper. Investigación hecha en octubre de 2026.

> **Sobre las fuentes.** La mayoría de lo publicado sobre clips virales viene de
> blogs de empresas que venden herramientas de clipping. Sus cifras (porcentajes
> de retención, "la regla de los 2 segundos", etc.) son **reglas prácticas, no
> estándares**. Donde hay fuentes primarias (Google, papers, la propia
> plataforma) se indica. Tus propias analíticas valen más que cualquier cifra
> publicada: prueba variantes y compara curvas de retención.

---

## 1. Lo que piden las plataformas

| | TikTok | YouTube Shorts | Instagram Reels | YouTube (horizontal) |
|---|---|---|---|---|
| Formato | 9:16, 1080×1920 | 9:16 (o cuadrado), 1080×1920 | 9:16, 1080×1920 | 16:9, 1920×1080 |
| Duración máxima | 10 min (subida) | **3 min** (vídeos verticales/cuadrados subidos desde el 15-oct-2024) | 3 min | sin límite práctico |
| Duración que mejor retiene | 15–45 s (regla práctica) | 15–45 s; < 60 s para la mayoría de contenidos | similar a TikTok | 5–15 min |
| Monetización | Creator Rewards **solo paga vídeos de más de 1 min** | fondo de Shorts / reparto de anuncios | — | YPP |
| fps | 30 o 60 | 30 o 60 | 30 | 30 o 60 |
| Bitrate orientativo | 8–12 Mbps @30 fps, 12–16 @60 | 8 Mbps @30, 12 @60 (objetivos SDR de YouTube) | igual | 8–12 Mbps |
| Audio | AAC 48 kHz estéreo | AAC 48 kHz; YouTube **solo baja** el volumen de lo que pase de −14 LUFS | igual | igual |

Consecuencias prácticas:

- **Un mismo vertical sirve para las tres plataformas.** Lo que cambia es la
  duración ideal, la UI que tapa partes del vídeo y la monetización.
- **Si apuntas a ingresos de TikTok, haz clips de 61–90 s**; si apuntas a
  alcance puro, 20–45 s. KickClipper permite ajustar el rango
  (`KC_MIN_CLIP_SECONDS`, `KC_MAX_CLIP_SECONDS`, hasta 180 s).
- Un vídeo horizontal **nunca** cuenta como Short aunque dure 30 s.

### Zonas seguras (safe zones) en 9:16

La UI de la app tapa la parte inferior (nombre del canal, descripción, sonido)
y la columna derecha (likes, comentarios, compartir). Las únicas cifras de
fuente primaria son las de Google para anuncios verticales en Shorts:

- **arriba 288 px, abajo 672 px, izquierda 48 px, derecha 192 px** (sobre 1080×1920).

Para publicar el mismo archivo en todas partes, mantén el texto importante en
el rectángulo central aproximado de **x 48–888, y 288–1248**. KickClipper
coloca los subtítulos y el texto gancho dentro de esa zona.

## 2. Anatomía de un clip que retiene

Todas las fuentes coinciden en lo mismo: **las plataformas reparten primero el
vídeo a un público pequeño de prueba y amplían el alcance según cuánta gente
lo ve hasta el final**. Los primeros segundos deciden casi todo.

```
0–3 s    GANCHO        la frase/reacción más fuerte, o una pregunta/tensión
3–10 s   CONTEXTO      lo mínimo para que alguien que no conoce al streamer entienda
10–40 s  ESCALADA      la situación avanza, sin relleno ni silencios
         PAYOFF        el remate: la jugada, la frase, el grito
+1–4 s   REACCIÓN      la cara/voz del streamer reaccionando (y corte)
```

Técnicas concretas:

1. **Corta la intro.** Saludos, "bueno chat", "a ver…" fuera. Varios clippers
   recortan por defecto los primeros 5–7 s de cada candidato; si no hay
   gancho de audio en los primeros ~2 s, el clip empieza más tarde.
2. **Gancho en pantalla de 3–5 palabras** durante los primeros ~3 s
   ("NO SABÍA QUE ERA SU MADRE", "EL PEOR CLUTCH DE LA HISTORIA"). Aporta el
   contexto que falta sin explicar.
3. **Cold open / flash-forward**: mostrar 1,5–2,5 s del clímax al principio y
   luego "rebobinar" al inicio. Útil cuando el payoff llega tarde (> 10 s).
4. **Jump cuts**: eliminar pausas y silencios para que el ritmo no decaiga.
   Ojo: en gameplay, un silencio de voz puede ser tensión (no cortar si el
   audio del juego está alto).
5. **Segundo gancho** hacia los ~15 s en clips largos (giro, nueva tensión).
6. **Termina en el remate o en la reacción**, no en una frase a medias. Un
   final que enlaza con el principio favorece el *loop* (más reproducciones
   completas).
7. **Una sola idea por clip.** Si para entenderlo hace falta lo de hace 5
   minutos, no es un buen short (puede ser un vídeo largo).

Diagnóstico con analíticas: retención baja a los 3 s → problema de gancho;
buen gancho pero poca duración media → problema del cuerpo (relleno, falta de
escalada).

## 3. Cómo encuentran los clippers los momentos

### Señales que funcionan

| Señal | Por qué | Cómo se usa |
|---|---|---|
| **Velocidad del chat** | el chat reacciona en 1–2 s a todo lo que merece la pena; es un "detector gratis" | mensajes/s comparados con una **línea base móvil** (mediana de los últimos ~5 min); pico = varias veces la base |
| **Picos de audio** | gritos, risas, golpes, música de victoria | energía comparada con la **línea base local** (no con el máximo del VOD) |
| **Chat + audio a la vez** | Eklipse lo trata como su evento de mayor confianza | fusión de ambas curvas |
| **Palabras clave** | "clip", "clipéalo", "no puede ser", "KEKW", "jajaja", "W/L" | en la transcripción y en el chat |
| **Risas (en el chat y en la voz)** | el humor es el tipo de clip que más viaja | emotes de risa en el chat; ritmo "ja-ja-ja" en el audio |
| **Reacción con facecam** | los clips que salen de la burbuja del streamer casi siempre son de facecam | ¿funciona con el audio del juego silenciado? |

**El chat va con retraso.** Reacciona *después* del momento: el clip debe
empezar bastante antes del pico de mensajes (las guías hablan de hasta ~30 s
para incluir la preparación). La literatura académica lo trata explícitamente:
el trabajo de *implicit crowdsourcing* sobre chat de Twitch (arXiv 1910.12201)
puntúa ventanas deslizantes de chat y añade una etapa para **estimar dónde
empieza realmente** el highlight; la tesis de C. Ringer (Univ. de York, 2022)
estudia la fusión multimodal audio + vídeo + lenguaje y señala que fusionar
fuentes ruidosas es la dificultad central.

### Contexto y estructura

- Una reacción sin su detonante no funciona: **empieza antes de la decisión,
  el error o el momento de presión**.
- Si falta contexto, el **texto en pantalla** lo aporta (3–5 palabras).
- Velocidad: veinte clippers pueden encontrar el mismo momento; publicar
  pronto importa.

## 4. Subtítulos

Mucha gente ve los shorts sin sonido, y los subtítulos grandes son ya parte del
lenguaje visual del formato.

- **Palabra a palabra**: 1–3 palabras en pantalla, la palabra que se está
  diciendo resaltada en color. Es el estilo dominante en shorts.
- **Tipografía**: sans-serif gruesa (Montserrat/Anton/Poppins/Arial Black),
  blanco con **contorno negro grueso + sombra** para leerse sobre cualquier
  fondo.
- **Mayúsculas**: decisión estética. Más impacto en shorts; para accesibilidad
  pura se lee mejor en minúsculas (por eso KickClipper ofrece ambos estilos).
- **Posición**: centro o centro-bajo, **fuera del 20–25 % inferior y de la
  columna derecha**. En layout cámara+gameplay, en la costura entre ambos.
- **Animación**: aparición con un pequeño "pop" (escala 80 % → 100 %) da ritmo
  sin distraer.
- **Énfasis**: colorear de otro tono las palabras clave (cifras, insultos
  cariñosos, la palabra del remate). Con varios hablantes, un color por persona.
- Para vídeos horizontales largos: subtítulos clásicos de 1–2 líneas
  (32–42 caracteres por línea) abajo, o ninguno.

## 5. Encuadre vertical según el tipo de stream

| Situación | Layout recomendado |
|---|---|
| **Gameplay + facecam** | **Split**: cámara arriba (~40 % de la altura), gameplay abajo (~60 %), subtítulos en la costura. Es el layout del editor de clips de Twitch y de casi todas las herramientas. |
| **IRL / Just Chatting (una persona)** | Recorte 9:16 que **sigue la cara** (suavizado, sin temblores). |
| **Podcast / varios hablando** | **Foco en el hablante activo**: recorte 9:16 sobre quien habla, con corte seco al cambiar de hablante (duración mínima de plano ~1–1,5 s para no marear). Cuando hablan a la vez (*crosstalk*), **pantalla dividida** con los dos. |
| **Gameplay sin cámara** | Vídeo completo centrado sobre fondo difuminado del propio vídeo, o recorte central si la acción está en el centro. |

Cómo se detecta el hablante activo: triangulación audio-visual. Se detectan
caras, se mide el **movimiento de la boca** de cada una (apertura de mandíbula
o diferencia de píxeles en la zona de la boca) y se cruza con los tramos en los
que hay voz. Es el punto débil conocido de todas las herramientas: en
*crosstalk* el auto-encuadre puede fijarse en la cara equivocada; por eso se
recurre a la pantalla dividida y se permite corregir a mano.

Mantener a cada persona grande en el plano funciona mejor que meter a todos
siempre (las caras quedan diminutas en 9:16).

## 6. Audio

- Normaliza a **−14 LUFS** integrados, pico real ≤ −1,5 dBTP. YouTube baja lo
  que pase de −14; quedarse muy por debajo hace que el clip suene flojo al
  lado de los demás.
- La voz del streamer debe entenderse por encima del juego.

## 7. Título, descripción y hashtags

- Título-gancho de ≤ 60 caracteres, en el idioma del stream; promete el
  payoff sin destriparlo.
- Descripción de 1–2 frases + enlaces del streamer.
- 3–5 hashtags relevantes (juego, streamer, tipo de momento), no 30
  genéricos.

## 8. Permisos y políticas

- **Solo con permiso del creador** (o contenido propio). Muchos streamers
  tienen programas de clipping con reglas propias: revísalas antes de cortar
  drama o momentos fuera de contexto.
- **YouTube (contenido reutilizado)**: no prohíbe clips ni recopilaciones, pero
  monetizar exige **aportar valor original** (edición creativa, contexto,
  comentario). El cambio de julio de 2025 renombró "repetitious" a
  "inauthentic content" (contenido en masa/repetitivo); la política de
  contenido reutilizado no cambió.
- **TikTok**: los reposts con marca de agua, dúos y stitches no entran en
  Creator Rewards.

---

## 9. Cómo lo aplica KickClipper

| Técnica | Dónde |
|---|---|
| Curva de excitación con **línea base local**, energía, flujo espectral, banda de gritos y ritmo de risa | `kickclipper/pipeline/audio_analysis.py`, `webapp/js/audio.js` |
| **Velocidad del chat** del VOD con línea base móvil y compensación del retraso | `kickclipper/pipeline/chat.py`, `webapp/js/chat.js` (vía el marcador de Kick) |
| Candidatos **anclados a picos** (preparación → pico → reacción) además de ventanas deslizantes | `highlights.py` / `highlights.js` |
| Recorte automático de **intros sin gancho**, penalización de silencios | `highlights.py` / `highlights.js` |
| LLM con criterios de gancho, contexto autónomo y payoff; **elige la frase de inicio y fin**, escribe el **texto gancho** y las palabras clave | `highlights.py` (Ollama), `webapp/js/llm.js` (WebLLM, opcional) |
| Subtítulos palabra a palabra con pop, mayúsculas, palabras clave y **colores por hablante**, dentro de la zona segura | `subtitles.py`, `webapp/js/captions.js` |
| Texto gancho en pantalla los primeros segundos | ídem |
| **Split cámara + gameplay** con detección automática de la facecam | `vision.py` + `renderer.py`, `webapp/js/vision.js` + `compositor.js` |
| **Foco en el hablante activo** con duración mínima de plano y pantalla dividida en crosstalk | ídem |
| **Jump cuts** de silencios (sin cortar tensión de gameplay) y **cold open** opcional | `timeline.py` / `timeline.js` |
| Normalización **−14 LUFS** y exportación 1080×1920 H.264/AAC | `renderer.py`, `webapp/js/render.js` |

---

## Fuentes

Plataformas y especificaciones

- [Socialync — Safe zones de YouTube Shorts (cifras de Google para anuncios verticales)](https://www.socialync.io/tools/safe-zones/youtube-shorts)
- [Postiz — Vertical video dimensions](https://postiz.com/blog/vertical-video-dimensions)
- [Piktochart — How long should YouTube Shorts be (2026)](https://piktochart.com/blog/how-long-youtube-shorts/)
- [BlitzCut — YouTube Shorts length limits 2026](https://blitzcutai.com/blog/youtube-shorts-length-limits-2026)
- [Async — How long can YouTube Shorts be (2026)](https://async.com/blog/how-long-can-youtube-shorts-be/)
- [ttcalculator — How TikTok Creator Rewards work in 2026](https://ttcalculator.net/blog/how-tiktok-creator-rewards-work-2026/)
- [Postfa.st — TikTok monetization requirements 2026](https://postfa.st/blog/tiktok-monetization-requirements)
- [ShortsFast — TikTok Creator Rewards 2026 requirements](https://shortsfast.com/blog/tiktok-creator-rewards-2026-requirements/)
- [ClipSpeed — Video resolution guide for Shorts, Reels and TikTok](https://www.clipspeed.ai/blog/video-resolution-guide-shorts-reels-tiktok.html)
- [ShortSync — YouTube Shorts upload requirements 2026](https://shortsync.app/resources/youtube-shorts-upload-requirements-2026)
- [The Post Flow — Export settings for YouTube, Instagram, TikTok](https://thepostflow.com/post-production/post-production-workflows/export-settings-youtube-instagram-tiktok/)

Ganchos y retención

- [Deeka — How to make viral videos in 2026](https://deeka.ai/blog/how-to-make-viral-videos-2026)
- [Grow-it — Short-form video retention benchmarks](https://www.grow-it.se/en/resources/short-form-video-retention-benchmarks)
- [Hypenest — How the TikTok algorithm works in 2026](https://hypenest.ai/blogs/tiktok-algorithm-2026-video-hooks-retention)
- [Prapermedia — Viral YouTube Shorts 2026](https://prapermedia.com/blog/make-viral-youtube-shorts/)
- [cut.pro — 7 hooks for the first 3 seconds](https://cut.pro/en/blog/hooks-first-3-seconds-2026)
- [Eklipse — Stream hook strategy: the first two seconds](https://blog.eklipse.gg/beginner-guide-2/stream-hook-strategy-first-two-seconds.html)

Clipping de streams y detección de momentos

- [Towards Extracting Highlights From Recorded Live Videos: An Implicit Crowdsourcing Approach (arXiv 1910.12201)](https://arxiv.org/pdf/1910.12201)
- [C. Ringer — Tesis doctoral sobre detección multimodal de highlights en livestreams (Univ. de York, 2022)](https://etheses.whiterose.ac.uk/32406/)
- [Stanford CS231n 2024 — HiLAIT: automatic video highlighting leveraging audio and text](https://cs231n.stanford.edu/2024/papers/hilait-automatic-video-highlighting-system-leveraging-audio-text.pdf)
- [Eklipse — How does Eklipse automatically create clips](https://eklipse.gg/help/how-does-eklipse-automatically-create-clips/)
- [Opus — Clip the moments your live chat exploded](https://www.opus.pro/mcp/inspiration/clip-the-moments-your-live-chat-exploded)
- [Klypra — Twitch VOD highlights clipper](https://similarlabs.com/p/klypra-twitch-vod-highlights-clipper)
- [Streams Charts — Clip strategy: one stream, short-form content](https://streamscharts.com/news/clip-strategy-one-stream-short-form-content)
- [ClipSpeed — Clipping Twitch streamers for YouTube Shorts (2026)](https://www.clipspeed.ai/blog/clip-twitch-vods-youtube-shorts.html)
- [Lindsey Gamble — Twitch clip editor para vertical](https://www.lindseygamble.com/blog/twitch-rolls-out-new-clip-editor-to-convert-live-streams-into-vertical-videos)

Subtítulos

- [Opus — YouTube Shorts caption & subtitle best practices](https://www.opus.pro/blog/youtube-shorts-caption-subtitle-best-practices)
- [Opus — Best caption presets & styles that boost retention](https://www.opus.pro/blog/best-caption-presets-styles-boost-retention)
- [BlitzCut — How to make captions look professional](https://blitzcutai.com/blog/how-to-make-captions-look-professional)
- [ClipSpeed — Subtitles vs captions for short-form video](https://www.clipspeed.ai/blog/subtitles-vs-captions-which-better.html)
- [Meryl — 10 guidelines for creating great captioned videos](https://meryl.net/10-rules-you-need-to-create-great-captioned-videos/)

Encuadre y hablante activo

- [Vizard — Automatically centers the active speaker](https://vizard.ai/blog/vizard-now-automatically-centers-the-active-speaker)
- [Joyspace — Auto-crop multi-person podcasts with speaker detection](https://joyspace.ai/speaker-highlight-method-automated-framing)
- [Choppity — Automated tracking](https://www.choppity.com/features/automated-tracking/)
- [MediaPipe Face Landmarker (blendshapes como `jawOpen`)](https://developers.google.cn/mediapipe/solutions/vision/face_landmarker/python)

Políticas

- [AIR Media-Tech — YouTube monetization policy changes 2026](https://air.io/en/monetization/youtube-monetization-policy-changes-2026-a-complete-dated-timeline)
- [vidIQ — YouTube reused content policy guide](https://vidiq.com/blog/post/youtube-reused-content-policy-guide/)
