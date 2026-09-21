# Monitor de Circulares ASFI

Un robot que revisa automáticamente si ASFI publicó una circular nueva, **descarga el PDF** y se lo **envía por correo a usted y a su Gerente**. Además mantiene un **panel web** con el historial de circulares.

Funciona en la nube, gratis, con GitHub. No necesita que su computadora esté encendida.

---

## Lo que necesita antes de empezar

1. Una cuenta gratuita en **GitHub** (github.com).
2. Una cuenta de **Gmail** desde la que saldrán los correos (puede ser una cuenta nueva y exclusiva para esto; es lo más recomendable).
3. Los correos de los destinatarios (el suyo y el de su Gerente).

Tiempo estimado: 20 a 30 minutos.

---

## Paso 1. Crear el repositorio en GitHub

1. Entre a GitHub y haga clic en **New** (o en el «+» de arriba, luego **New repository**).
2. Nombre: `monitor-circulares-asfi`.
3. Elija **Public** si quiere ver el panel en línea gratis (ver Paso 7). Elija **Private** si no le importa el panel en línea.
4. Deje todo lo demás sin marcar y haga clic en **Create repository**.

> **Sobre la privacidad:** aunque el repositorio sea público, sus correos y contraseñas **nunca** quedan visibles, porque se guardan aparte en los «Secrets» (Paso 4). Lo único público serían las circulares de ASFI (que ya son públicas) y direcciones parcialmente ocultas como `j***@empresa.com`.

## Paso 2. Subir los archivos

1. Descomprima el archivo `monitor-circulares-asfi.zip` en su computadora.
2. En la página de su repositorio, haga clic en **uploading an existing file**.
3. Arrastre **todo el contenido** de la carpeta descomprimida (los archivos `monitor.py`, `config.json`, `requirements.txt`, `LEEME.md`, y las carpetas `docs` y `.github`).
4. Haga clic en **Commit changes**.

> **Si no aparece la carpeta `.github`:** algunos computadores (Mac, Linux) ocultan las carpetas que empiezan con punto. En ese caso cree el archivo a mano: en GitHub, **Add file → Create new file**, escriba como nombre `.github/workflows/monitor.yml` (al escribir las barras `/` se crean las carpetas) y pegue dentro el contenido del archivo `monitor.yml` que viene en el zip.

## Paso 3. Crear la contraseña de aplicación de Gmail

Gmail no permite que un programa use su contraseña normal. Hay que crear una «contraseña de aplicación», que se puede borrar en cualquier momento.

1. Entre a su cuenta de Google → **Seguridad** y active la **verificación en dos pasos** (si aún no la tiene).
2. Vaya a **myaccount.google.com/apppasswords**.
3. Escriba un nombre, por ejemplo `Monitor ASFI`, y haga clic en **Crear**.
4. Google mostrará una clave de 16 letras (como `abcd efgh ijkl mnop`). **Cópiela**; no se vuelve a mostrar.

> Si la opción de contraseñas de aplicación no aparece, su cuenta puede ser de una empresa (Google Workspace) con esa función bloqueada. En ese caso use una cuenta Gmail personal o pida a su área de sistemas que la habilite.

## Paso 4. Guardar los datos de correo (Secrets)

En su repositorio: **Settings → Secrets and variables → Actions → New repository secret**. Cree estos tres, uno por uno:

| Nombre (exacto) | Qué poner |
|---|---|
| `SMTP_USUARIO` | El Gmail desde el que saldrán los correos, por ejemplo `monitor.circulares@gmail.com` |
| `SMTP_CLAVE` | La clave de 16 letras del Paso 3 (con o sin espacios) |
| `DESTINATARIOS` | Los correos que recibirán el aviso, separados por coma: `usted@empresa.com, gerente@empresa.com` |

## Paso 5. Primera ejecución

1. Vaya a la pestaña **Actions** del repositorio. Si GitHub pregunta, acepte habilitar los flujos de trabajo.
2. Elija **Monitor de circulares ASFI** en la lista de la izquierda.
3. Haga clic en **Run workflow**, deje «revisar» y confirme.
4. Espere 1 a 3 minutos. Cuando aparezca una marca verde, revise su correo.

**Qué pasa en la primera ejecución:** el robot toma como punto de partida las circulares que ya existen (no le envía las antiguas) y le manda **un correo de activación** con la circular más reciente adjunta. Así confirma que todo funciona. Desde ahí en adelante recibirá **un correo por cada circular nueva**.

Si quiere probar el correo en cualquier momento: **Run workflow → probar_correo**. Le llegará la última circular como prueba.

## Paso 6. Dejarlo funcionando

No hace falta hacer nada más. El robot se ejecuta solo **cada 2 horas, de lunes a viernes, de 08:00 a 18:00 (hora de Bolivia)**. Cada revisión queda guardada en la pestaña Actions y en el historial.

## Paso 7 (opcional). Activar el panel en línea

1. **Settings → Pages**.
2. En **Source** elija **Deploy from a branch**; en **Branch** elija `main` y la carpeta `/docs`; guarde.
3. En un par de minutos su panel estará en `https://SU_USUARIO.github.io/monitor-circulares-asfi/`.
4. Comparta ese enlace con su Gerente.

*(Opcional)* Para que los correos incluyan un enlace al panel: **Settings → Secrets and variables → Actions → pestaña Variables → New repository variable**, nombre `URL_PANEL`, valor el enlace de arriba.

> Si eligió repositorio privado, GitHub Pages requiere un plan de pago. Alternativa gratuita: en el repositorio, **Code → Download ZIP** y abra `docs/index.html` en su navegador (muestra los datos del momento de la descarga).

---

## Cambios habituales

- **Agregar o quitar un destinatario:** edite el secreto `DESTINATARIOS` (Settings → Secrets and variables → Actions).
- **También vigilar las circulares del Mercado de Valores:** abra `config.json`, cambie `"activa": false` a `"activa": true` en la fuente «Mercado de Valores» y guarde. Este bloque tiene su propia numeración.
- **Cambiar la frecuencia:** edite la línea `cron` en `.github/workflows/monitor.yml` (la hora está en UTC; Bolivia es UTC-4, así que las 08:00 de Bolivia son las 12:00 UTC) y ajuste `"horario"` en `config.json` para que el panel muestre la próxima revisión correcta.
- **Ver más historial al inicio:** en `config.json`, baje `numero_inicial` (por ejemplo a `890`) antes de la primera ejecución. Solo se guardarán, sin enviar correo, las circulares desde ese número.

---

## Cómo detecta las circulares (para quien quiera saberlo)

Los PDF de ASFI tienen direcciones con numeración correlativa, como `.../circular/Circulares/ASFI_919.pdf`. El robot recuerda el último número y prueba el siguiente. Si existe, es una circular nueva: la descarga, lee del PDF la fecha y el asunto (línea «REF:») y la envía. No depende del diseño de la página de ASFI. Tolera saltos de hasta 2 números en la numeración.

## Si algo falla

El robot avisa por sí solo: si no logra revisar ASFI **3 veces seguidas**, le envía un correo de alerta (una sola vez por interrupción). Además, en cada revisión comprueba que la última circular conocida siga accesible; si deja de estarlo (ASFI cambió su sitio o bloqueó el acceso), lo detecta y lo reporta.

| Síntoma | Qué hacer |
|---|---|
| No llega el correo de activación | Revise la carpeta de spam. Luego abra la ejecución en **Actions** y lea el paso «Revisar circulares nuevas». Si dice que el servidor rechazó el usuario o la contraseña, cree una nueva contraseña de aplicación (Paso 3) y actualice `SMTP_CLAVE`. |
| Dice «Faltan datos de correo» | Falta uno de los tres secretos del Paso 4, o el nombre está mal escrito. |
| Dice «No se pudo conectar con ASFI» o «HTTP 403» | El sitio de ASFI puede estar caído o bloqueando los servidores de GitHub (algunos sitios públicos bloquean tráfico del extranjero). Pruebe de nuevo más tarde. Si persiste, use la alternativa de abajo. |
| Dice «la circular N ya no está en la dirección esperada» | ASFI cambió las direcciones de sus PDF. Abra una circular reciente en el sitio de ASFI, copie la dirección del PDF y ajuste `url_base` en `config.json`. |
| «No se encontró ninguna circular cerca del número…» | El `numero_inicial` de `config.json` no corresponde a una circular real. Póngale el número de una circular reciente. |
| El panel dice «Sin revisiones recientes» | Hace más de 3 días que no se ejecuta. GitHub pausa los flujos programados en repositorios sin actividad, pero como el robot guarda su historial en cada revisión, normalmente se mantiene activo. Entre a **Actions** y vuelva a habilitarlo si aparece pausado. |

## Alternativa: ejecutarlo en su propia computadora

Si GitHub no puede acceder a ASFI, el mismo programa funciona en Windows, Mac o Linux:

1. Instale **Python 3.10 o superior** (python.org).
2. En la carpeta del proyecto, abra una terminal y ejecute: `pip install -r requirements.txt`
3. Copie `.env.ejemplo` como `.env` y complete sus datos de correo.
4. Ejecute `python monitor.py`. Para automatizarlo en Windows, use el **Programador de tareas** y cree una tarea que ejecute ese comando cada 2 horas.

Para ver cómo quedarían los correos sin enviarlos: `python monitor.py --simular-correo` (los guarda en la carpeta `correos_simulados`).

## Aviso importante

- Este servicio es **independiente y no oficial**; no está afiliado a ASFI. Para cualquier decisión normativa, verifique siempre el documento en el sitio oficial de ASFI.
- El robot hace pocas consultas (unas pocas por revisión) y se identifica con un nombre claro, para no cargar el sitio de ASFI.
- El programa fue probado con un servidor simulado de ASFI (circulares nuevas, saltos de numeración, caídas del sitio y alertas). **No pudo probarse contra el sitio real**, porque el servidor de ASFI bloquea el acceso automatizado desde el entorno de desarrollo. Por eso el Paso 5 es importante: es la primera prueba con el sitio real.
