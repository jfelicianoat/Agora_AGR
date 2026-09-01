# RESULTADO DE FASE — F2 API segura y runners remotos

Fecha: 2026-09-01  
Rama: `codex/agora-f0`  
Estado: completada técnicamente; no existe puerta humana entre F2 y F3.

## Implementado

- API FastAPI versionada bajo `/api/v1` con health, BOARD, CARD, perfiles, eventos, work,
  create, claim, progress, close, yield y unblock administrativo.
- HTTPS obligatorio tanto en servidor como cliente; el runner no admite una URL `http://`.
- Autenticación provisional Bearer con comparación constante y credencial sólo por entorno.
- `Idempotency-Key` obligatorio en todas las mutaciones, persistido atómicamente y válido
  después de reiniciar la API.
- Claim remoto compare-and-swap ejecutado por el dueño local del BOARD.
- Selección pull que sólo devuelve CARDs compatibles con los perfiles declarados.
- Transferencia de artefactos como bytes base64 con nombre simple y límite de tamaño; el
  servidor materializa y verifica los artefactos antes de cerrar.
- Runner genérico que no importa `Board` ni `Card` y nunca conoce rutas de `KANBAN`.
- Eventos durables de creación, claim, progreso, cierre, yield y desbloqueo.
- CLI TLS para servidor y runner; no imprime credenciales y desactiva access logs.

## Archivos

- `src/agora/api/{app,contracts,main,service,storage}.py`
- `src/agora/remote/{client,main,runner}.py`
- `src/agora/documents.py`: escritura atómica binaria.
- `src/agora/board.py`: transición de desbloqueo del dominio.
- `tests/test_api.py`
- `tests/test_remote_runner.py`
- `tests/test_tls.py`
- `README.md` y `pyproject.toml`

## Tests

Comando final:

```powershell
python -m pytest -q --basetemp=tmp\pytest-f2-final
```

Resultado: **47 passed in 2.97s**.

Cobertura de las diez pruebas F2 obligatorias:

1. un runner obtiene sólo CARDs compatibles con sus PROFILE;
2. dos claims remotos concurrentes producen un ganador y un 409;
3. una respuesta de claim perdida se reintenta con la misma clave sin segundo claim;
4. create repetido y reinicio de API no duplican CARD;
5. una CA incorrecta es rechazada por un handshake TLS real y por HTTPX;
6. traversal en CARD y en nombre de artefacto es rechazado;
7. runner offline deja la CARD en pending;
8. el mismo runner reconectado reclama y completa;
9. una escritura sin autenticación no modifica el BOARD;
10. análisis AST confirma que cliente y runner no importan BOARD/CARD.

Pruebas adicionales: origen falsificado rechazado, identidad derivada del principal,
conflicto por reutilización de clave, HTTP plano rechazado, progreso/Record/eventos,
artefacto real, yield sin intento y unblock mediante transición de dominio.

## Prueba real de transporte

Se levantó Uvicorn sobre `https://127.0.0.1:18741` con una CA y certificado efímeros de
prueba. Un `AgoraApiClient` configurado con esa CA obtuvo:

```text
{'status': 'ok', 'api': 'v1', 'board_owner': 'agora'}
{'status': 'completed', 'card': 'live.md', 'detail': ''}
```

La misma conexión, confiando en una CA distinta, falló antes de HTTP con:

```text
httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED]
unable to get local issuer certificate
```

El servidor se detuvo después de la prueba. Se usó una credencial efímera de ensayo, no el
token de AI_Broker.

## Verificado realmente

- Transporte TLS real, validación positiva y negativa de certificado.
- Pull, claim, progress, subida, close y Record completos a través de la API.
- Idempotencia durable entre recreaciones de la aplicación.
- Carrera de claims desde clientes HTTP concurrentes.
- Offline/reconexión sin pérdida de CARD.
- El cliente remoto no accede directa ni indirectamente al BOARD.
- Eventos y artefactos quedan en el PC principal mediante servicios autorizados.

## No verificado

- Comunicación entre dos máquinas físicas: la prueba TLS fue loopback real en el PC
  principal; no se abrieron puertos ni se modificó firewall.
- Certificado emitido por la PKI definitiva del usuario.
- Resistencia a caída de proceso exactamente entre la mutación y la persistencia de su
  respuesta idempotente; create y claim son recuperables por estado, pero falta un journal
  transaccional para cerrar por completo esa ventana extrema.
- OAuth y scopes: corresponden a F5; F2 usa una credencial provisional compartida.

## Deuda y riesgos

- El almacén JSON es adecuado para un único proceso API, pero F7 debería migrar eventos e
  idempotencia a una base transaccional si se habilitan múltiples procesos servidor.
- Los artefactos se reciben en memoria con límite de 25 MB; archivos grandes necesitarán
  streaming multipart en una evolución del contrato.
- El runner de referencia ejecuta una ronda. La supervisión continua y anuncios de
  disponibilidad se incorporan junto con los runners especializados.

## Decisiones descubiertas

- El origin remoto se deriva del principal autenticado y se guarda en `origin_identity`;
  el payload no puede declararlo.
- Cada mutación es idempotente, no sólo create, porque respuestas perdidas de claim/close
  son las que más riesgo tienen de duplicar ejecución o coste.
- Los artefactos cruzan como contenido, nunca como una ruta que el servidor deba confiar.
- La API reutiliza el core: no existe un segundo implementador de transiciones.

## Siguiente fase

F3: runner de IA y AI_Broker real, empezando por validar el ciclo del X-Admin-Token y una
adquisición exclusivamente local que no lo transporte al PC principal.
