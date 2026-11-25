# AWTRIX Stockholm dashboard

A small service that pushes one static card to an AWTRIX NG display. It shows the next citybound metro's minutes (at most two digits) in the original small font on the left, with each character in a different rainbow color. It skips every departure five minutes away or closer and shows the first one beyond that cutoff, when available, avoiding an impractically short countdown. The current temperature, including negative values and `°C`, sits on the right in the same font. Both stay vertically centered over animated rain, snow, sun, cloud, or night weather backgrounds. The temperature is blue for rain, white for snow, gray for cloud, yellow for sun, and pale blue at night.

It uses SMHI's open SNOW forecast and SL's public, keyless SL Transport API. The service runs continuously and refreshes the cards every minute. No Trafiklab API key is needed for this SL endpoint.

## Start with Docker

1. Copy `.env.example` to `.env`.
2. Set `AWTRIX_URL` to the base URL of your display (for example `http://awtrixng-a1b2c3.local`).
3. Run `docker compose up -d --build`.

The service uses SL site ID `9106` for Islandstorget, fetches forecast data for its coordinates, and creates one app named `StockholmDashboard` on AWTRIX. It removes the previous `StockholmWeather` and `SLIslandstorget` apps, then leaves only the dashboard enabled in the display rotation. Other apps are disabled in rotation, not deleted. Pushed apps are stored in memory by AWTRIX and disappear if the display restarts; this service republishes the dashboard on its next poll.

## Run without Docker

Requires Python 3.11 or newer. Copy `.env.example` to `.env`, export the values from it in your shell, then run:

```sh
set -a
. ./.env
set +a
python3 app.py
```

The program uses only the Python standard library.

Running directly on the host is the most reliable option when AWTRIX is on your local network. Docker Desktop may isolate containers from local LAN devices; if the container logs connection errors for the display, use the direct host run above.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `AWTRIX_URL` | required | Display base URL, without a trailing slash |
| `POLL_SECONDS` | `60` | Refresh interval; avoid polling the public APIs excessively |
| `SL_SITE_NAME` | `Islandstorget` | Station name shown in service logs |
| `SL_SITE_ID` | `9106` | Islandstorget site ID used by SL's departures endpoint |
| `SL_CITYBOUND_DESTINATIONS` | `Skarpnäck,Farsta strand,Hagsätra` | Comma-separated destination names to include |
| `SL_METRO_LINES` | `17,18,19` | Comma-separated green-line designations to include |
| `TRAIN_SKIP_MINUTES` | `5` | Skip trains with this many minutes or less remaining; select the first later departure |
| `SMHI_LAT` | `59.3464` | Forecast latitude; defaults to Islandstorget |
| `SMHI_LON` | `17.8934` | Forecast longitude; defaults to Islandstorget |
| `HTTP_TIMEOUT` | `15` | Timeout in seconds for each HTTP request |

To use another stop, set `SL_SITE_NAME`, `SL_SITE_ID`, `SL_METRO_LINES`, and `SL_CITYBOUND_DESTINATIONS` in `.env`. SL reports each train's end destination rather than each intermediate station, so list the citybound termini that pass your target station. To change where the weather is measured, set `SMHI_LAT` and `SMHI_LON` to that location's coordinates. The station and weather location can be different.

## Display and attribution

The card is drawn in fixed positions on the 32×8 matrix, so it does not scroll. Animated 32×8 GIFs fill the background with rain, snow, clouds, sunlight, or stars at night. Daytime sun is shown only when the sun is above the horizon at the configured weather coordinates. The departure marker uses SL's expected time and falls back to its scheduled time.

Add a small “Data: SMHI / Trafiklab” attribution to your display setup if you make this dashboard public. SMHI requests attribution where possible; Trafiklab asks that public displays credit Trafiklab.se.

## Sharing

This repository is the Python/Docker version and needs a computer or server on the same network as the clock. The AWTRIX Hub publishes Berry scripts that run directly on the clock, so listing this project there would require a separate Berry port. Hub scripts can expose user-editable values such as station ID, skip minutes, and weather coordinates with `@config` fields. See the [AWTRIX script sharing guide](https://blueforcer.github.io/awtrix-ng/tc002/guides/scripting/sharing/) for its metadata and submission steps.

## Troubleshooting

- Check `docker compose logs -f` for API or display errors.
- Confirm the display is reachable from the machine running the service.
- If no train appears, check the raw SL departure destination names and set `SL_CITYBOUND_DESTINATIONS` to match. The filter accepts green metro lines 17, 18, and 19 by default.
- If a weather field is unavailable, the card reports that forecast data is unavailable rather than inventing a value.

## Data sources

- [AWTRIX NG pushed apps](https://blueforcer.github.io/awtrix-ng/esp32/guides/pushed-apps/)
- [SL Transport API](https://www.trafiklab.se/api/our-apis/sl/transport/)
- [SMHI meteorological forecast API](https://www.smhi.se/data/sok-oppna-data-i-utforskaren/meteorologisk-prognos-api)
