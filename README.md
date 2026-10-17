# automicronomade

Convierte el catálogo de **Wami Records / Micronomade Dub** (la carpeta `wami backup` de Drive) en videos de YouTube, sin tocar nada:

- **Compilados largos** (1–2 por semana): un release completo (`album`) o un mix de temas de varios releases (`mix`). Cada tema muestra la portada de su release, con capítulos reales, link a Bandcamp en la primera línea de la descripción y miniatura. Van a YouTube por **Zernio**.
- **Shorts verticales** (1 por día): 22–30 s de la parte más enérgica de un tema, con la imagen latiendo al bajo y el link de Bandcamp. Van a YouTube por **Buffer**.

Corre solo en **GitHub Actions** (gratis). Ritmo: martes y viernes (compilados) y todos los días (Shorts). Para cambiarlo, editá el `cron` de `.github/workflows/`.

Nunca repite: guarda en `state/state.json` qué usó y elige siempre lo que hace más tiempo que no sale.

---

## Puesta en marcha (una sola vez, ~25 min)

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

| Secret | Qué es |
|---|---|
| `GDRIVE_SA_JSON` | Contenido completo del JSON del paso 1 |
| `ZERNIO_API_KEY` | Zernio → API keys |
| `BUFFER_API_KEY` | https://publish.buffer.com/settings/api |

### 3. Conectar los canales
- **Zernio**: conectá el canal de YouTube (el plan gratis incluye 20 posts por mes y 2 perfiles).
- **Buffer**: conectá el mismo canal de YouTube.
- Para subir videos de **más de 15 min** el canal tiene que estar **verificado por teléfono** (youtube.com/verify). Si no, Zernio rechaza los compilados largos.
- Si tenés más de un canal, completá `zernio_account_id` y `buffer_channel_id` en `config.yaml` (con un solo canal se detectan solos).

### 4. Primera verificación
1. *Actions → catalog-check → Run workflow*. Tiene que listar tus releases y temas. Si sale vacío, la carpeta no está compartida con la cuenta de servicio.
2. *Actions → short → Run workflow* con `dry_run` activado. Descargá el artefacto `short-preview` y mirá el video.
3. Lo mismo con *compilation* (te deja `preview.mp4` de 90 s + miniatura + `meta.json` con título y descripción).
4. Primera subida real, **en privado**: *Run workflow* con `dry_run` desactivado y `visibility: private`. Revisá en YouTube Studio que se vea bien y que Buffer / Zernio hayan tomado el video.
5. Cuando esté todo bien, poné `publish.visibility: public` en `config.yaml`. Desde ahí corre solo.

---

## Qué podés ajustar

**`config.yaml`** — ritmo, duraciones, plantillas de título, tags, visibilidad, qué publicador usa cada formato.

**`releases.yaml`** (opcional) — para corregir lo que el parseo del nombre de carpeta no acierte o para poner el link exacto de cada release en Bandcamp. Ver el ejemplo dentro del archivo. Con `enabled: false` sacás un release del circuito (útil para remixes de temas de terceros o material sin liberar).

**Banco de visuales** (opcional) — sin esto ya funciona con las portadas (`cover.jpg` de cada release). Para sumar variedad, creá una carpeta en Drive con subcarpetas `landscape/`, `portrait/` y `loops/`, compartila con la cuenta de servicio y pegá su id en `source.visuals_folder_id`. Las imágenes y los loops (de Magnific, Kie, lo que quieras) se mezclan con las portadas. Los loops de video se arman ida y vuelta para que no salten.

---

## Límites a tener presentes

- **YouTube Data API**: los videos subidos por una app sin auditar quedan *privados*. Por eso se publica a través de Zernio y Buffer, que ya pasaron esa auditoría.
- **Zernio gratis**: 20 posts por mes. Con 8 compilados al mes sobra; no pongas `shorts_publisher: zernio` salvo que subas el plan.
- **Buffer**: API disponible en todos los planes, incluido el gratis (100 pedidos cada 15 min, 250 por día).
- **GitHub Actions**: repo privado = 2.000 min/mes gratis. Un compilado ronda 15–25 min y un Short 2–4 min: ~250 min/mes con el ritmo por defecto.
- **Monetización**: YouTube no monetiza contenido masivo/repetitivo. Mantené el ritmo moderado y sumá visuales variados (banco) para que los videos no parezcan una plantilla.
- El pipeline **no genera ni compra views**. Solo publica contenido real a un ritmo razonable.

## Puntos a confirmar en la primera corrida real

Estas piezas salen de la documentación pública y están probadas contra un servidor simulado, pero no contra las APIs reales (no había credenciales). Por eso la primera subida es en privado:

1. Formato exacto de `timezone` en Zernio (solo importa si ponés `publish_now: false`).
2. Que Buffer siga tomando el video si el post queda en cola varios días (el archivo vive 7 días en el hosting temporal de Zernio). Si lo ves fallar, usá `buffer.mode: customScheduled` con `delay_hours` corto.
3. Que `isAiGenerated` / `containsSyntheticMedia` reflejen lo que corresponde (por defecto `false`: las portadas son arte propio).

## Pruebas

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v          # 16 pruebas: parseo, metadata, estado, publicadores
python tests/make_fixture.py /tmp/fx              # catálogo falso para probar el render localmente
python -m autotube.run short --dry-run --config /tmp/fx/config.test.yaml --out /tmp/out
```
