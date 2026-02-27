# E-Ink Display

A Raspberry Pi e-ink display system for a Waveshare 7.3" color e-paper (800×480). Displays weather radar, daily facts/questions, saint of the day, Wikipedia image of the day, or a custom movie/image slideshow. Controlled via a local web dashboard.

## Hardware

- Waveshare 7.3" ACeP 7-Color E-Paper (800×480)
- Raspberry Pi (any model with GPIO)

## Modules

| Module | Description |
|---|---|
| `weather` | NWS radar image for a configured station with forecast fallback |
| `text` | Rotating fun facts and conversation questions from local CSVs |
| `saint_of_day` | Saint of the Day scraped from franciscanmedia.org with portrait |
| `wiki_image` | Wikipedia Picture of the Day with caption overlay |
| `movie_slideshow` | Sequential image frames from a local folder, one per refresh |

## Setup

**Install dependencies:**
```bash
poetry install
```

**Run once:**
```bash
poetry run python main.py
```

**Start the web dashboard:**
```bash
poetry run python server/app.py
```
Then open `http://<pi-hostname>.local:5000` from any device on the network.

> On macOS, port 5000 may conflict with AirPlay Receiver. Disable it in System Settings → General → AirDrop & Handoff, or change the port in `server/app.py`.

## Configuration

All settings live in `config.yml`.

**Switch modules:**
```yaml
active_module: weather   # weather | text | saint_of_day | wiki_image | movie_slideshow
update_interval: 300     # seconds between auto-refreshes
```

**Weather station:**
```yaml
station:
  name: KOHX
  location: Nashville, TN
  zone_code: tn
```
Any NEXRAD station ID works. See the full list in `config.yml`.

**Movie slideshow:**
```yaml
movie_slideshow:
  movies_dir: data/movies
  active_movie: steamboat_willie   # folder name inside data/movies/
  output_path: movie_display.bmp
```
Drop `.jpg`, `.png`, or `.bmp` frames into `data/movies/<name>/`. Frames advance alphabetically on each refresh.

## Web Dashboard

The Flask server at port 5000 lets you:

- Switch the active module instantly
- Trigger a manual refresh
- Preview the current output image
- Adjust the update interval
- Manage movie libraries (upload a ZIP of frames)
- Edit any config value via dot-notation key/value fields

## Scheduling (Raspberry Pi)

Add a cron job to auto-refresh at the configured interval:
```bash
crontab -e
```
```
*/5 * * * * cd /home/pi/eink && poetry run python main.py >> /tmp/eink.log 2>&1
```

Or use a systemd timer for more control.

## Project Structure

```
eink/
├── main.py                    # Dispatcher: reads active_module, calls generate()
├── config.yml                 # All configuration
├── display.py                 # Hardware abstraction (Linux only)
├── modules/
│   ├── weather.py             # NWS radar + forecast
│   ├── text_display.py        # Facts and questions from CSV
│   ├── saint_of_day.py        # Saint of the Day (scraped, cached daily)
│   ├── wiki_image.py          # Wikipedia Picture of the Day (cached daily)
│   ├── movie_slideshow.py     # Sequential image frame player
│   ├── forecast.py            # Detailed NWS forecast helper
│   └── special_weather.py     # NWS special weather message scraper
├── server/
│   ├── app.py                 # Flask web server (port 5000)
│   └── templates/index.html   # Web dashboard UI
├── data/
│   ├── questions/             # CSVs for facts and conversation questions
│   └── movies/                # Movie frame directories
└── waveshare_epd/             # Waveshare driver library
```

## Dependencies

- `pillow` — image generation
- `requests` — HTTP fetching
- `flask` — web dashboard
- `beautifulsoup4` + `lxml` — HTML scraping (saint module)
- `pyyaml` — config parsing
- `spidev`, `rpi-gpio` — hardware SPI/GPIO (Linux/Pi only)
