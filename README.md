# automicronomade

Convierte el catálogo de **Wami Records / Micronomade Dub** (la carpeta `wami backup` de Drive) en **Shorts de YouTube**, sin tocar nada:

- **1 Short por día**: 22–30 s de la parte más enérgica de un tema, con la imagen latiendo al bajo y el link de Bandcamp en la descripción. Se publica por **Buffer**.
- **Compilados largos** (álbum completo o mix de varios releases, con capítulos y miniatura): el código está listo y probado, pero **está apagado**, porque Buffer no publica videos largos de YouTube. Se activa conectando Zernio (ver más abajo).

Corre solo en **GitHub Actions** (gratis). Para cambiar el horario editá el `cron` de `.github/workflows/short.yml`.

Nunca repite: guarda en `state/state.json` qué usó y elige siempre lo que hace más tiempo que no sale.

---

## Puesta en marcha (una sola vez)

### 1. Llave de lectura de Google Drive
La automatización corre en un servidor que no puede entrar a tu Drive; necesita una "cuenta de servicio" con permiso de **solo lectura**.

1. [console.cloud.google.com](https://console.cloud.google.com) → crear proyecto (nombre libre).
2. *APIs y servicios* → **Habilitar APIs** → **Google Drive API**.
3. *IAM y administración* → **Cuentas de servicio** → crear una (`autotube`, sin roles).
4. Entrá a esa cuenta → **Claves** → *Agregar clave* → **JSON**. Se descarga un archivo.
5. En Drive, compartí **solo la carpeta `wami backup`** como **Lector** con el email de la cuenta de servicio (termina en `iam.gserviceaccount.com`).
   - ⚠ No compartas `Musica de Backup`: adentro hay carpetas de otras personas (p. ej. `MUSICA ARI`).

### 2. Secrets del repo
En GitHub: *Settings → Secrets and variables → Actions → New repository secret*. Se pegan **ahí**, nunca en un chat ni en un archivo.

| Secret | Qué es | Dónde se saca |
|---|---|---|
| `GDRIVE_SA_JSON` | Contenido completo del JSON del paso 1 | Google Cloud |
| `BUFFER_API_KEY` | Llave de la API de Buffer | https://publish.buffer.com/settings/api |
| `CLOUDINARY_URL` | `cloudinary://<api_key>:<api_secret>@<cloud_name>` | Cloudinary → Settings → API Keys (botón de copiar la "API environment variable") |
| `ZERNIO_API_KEY` | *Opcional*, solo para publicar compilados largos | Zernio → API keys |

**¿Por qué Cloudinary?** Buffer no recibe archivos: pide una URL pública, directa y estable para bajar el video cuando toca publicar. Cloudinary (plan gratis, sin tarjeta) la da. Los videos se borran solos a los 3 días (`publish.cloudinary.keep_days`). El plan gratis acepta hasta 100 MB por archivo (un Short pesa 10–20 MB) y 25 créditos al mes (alcanza de sobra).

### 3. Conectar el canal
- **Buffer**: conectá el canal de YouTube. Con un solo canal se detecta solo; si tenés más de uno, completá `buffer_channel_id` en `config.yaml`.

### 4. Primera verificación
1. *Actions → catalog-check → Run workflow*. Tiene que listar tus releases y temas. Si sale vacío, la carpeta no está compartida con la cuenta de servicio.
2. *Actions → short → Run workflow* con `dry_run` activado. Descargá el artefacto `short-preview` y mirá el video.
3. Primera subida real, **en privado**: *Run workflow* con `dry_run` desactivado. Revisá en YouTube Studio que el Short se vea bien (sale unas 3 horas después, `publish.buffer.delay_hours`).
4. Cuando esté todo bien, poné `publish.visibility: public` en `config.yaml`. Desde ahí corre solo, todos los días.

---

## Qué podés ajustar

**`config.yaml`** — duraciones, plantillas de título, tags, visibilidad, horario de salida en Buffer, y la frase de presentación del sello.

**`releases.yaml`** (opcional) — para corregir lo que el parseo del nombre de carpeta no acierte o para poner el link exacto de cada release en Bandcamp. Con `enabled: false` sacás un release del circuito (útil para remixes de temas de terceros o material sin liberar).

**Banco de visuales** (opcional) — sin esto ya funciona con las portadas (`cover.jpg` de cada release). Para sumar variedad, creá una carpeta en Drive con subcarpetas `landscape/`, `portrait/` y `loops/`, compartila con la cuenta de servicio y pegá su id en `source.visuals_folder_id`. Las imágenes y los loops (de Magnific, Kie, lo que quieras) se mezclan con las portadas.

## Activar los compilados largos (más adelante)

1. Conectá el canal de YouTube en Zernio y guardá `ZERNIO_API_KEY`.
2. Verificá el canal por teléfono (youtube.com/verify): sin eso YouTube no deja subir más de 15 min.
3. En `config.yaml` poné `publish.long_publisher: zernio`.
4. En `.github/workflows/compilation.yml` descomentá el `schedule`.
5. Probá con *Run workflow* → `dry_run` (te deja `preview.mp4` de 90 s, la miniatura y `meta.json`) y después una subida en privado.

Medido en el entorno de pruebas: un mix de 53 min en 1080p se arma en ~10 min, usa ~770 MB de memoria y pesa ~1,1 GB (todo dentro de los límites gratis de GitHub y de Zernio).

---

## Límites a tener presentes

- **Buffer** solo publica Shorts de YouTube (hasta 3 min, vertical o cuadrado). Su API está disponible en el plan gratis (100 pedidos cada 15 min, 250 por día) y en el plan gratis la cola admite 10 posts por canal; con 1 por día sobra.
- **YouTube Data API**: los videos subidos por una app sin auditar quedan *privados*. Por eso no se sube directo: se pasa por Buffer.
- **GitHub Actions**: repo privado = 2.000 min/mes gratis. Un Short ronda 2–4 min: ~100 min/mes.
- **Monetización**: YouTube no monetiza contenido masivo/repetitivo. Un Short diario es un ritmo razonable; sumá visuales variados (banco) para que no parezcan una plantilla.
- El pipeline **no genera ni compra views**. Solo publica contenido real a un ritmo razonable.

## Puntos a confirmar en la primera corrida real

Estas piezas salen de la documentación pública y están probadas contra servidores simulados, pero no contra las APIs reales (no había credenciales). Por eso la primera subida es en privado:

1. Que Buffer acepte la URL de Cloudinary y publique el Short con título y descripción (si falla, el error de Buffer sale tal cual en el resumen del workflow).
2. Que `isAiGenerated` refleje lo que corresponde (por defecto `false`: las portadas son arte propio).
3. Si preferís que salga en el próximo horario libre de tu cola de Buffer en vez de 3 horas después, poné `publish.buffer.mode: addToQueue`.

## Pruebas

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v          # 24 pruebas: parseo, metadata, estado, publicadores, hosting
python tests/make_fixture.py /tmp/fx              # catálogo falso para probar el render localmente
python -m autotube.run short --dry-run --config /tmp/fx/config.test.yaml --out /tmp/out
```
