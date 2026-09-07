# Calendario del Real Madrid

Feed iCalendar de partidos oficiales del primer equipo masculino. Compatible con Google Calendar y Outlook.

## Archivos públicos

- Calendario: `https://aleafe21.github.io/real-madrid-calendario/real-madrid.ics`
- Estado: `https://aleafe21.github.io/real-madrid-calendario/status.json`
- Página: `https://aleafe21.github.io/real-madrid-calendario/`

## Actualización local

```powershell
python scripts/update_calendar.py --check
python scripts/update_calendar.py --update
python -m unittest discover -s tests -v
```

`--check` consulta la fuente y muestra diferencias sin escribir. `--update` genera `site/real-madrid.ics` y `site/status.json`.

## Publicación inicial

1. Crear repositorio público `aleafe21/real-madrid-calendario`.
2. Subir la rama `main`.
3. Abrir **Actions → Actualizar calendario → Run workflow**.
4. Si GitHub no habilita Pages automáticamente, seleccionar **Settings → Pages → Source: GitHub Actions** y repetir el workflow.

El workflow consulta el calendario diariamente a las 10:17 UTC y también admite ejecución manual. GitHub puede desactivar workflows programados en repositorios públicos sin actividad durante 60 días; en ese caso, reactivarlo desde **Actions**.

## Fuente y reglas

- Fuente: feed iCalendar oficial del Real Madrid.
- Se excluyen amistosos y entrenamientos según `DESCRIPTION` o `CATEGORIES`.
- Horario `00:00 UTC` sin información adicional se interpreta como fecha provisional y se publica como día completo.
- Eventos confirmados duran dos horas cuando la fuente no informa finalización.
- Se eliminan alarmas del origen; cada usuario configura notificaciones en su calendario.
