#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor de Circulares ASFI
==========================

Revisa si ASFI publicó circulares nuevas, descarga el PDF, lo envía por correo
a los destinatarios y actualiza el historial que muestra el panel (docs/index.html).

Cómo detecta las circulares
---------------------------
Los PDF de ASFI siguen un patrón numérico estable:

    https://servdmzw.asfi.gob.bo/circular/Circulares/ASFI_919.pdf

El robot recuerda el último número conocido y prueba el siguiente (920, 921, ...).
Si el archivo existe, es una circular nueva. No depende del diseño de ninguna
página web, por lo que no se rompe si ASFI rediseña su portal.

Uso
---
    python monitor.py                     # revisión normal
    python monitor.py --probar-correo     # además envía la última circular como prueba
    python monitor.py --simular-correo    # no envía; guarda los correos en ./correos_simulados
"""

from __future__ import annotations

import argparse
import html
import io
import json
import logging
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

RAIZ = Path(__file__).resolve().parent
RUTA_CONFIG = RAIZ / "config.json"
DIR_DOCS = RAIZ / "docs"
DIR_DATOS = DIR_DOCS / "datos"
RUTA_ESTADO = DIR_DATOS / "circulares.json"
RUTA_DATOS_JS = DIR_DATOS / "datos.js"
DIR_PDF = DIR_DOCS / "circulares"
DIR_CORREOS_SIMULADOS = RAIZ / "correos_simulados"

MAX_PDF_BYTES = 40 * 1024 * 1024       # descarta archivos absurdamente grandes
MAX_ADJUNTO_BYTES = 18 * 1024 * 1024   # Gmail admite 25 MB en total (con codificación)
MAX_POR_EJECUCION = 200                # tope de seguridad de circulares por revisión

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
NOMBRES_MES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
]

CONFIG_BASE = {
    "fuentes": [],
    "huecos_tolerados": 2,
    "pausa_segundos": 1.0,
    "verificar_ssl": "auto",
    "alerta_tras_fallos": 3,
    "zona_horaria": "America/La_Paz",
    "remitente_nombre": "Monitor de Circulares ASFI",
    "asunto_prefijo": "[ASFI]",
    "horario": {"dias": [1, 2, 3, 4, 5], "horas": [8, 10, 12, 14, 16, 18]},
}

log = logging.getLogger("monitor")


# ---------------------------------------------------------------------------
# Configuración y estado
# ---------------------------------------------------------------------------

def cargar_env_local() -> None:
    """Lee un archivo .env opcional (para ejecutar en su propia computadora)."""
    ruta = RAIZ / ".env"
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


def cargar_config(ruta: Path) -> dict:
    config = json.loads(json.dumps(CONFIG_BASE))
    with open(ruta, encoding="utf-8") as f:
        config.update(json.load(f))
    return config


def estado_inicial() -> dict:
    return {
        "version": 1,
        "fuentes": {},
        "circulares": [],
        "activacion_enviada": False,
        "revision": {
            "ultima": None,
            "ultima_exitosa": None,
            "resultado": None,
            "mensaje": "",
            "fallos_consecutivos": 0,
            "alerta_enviada": False,
        },
    }


def cargar_estado() -> dict:
    if not RUTA_ESTADO.exists():
        return estado_inicial()
    try:
        estado = json.loads(RUTA_ESTADO.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.error("El historial (%s) está dañado; se respalda y se empieza de nuevo.", RUTA_ESTADO)
        RUTA_ESTADO.rename(RUTA_ESTADO.with_suffix(".dañado.json"))
        return estado_inicial()
    base = estado_inicial()
    for clave, valor in base.items():
        estado.setdefault(clave, valor)
    for clave, valor in base["revision"].items():
        estado["revision"].setdefault(clave, valor)
    return estado


def _escribir_atomico(ruta: Path, texto: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    tmp.replace(ruta)


def enmascarar(correo: str) -> str:
    local, _, dominio = correo.partition("@")
    return f"{local[:1]}***@{dominio}" if local else f"***@{dominio}"


def guardar_estado(estado: dict, config: dict, correo: "Correo", ahora: datetime) -> None:
    """Guarda el historial y genera el archivo que lee el panel."""
    estado["circulares"].sort(key=lambda c: (c["numero"], c["fuente"]), reverse=True)
    _escribir_atomico(RUTA_ESTADO, json.dumps(estado, ensure_ascii=False, indent=2))

    rev = estado["revision"]
    publico = {
        "generado_en": ahora.isoformat(timespec="seconds"),
        "zona_horaria": config["zona_horaria"],
        "horario": config["horario"],
        "fuentes": [
            {"id": f["id"], "nombre": f["nombre"], "activa": bool(f.get("activa", True))}
            for f in config["fuentes"]
        ],
        "circulares": [
            {k: v for k, v in c.items()} for c in estado["circulares"]
        ],
        "revision": {
            "ultima": rev["ultima"],
            "ultima_exitosa": rev["ultima_exitosa"],
            "resultado": rev["resultado"],
            "mensaje": rev["mensaje"],
        },
        "destinatarios": [enmascarar(d) for d in correo.destinatarios],
        "correo_configurado": correo.listo,
    }
    _escribir_atomico(
        RUTA_DATOS_JS,
        "window.MONITOR_DATOS = " + json.dumps(publico, ensure_ascii=False, indent=2) + ";\n",
    )


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------

class Cliente:
    """Cliente HTTP con reintentos y manejo de certificados."""

    def __init__(self, config: dict):
        self.sesion = requests.Session()
        self.sesion.headers["User-Agent"] = (
            "MonitorCircularesASFI/1.0 (seguimiento interno de circulares publicas)"
        )
        modo = config.get("verificar_ssl", "auto")
        self.modo_ssl = "auto" if str(modo).lower() == "auto" else ("si" if modo else "no")
        self.sesion.verify = self.modo_ssl != "no"
        if self.modo_ssl == "no":
            self._silenciar_advertencias()
        self.pausa = float(config.get("pausa_segundos", 1.0))

    @staticmethod
    def _silenciar_advertencias() -> None:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def sondear(self, url: str, descargar: bool = True):
        """
        Devuelve (estado, datos):
          ("ok", bytes)          el PDF existe
          ("no_existe", None)    la dirección no tiene un PDF
          ("error", "mensaje")   problema de red o del servidor (no se puede concluir nada)
        """
        for intento in range(1, 4):
            try:
                r = self.sesion.get(url, timeout=(15, 90), stream=True)
            except requests.exceptions.SSLError as e:
                if self.modo_ssl == "auto" and self.sesion.verify:
                    log.warning(
                        "El certificado de seguridad de ASFI no pudo validarse; se continúa "
                        "sin validarlo (solo se descargan documentos públicos)."
                    )
                    self.sesion.verify = False
                    self._silenciar_advertencias()
                    continue
                return "error", f"Error de certificado SSL: {e.__class__.__name__}"
            except requests.exceptions.RequestException as e:
                if intento < 3:
                    time.sleep(3 * intento)
                    continue
                return "error", f"No se pudo conectar con ASFI ({e.__class__.__name__})"

            with r:
                if r.status_code in (404, 410):
                    return "no_existe", None
                if r.status_code != 200:
                    if r.status_code >= 500 and intento < 3:
                        time.sleep(3 * intento)
                        continue
                    return "error", f"ASFI respondió con el código HTTP {r.status_code}"

                if descargar:
                    buffer = bytearray()
                    for trozo in r.iter_content(65536):
                        buffer.extend(trozo)
                        if len(buffer) > MAX_PDF_BYTES:
                            return "error", "El archivo es demasiado grande"
                    datos = bytes(buffer)
                else:
                    datos = next(r.iter_content(1024), b"")

                if b"%PDF" not in datos[:1024]:
                    return "no_existe", None
                return "ok", (datos if descargar else b"")
        return "error", "ASFI no respondió"


# ---------------------------------------------------------------------------
# Lectura del PDF (fecha y asunto)
# ---------------------------------------------------------------------------

def leer_pdf(datos: bytes) -> tuple[str, int | None]:
    try:
        from pypdf import PdfReader
        lector = PdfReader(io.BytesIO(datos))
        texto = "\n".join((p.extract_text() or "") for p in lector.pages[:2])
        return texto, len(lector.pages)
    except Exception as e:  # noqa: BLE001 - un PDF ilegible no debe detener el flujo
        log.warning("No se pudo leer el texto del PDF: %s", e.__class__.__name__)
        return "", None


def interpretar_texto(texto: str) -> dict:
    """Extrae año del código, fecha de emisión y asunto (REF:) de forma tolerante."""
    lineal = re.sub(r"[ \t]+", " ", texto.replace("\r", ""))
    resultado = {"anio": None, "fecha": None, "asunto": None}

    m = re.search(r"ASFI\s*/\s*\d{1,4}\s*/\s*(\d{4})", lineal, re.I)
    if m:
        resultado["anio"] = int(m.group(1))

    m = re.search(r"(\d{1,2})\s+de\s+([A-Za-záéíóúÁÉÍÓÚ]+)\s+de(?:l)?\s+(\d{4})", lineal)
    if m and m.group(2).lower() in MESES:
        try:
            resultado["fecha"] = datetime(
                int(m.group(3)), MESES[m.group(2).lower()], int(m.group(1))
            ).date().isoformat()
        except ValueError:
            pass

    m = re.search(
        r"\bREF(?:ERENCIA)?\s*[:.]\s*(.+?)"
        r"(?:\n\s*\n|\n\s*(?:Se[ñn]or|Estimad|De mi|De nuestra|Mediante|Por medio|Tenemos)|$)",
        lineal, re.I | re.S,
    )
    if m:
        lineas = [l.strip() for l in m.group(1).strip().split("\n") if l.strip()][:3]
        asunto = re.sub(r"\s+", " ", " ".join(lineas)).strip(" .:-")
        if len(asunto) > 320:
            asunto = asunto[:317].rstrip() + "…"
        resultado["asunto"] = asunto or None
    return resultado


# ---------------------------------------------------------------------------
# Registro de circulares
# ---------------------------------------------------------------------------

def url_circular(fuente: dict, numero: int) -> str:
    return fuente["url_base"].format(numero=numero)


def registrar(fuente: dict, numero: int, datos: bytes, estado: dict, ahora: datetime, notificar: bool) -> dict | None:
    id_ = f"{fuente['id']}:{numero}"
    if any(c["id"] == id_ for c in estado["circulares"]):
        return None

    texto, paginas = leer_pdf(datos)
    info = interpretar_texto(texto)
    anio = info["anio"] or (int(info["fecha"][:4]) if info["fecha"] else ahora.year)

    carpeta = DIR_PDF / fuente["id"]
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = f"ASFI_{numero}.pdf"
    (carpeta / nombre).write_bytes(datos)

    registro = {
        "id": id_,
        "fuente": fuente["id"],
        "numero": numero,
        "anio": anio,
        "codigo": f"ASFI/{numero}/{anio}",
        "fecha": info["fecha"],
        "asunto": info["asunto"],
        "paginas": paginas,
        "tamano_kb": max(1, round(len(datos) / 1024)),
        "url_original": url_circular(fuente, numero),
        "archivo": f"circulares/{fuente['id']}/{nombre}",
        "detectada_en": ahora.isoformat(timespec="seconds"),
        "notificar": notificar,
        "correo_enviado": False,
    }
    estado["circulares"].append(registro)
    log.info("Circular registrada: %s (%s)", registro["codigo"], registro["asunto"] or "sin asunto legible")
    return registro


def explorar_hacia_adelante(fuente: dict, desde: int, cliente: Cliente, huecos: int):
    """Prueba desde, desde+1, ... hasta encontrar `huecos + 1` números seguidos inexistentes."""
    encontradas, fallidos, n = [], 0, desde
    while fallidos <= huecos and len(encontradas) < MAX_POR_EJECUCION:
        est, datos = cliente.sondear(url_circular(fuente, n))
        if est == "ok":
            encontradas.append((n, datos))
            fallidos = 0
        elif est == "no_existe":
            fallidos += 1
        else:
            return encontradas, datos
        n += 1
        time.sleep(cliente.pausa)
    return encontradas, None


def descubrir_inicio(fuente: dict, cliente: Cliente):
    """Primera ejecución: localiza una circular real cerca del número inicial configurado."""
    n0 = int(fuente["numero_inicial"])
    for n in range(n0, max(n0 - 26, 0), -1):
        est, datos = cliente.sondear(url_circular(fuente, n))
        if est == "ok":
            return n, datos, None
        if est == "error":
            return None, None, datos
        time.sleep(cliente.pausa)
    return None, None, (
        f"No se encontró ninguna circular cerca del número {n0}. "
        "Revise 'numero_inicial' y 'url_base' en config.json."
    )


def revisar_fuente(fuente: dict, estado: dict, cliente: Cliente, config: dict, ahora: datetime):
    """Devuelve (registros_nuevos, mensaje_de_error_o_None)."""
    fid = fuente["id"]
    huecos = int(config["huecos_tolerados"])
    info = estado["fuentes"].get(fid)
    nuevos: list[dict] = []
    error = None

    if info is None:
        log.info("[%s] Primera revisión: se toma como punto de partida el historial disponible.", fuente["nombre"])
        n, datos, error = descubrir_inicio(fuente, cliente)
        if error:
            return nuevos, error
        registrar(fuente, n, datos, estado, ahora, notificar=False)
        ultimo = n
        encontradas, error = explorar_hacia_adelante(fuente, n + 1, cliente, huecos)
        for num, contenido in encontradas:
            registrar(fuente, num, contenido, estado, ahora, notificar=False)
            ultimo = max(ultimo, num)
        estado["fuentes"][fid] = {"ultimo_numero": ultimo}
        return nuevos, error

    ultimo = int(info["ultimo_numero"])

    # Verificación de salud: la última circular conocida debe seguir accesible.
    est, msg = cliente.sondear(url_circular(fuente, ultimo), descargar=False)
    if est != "ok":
        detalle = msg if est == "error" else (
            f"la circular {ultimo} ya no está en la dirección esperada; ASFI pudo haber "
            "cambiado su sitio o bloqueado el acceso automático"
        )
        return nuevos, detalle
    time.sleep(cliente.pausa)

    encontradas, error = explorar_hacia_adelante(fuente, ultimo + 1, cliente, huecos)
    for num, contenido in encontradas:
        reg = registrar(fuente, num, contenido, estado, ahora, notificar=True)
        if reg:
            nuevos.append(reg)
        ultimo = max(ultimo, num)
    estado["fuentes"][fid] = {"ultimo_numero": ultimo}
    return nuevos, error


# ---------------------------------------------------------------------------
# Correo
# ---------------------------------------------------------------------------

def _e(texto) -> str:
    return html.escape(str(texto), quote=True)


def fecha_larga(iso: str | None) -> str:
    if not iso:
        return "No disponible"
    d = datetime.fromisoformat(iso[:10])
    return f"{d.day} de {NOMBRES_MES[d.month - 1]} de {d.year}"


def fecha_hora_larga(iso: str) -> str:
    d = datetime.fromisoformat(iso)
    return f"{d.day} de {NOMBRES_MES[d.month - 1]} de {d.year}, {d:%H:%M} (hora de Bolivia)"


def plantilla_html(titulo: str, encabezado: str, intro: str, filas: list[tuple[str, str]],
                   boton: tuple[str, str] | None, nota: str, pie: str, grande: bool = True) -> str:
    tam = "34px" if grande else "24px"
    filas_html = "".join(
        f'<tr><td style="padding:11px 0;border-top:1px solid #E3E8F0;color:#5B6678;font-size:13px;'
        f'width:140px;vertical-align:top">{_e(k)}</td>'
        f'<td style="padding:11px 0;border-top:1px solid #E3E8F0;color:#14284B;font-size:15px;'
        f'line-height:1.5">{_e(v)}</td></tr>'
        for k, v in filas
    )
    tabla = (
        f'<tr><td style="padding:4px 28px 0 28px"><table role="presentation" width="100%" '
        f'cellpadding="0" cellspacing="0">{filas_html}</table></td></tr>' if filas else ""
    )
    boton_html = ""
    if boton:
        boton_html = (
            f'<tr><td style="padding:22px 28px 6px 28px"><a href="{_e(boton[1])}" '
            f'style="display:inline-block;background:#14284B;color:#FFFFFF;text-decoration:none;'
            f'font-size:14px;font-weight:600;padding:12px 20px;border-radius:4px">{_e(boton[0])}</a></td></tr>'
        )
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(titulo)}</title></head>
<body style="margin:0;padding:0;background:#EEF1F6">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#EEF1F6;padding:24px 12px">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#FFFFFF;border-radius:6px;font-family:'Segoe UI',Helvetica,Arial,sans-serif">
<tr><td style="background:#14284B;padding:16px 28px;color:#FFFFFF;font-size:14px;border-radius:6px 6px 0 0">Monitor de Circulares ASFI</td></tr>
<tr><td style="padding:28px 28px 10px 28px">
<div style="font-size:14px;color:#5B6678;margin-bottom:8px">{_e(titulo)}</div>
<div style="font-family:Georgia,'Times New Roman',serif;font-size:{tam};line-height:1.15;color:#14284B;font-weight:bold">{_e(encabezado)}</div>
<p style="margin:14px 0 0 0;font-size:15px;line-height:1.55;color:#2B3648">{_e(intro)}</p>
</td></tr>
{tabla}
{boton_html}
<tr><td style="padding:12px 28px 26px 28px;color:#5B6678;font-size:13px;line-height:1.55">{_e(nota)}</td></tr>
<tr><td style="background:#F5F6F8;padding:16px 28px;color:#7A8497;font-size:12px;line-height:1.55;border-radius:0 0 6px 6px">{_e(pie)}</td></tr>
</table></td></tr></table></body></html>"""


def texto_plano(titulo: str, encabezado: str, intro: str, filas, enlace: str | None, nota: str, pie: str) -> str:
    partes = [titulo, encabezado, "", intro, ""]
    partes += [f"{k}: {v}" for k, v in filas]
    if enlace:
        partes += ["", f"Documento original: {enlace}"]
    partes += ["", nota, "", "--", pie]
    return "\n".join(partes)


class Correo:
    """Envío de correos por SMTP (Gmail por defecto). Los datos vienen de variables de entorno."""

    def __init__(self, config: dict, simular: bool):
        self.config = config
        self.simular = simular
        self.usuario = os.environ.get("SMTP_USUARIO", "").strip()
        # Las contraseñas de aplicación de Google se muestran con espacios: se ignoran.
        self.clave = os.environ.get("SMTP_CLAVE", "").replace(" ", "").strip()
        self.servidor = os.environ.get("SMTP_SERVIDOR", "").strip() or "smtp.gmail.com"
        self.puerto = int(os.environ.get("SMTP_PUERTO", "").strip() or 465)
        crudos = re.split(r"[,;\s]+", os.environ.get("DESTINATARIOS", ""))
        self.destinatarios = [d for d in crudos if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", d)]
        if simular and not self.destinatarios:
            self.destinatarios = ["destinatario@ejemplo.com"]
        self.url_panel = os.environ.get("URL_PANEL", "").strip()
        self._contador = 0

    @property
    def listo(self) -> bool:
        return self.simular or bool(self.usuario and self.clave and self.destinatarios)

    def _mensaje(self, asunto: str, texto: str, cuerpo_html: str, adjunto: bytes | None, nombre: str) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = asunto
        msg["From"] = formataddr((self.config["remitente_nombre"], self.usuario or "monitor@ejemplo.local"))
        msg["To"] = ", ".join(self.destinatarios)
        msg.set_content(texto)
        msg.add_alternative(cuerpo_html, subtype="html")
        if adjunto:
            msg.add_attachment(adjunto, maintype="application", subtype="pdf", filename=nombre)
        return msg

    def _enviar(self, msg: EmailMessage) -> bool:
        if self.simular:
            DIR_CORREOS_SIMULADOS.mkdir(exist_ok=True)
            self._contador += 1
            ruta = DIR_CORREOS_SIMULADOS / f"{datetime.now():%Y%m%d_%H%M%S}_{self._contador}.eml"
            ruta.write_bytes(bytes(msg))
            log.info("Correo simulado guardado en %s", ruta)
            return True
        ultimo_error = None
        for intento in range(1, 4):
            try:
                contexto = ssl.create_default_context()
                if self.puerto == 587:
                    with smtplib.SMTP(self.servidor, self.puerto, timeout=60) as s:
                        s.starttls(context=contexto)
                        s.login(self.usuario, self.clave)
                        s.send_message(msg)
                else:
                    with smtplib.SMTP_SSL(self.servidor, self.puerto, context=contexto, timeout=60) as s:
                        s.login(self.usuario, self.clave)
                        s.send_message(msg)
                return True
            except smtplib.SMTPAuthenticationError:
                log.error("El servidor de correo rechazó el usuario o la contraseña de aplicación.")
                return False
            except Exception as e:  # noqa: BLE001
                ultimo_error = e
                log.warning("Intento %d de envío falló: %s", intento, e.__class__.__name__)
                time.sleep(4 * intento)
        log.error("No se pudo enviar el correo: %s", ultimo_error)
        return False

    # -- mensajes concretos -------------------------------------------------

    def enviar_circular(self, reg: dict, etiqueta: str = "", intro_extra: str = "") -> bool:
        ruta = DIR_DOCS / reg["archivo"]
        pdf = ruta.read_bytes() if ruta.exists() else None
        adjuntar = bool(pdf) and len(pdf) <= MAX_ADJUNTO_BYTES
        nombre_pdf = f"Circular_ASFI_{reg['numero']}_{reg['anio']}.pdf"

        prefijo = self.config["asunto_prefijo"]
        asunto_txt = reg["asunto"] or "consulte el documento adjunto"
        corto = asunto_txt if len(asunto_txt) <= 90 else asunto_txt[:87].rstrip() + "…"
        asunto = f"{prefijo} {etiqueta}Nueva circular {reg['codigo']}: {corto}".replace("  ", " ")

        titulo = "Se emitió una nueva circular" if not etiqueta else "Correo de prueba: última circular registrada"
        intro = intro_extra or (
            "ASFI publicó una nueva circular. El documento se adjunta a este correo."
            if adjuntar else
            "ASFI publicó una nueva circular. El archivo es muy grande para adjuntarlo; use el botón para descargarlo."
        )
        filas = [
            ("Circular", reg["codigo"]),
            ("Fecha de emisión", fecha_larga(reg["fecha"])),
            ("Asunto", reg["asunto"] or "No se pudo leer automáticamente; consulte el PDF."),
            ("Detectada", fecha_hora_larga(reg["detectada_en"])),
            ("Documento", f"PDF, {reg['tamano_kb']} KB" + (f", {reg['paginas']} {'página' if reg['paginas'] == 1 else 'páginas'}" if reg["paginas"] else "")),
        ]
        nota = "El PDF es la copia descargada del sitio oficial de ASFI en el momento de la detección."
        if self.url_panel:
            nota += f" Historial completo: {self.url_panel}"
        pie = "Mensaje automático del Monitor de Circulares ASFI. Este servicio no es oficial ni está afiliado a ASFI."
        boton = ("Abrir el documento en ASFI", reg["url_original"])

        msg = self._mensaje(
            asunto,
            texto_plano(titulo, reg["codigo"], intro, filas, reg["url_original"], nota, pie),
            plantilla_html(titulo, reg["codigo"], intro, filas, boton, nota, pie),
            pdf if adjuntar else None,
            nombre_pdf,
        )
        return self._enviar(msg)

    def enviar_activacion(self, reg: dict | None, fuentes: list[str]) -> bool:
        titulo = "El monitor quedó activado"
        encabezado = "Monitor de Circulares ASFI"
        intro = (
            "Este mensaje confirma que el envío de correos funciona. A partir de ahora recibirá un correo "
            "por cada circular nueva, con el PDF adjunto."
        )
        filas = [("Fuentes vigiladas", ", ".join(fuentes)), ("Destinatarios", str(len(self.destinatarios)))]
        if reg:
            filas.append(("Última circular", f"{reg['codigo']}: {reg['asunto'] or 'ver PDF adjunto'}"))
        nota = "Como referencia, se adjunta la circular más reciente que existía al activar el monitor."
        pie = "Mensaje automático del Monitor de Circulares ASFI. Este servicio no es oficial ni está afiliado a ASFI."
        adjunto, nombre = None, ""
        if reg:
            ruta = DIR_DOCS / reg["archivo"]
            if ruta.exists() and ruta.stat().st_size <= MAX_ADJUNTO_BYTES:
                adjunto, nombre = ruta.read_bytes(), f"Circular_ASFI_{reg['numero']}_{reg['anio']}.pdf"
        msg = self._mensaje(
            f"{self.config['asunto_prefijo']} Monitor de circulares activado",
            texto_plano(titulo, encabezado, intro, filas, None, nota, pie),
            plantilla_html(titulo, encabezado, intro, filas, None, nota, pie, grande=False),
            adjunto, nombre,
        )
        return self._enviar(msg)

    def enviar_alerta(self, detalle: str, fallos: int) -> bool:
        titulo = "Atención"
        encabezado = "El monitor no pudo revisar ASFI"
        intro = (
            f"Las últimas {fallos} revisiones fallaron, así que podrían estar pasando circulares nuevas sin avisar. "
            "El monitor sigue intentando en cada ciclo."
        )
        filas = [("Detalle", detalle)]
        nota = (
            "Qué hacer: abra el sitio de ASFI y compruebe que sus circulares se ven con normalidad. Si el sitio "
            "cambió, revise la guía (LEEME.md), sección 'Si algo falla'."
        )
        pie = "Este aviso se envía una sola vez por cada interrupción."
        msg = self._mensaje(
            f"{self.config['asunto_prefijo']} Atención: el monitor no pudo revisar ASFI",
            texto_plano(titulo, encabezado, intro, filas, None, nota, pie),
            plantilla_html(titulo, encabezado, intro, filas, None, nota, pie, grande=False),
            None, "",
        )
        return self._enviar(msg)


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Monitor de circulares de ASFI")
    ap.add_argument("--config", default=str(RUTA_CONFIG), help="ruta de config.json")
    ap.add_argument("--probar-correo", action="store_true", help="envía la última circular como correo de prueba")
    ap.add_argument("--simular-correo", action="store_true", help="guarda los correos como .eml en vez de enviarlos")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cargar_env_local()
    config = cargar_config(Path(args.config))
    ahora = datetime.now(ZoneInfo(config["zona_horaria"]))
    estado = cargar_estado()
    correo = Correo(config, args.simular_correo)
    cliente = Cliente(config)
    salida = 0

    fuentes = [f for f in config["fuentes"] if f.get("activa", True)]
    if not fuentes:
        log.error("No hay fuentes activas en config.json.")
        return 1

    guardar = lambda: guardar_estado(estado, config, correo, ahora)  # noqa: E731

    # 1) Revisar ASFI ------------------------------------------------------
    errores: list[str] = []
    for fuente in fuentes:
        try:
            _, error = revisar_fuente(fuente, estado, cliente, config, ahora)
        except Exception as e:  # noqa: BLE001
            log.exception("Fallo inesperado en la fuente %s", fuente["id"])
            error = f"Error inesperado ({e.__class__.__name__})"
        if error:
            log.error("[%s] %s", fuente["nombre"], error)
            errores.append(f"{fuente['nombre']}: {error}")
        guardar()

    rev = estado["revision"]
    rev["ultima"] = ahora.isoformat(timespec="seconds")
    if errores:
        rev["resultado"] = "error"
        rev["mensaje"] = " | ".join(errores)
        rev["fallos_consecutivos"] += 1
    else:
        rev["resultado"] = "ok"
        rev["mensaje"] = ""
        rev["ultima_exitosa"] = rev["ultima"]
        rev["fallos_consecutivos"] = 0
        rev["alerta_enviada"] = False
    guardar()

    # 2) Correos pendientes ------------------------------------------------
    pendientes = [c for c in sorted(estado["circulares"], key=lambda c: c["numero"])
                  if c["notificar"] and not c["correo_enviado"]]
    if not correo.listo:
        if pendientes or args.probar_correo:
            log.error("Faltan datos de correo (SMTP_USUARIO, SMTP_CLAVE, DESTINATARIOS). "
                      "Las circulares quedan pendientes y se enviarán cuando se configuren.")
            salida = 1
        else:
            log.warning("El correo aún no está configurado (SMTP_USUARIO, SMTP_CLAVE, DESTINATARIOS).")
    else:
        for reg in pendientes:
            if correo.enviar_circular(reg):
                reg["correo_enviado"] = True
                log.info("Correo enviado: %s", reg["codigo"])
            else:
                salida = 1
            guardar()

        ultima = None
        if estado["circulares"]:
            ultima = max(estado["circulares"], key=lambda c: (c["fecha"] or c["detectada_en"][:10], c["numero"]))

        if args.probar_correo:
            if ultima and correo.enviar_circular(ultima, etiqueta="[PRUEBA] "):
                estado["activacion_enviada"] = True
                log.info("Correo de prueba enviado con la circular %s", ultima["codigo"])
            else:
                log.error("No se pudo enviar el correo de prueba.")
                salida = 1
        elif not estado["activacion_enviada"] and ultima:
            if correo.enviar_activacion(ultima, [f["nombre"] for f in fuentes]):
                estado["activacion_enviada"] = True
                log.info("Correo de activación enviado.")
            else:
                salida = 1
        guardar()

        # 3) Alerta por interrupciones prolongadas ---------------------------
        if (rev["fallos_consecutivos"] >= int(config["alerta_tras_fallos"]) and not rev["alerta_enviada"]):
            if correo.enviar_alerta(rev["mensaje"], rev["fallos_consecutivos"]):
                rev["alerta_enviada"] = True
                log.info("Aviso de interrupción enviado.")
            guardar()

    log.info("Revisión terminada: %s.", "con errores" if errores else "sin problemas")
    return salida


if __name__ == "__main__":
    sys.exit(main())
