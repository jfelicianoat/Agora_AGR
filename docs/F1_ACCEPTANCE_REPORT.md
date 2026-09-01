# RESULTADO DE FASE — F1 Agora Desktop

Fecha: 2026-09-01  
Rama: `codex/agora-f0`  
Estado: completada técnicamente; no existe puerta humana entre F1 y F2.

## Implementado

- Fachada `AgoraApplication` sin Qt para inicialización, snapshots, detalle de CARD,
  censo tolerante de perfiles, actividad y ejecución del dispatcher.
- Aplicación Windows PySide6/Qt con:
  - BOARD completo y contadores por estado;
  - detalle de CARD y metadatos;
  - pestaña Record;
  - perfiles, handles, refuses, skills y harness;
  - actividad del dispatcher;
  - errores de contratos corruptos;
  - simulación y ejecución de una ronda;
  - refresco manual y periódico.
- El paquete desktop es opcional: el core no importa ni requiere PySide6.
- La ventana no importa APIs de filesystem ni escribe ficheros; todas las acciones pasan
  por la fachada y los servicios de dominio.
- Cerrar la ventana no altera el BOARD. Al reabrirla se reconstruye el estado desde disco.
- Script reproducible de aceptación visual con interacciones QtTest reales.

## Archivos

- `src/agora/application.py`
- `src/agora/desktop/__init__.py`
- `src/agora/desktop/__main__.py`
- `src/agora/desktop/main.py`
- `src/agora/desktop/window.py`
- `tests/test_application.py`
- `tests/test_desktop.py`
- `scripts/capture_f1_acceptance.py`
- `docs/assets/f1-acceptance.png`
- `README.md`, `pyproject.toml` y `.gitignore`

## Tests

Comando:

```powershell
python -m pytest -q --basetemp=tmp\pytest-f1-final
```

Resultado: **31 passed in 1.10s**.

Las diez pruebas nuevas demuestran:

1. importar el core no carga PySide6;
2. un snapshot refleja movimientos reales del BOARD;
3. CARD y PROFILE corruptos son visibles sin ocultar contratos válidos;
4. el dispatcher se invoca por el servicio y registra actividad;
5. el detalle rechaza path traversal;
6. el refresco visual refleja un claim externo;
7. una CARD corrupta aparece en BOARD y Errores;
8. el botón de ejecución termina la CARD y muestra el Record;
9. cerrar y reabrir conserva el estado persistente;
10. el módulo de widgets no dispone de una superficie directa de escritura filesystem.

Comprobaciones adicionales:

- `ruff check .`: **All checks passed**.
- PySide6: **6.10.2**; Qt: **6.10.2**.
- El control de tipos estricto sigue configurado, pero `mypy` no está disponible en un
  entorno ejecutable sano de esta máquina, según lo documentado en F0.

## Evidencia de usuario y visual

Se creó un workspace aislado con una CARD válida, una CARD corrupta, un PROFILE y un SKILL.
La prueba visible realizó clicks Qt reales para:

1. abrir la aplicación;
2. ejecutar una ronda;
3. seleccionar la CARD terminada;
4. abrir la pestaña Record;
5. verificar el artefacto y capturar la ventana.

Resultado exacto del script:

```json
{"completed_cards": 1, "record_visible": true, "screenshot": true}
```

La captura `docs/assets/f1-acceptance.png` muestra simultáneamente la CARD corrupta con
aviso visible, la CARD terminada y el Record cronológico con claim, admisión, skill,
artefacto absoluto y cierre.

Se intentó además controlar la ventana mediante Windows UI Automation. Windows confirmó
que la ventana `Agora Desktop` tenía un handle y respondía, pero el controlador seguro no
la devolvió entre sus ventanas seleccionables. No se fabricó un handle ni se usaron
coordenadas a ciegas. La interacción se validó mediante QtTest y renderizado Qt nativo.

## Verificado realmente

- El core funciona sin Qt.
- La UI no escribe directamente en `KANBAN`.
- Los movimientos realizados fuera de la UI aparecen tras actualizar.
- Los errores de front matter son visibles y no derriban la ventana.
- El dispatcher ejecuta mediante dominio, genera artefacto, mueve a `done` y actualiza UI.
- Cerrar y construir una nueva ventana y servicio conserva la CARD.
- La interfaz renderiza correctamente con fuentes Windows en 1240×780.

## No verificado

- Instalación en un equipo Windows limpio mediante instalador: aún no existe empaquetado.
- Ejecución persistente en bandeja o como servicio al cerrar la ventana: F1 no incorpora
  todavía un runtime en segundo plano; el core sigue siendo ejecutable por separado.
- Accesibilidad completa mediante UI Automation: el helper disponible no enumeró la
  ventana Qt, aunque Qt expone nombres de objeto y los widgets fueron probados directamente.

## Deuda y riesgos

- El refresco es síncrono y adecuado para filesystem local; F2 debe aislar llamadas remotas
  para que una red lenta no bloquee el hilo de UI.
- La actividad de sesión es efímera. El historial durable es el Record; F7 incorporará
  eventos, métricas e histórico avanzado.
- F2 debe conservar `AgoraApplication` como frontera y no introducir HTTP dentro de widgets.
- La rama mantiene el nombre histórico `codex/agora-f0`; se conserva para no reescribir la
  historia aprobada y los commits de F1 se mantienen separados.

## Decisiones descubiertas

- Un censo tolerante por documento es necesario para mostrar errores sin que un único
  PROFILE o CARD corrupto oculte todo el BOARD.
- `AgoraApplication` será también una frontera útil para la futura API: la UI, CLI y HTTP
  pueden compartir operaciones sin duplicar reglas.
- Record y metadatos se presentan por separado; esto hace legible la auditoría sin perder
  el contrato estructurado.

## Siguiente fase

F2: API HTTPS versionada, autenticación provisional, idempotencia, eventos y runner remoto
pull con claim compare-and-swap. No se implementará A2A ni integración broker en esta fase.
