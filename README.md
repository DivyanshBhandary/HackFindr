---
title: HackFindr
emoji: 🎓
colorFrom: purple
colorTo: pink
sdk: gradio
sdk_version: 6.30.0
python_version: "3.12"
app_file: app.py
pinned: false
license: mit
short_description: Find student events near you with Gemma 4
---

# HackFindr: built with Gemma 4

HackFindr finds upcoming student events (hackathons, ideathons, webinars, workshops, competitions, conferences,
quizzes, coding contests) from **Unstop, Devpost, Devfolio, KnowAFest, Hack2skill, HackerEarth, Codeforces and MLH**, filters them by distance from your city, and shows them on a map.

It is built with **Gemma 4 (`gemma-4-26b-a4b-it`) through the Gemini API** (`google-genai`). Gemma:
- summarizes each Unstop event page into a short description, eligibility and requirements,
- reads a photo of an **event poster** and adds that event to the map (the demo highlight),
- answers questions about an event in a small **chat** on its details page.

![screenshot](docs/screenshot.png)

Presentation: [docs/HackFindr_presentation.pptx](docs/HackFindr_presentation.pptx)

## Features
**Find events**
- **Map and list side by side.** The map stays in view while you scroll the event list. Pins are coloured by type
  (hackathon, workshop, competition, conference) and numbered when several events share a place.
- **Your city or Near me.** Type a city or use your location, then pick a radius from 50 to 2000 km.
  Online events are listed too.
- **One-tap filters:** Saved, Hackathons, Workshops, Free, This week and Open to 1st year.
- **Search by title** and sort by Nearest or Closing soon.
- **At a glance:** counts of nearby, online, free and closing-this-week events, plus a *Closing this week* strip
  and red badges when registration closes soon.
- Events that have ended are hidden automatically.

**Event details**
- Opens straight away with dates, place, team size and a **Register** button for the official page.
  The AI summary, eligibility and requirements load in a moment later.
- **Save** events with a star (kept in your browser), **add to calendar** (.ics) and **copy a link** to one event.
- **Gemma helpers:** ask questions about the event in a chat, an **"Is this for me?"** check from your year and skills,
  a **team-invite message** ready for WhatsApp, and a side-by-side **compare** with another event.

**Add events**
- **Add from poster:** upload a photo of a notice-board poster, Gemma fills in the details, you check them and add
  the event to the map.

Works on laptops and phones, in day and night mode.

## How to run
```
python -m venv .venv
.venv\Scripts\activate            # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # then put your GEMINI_API_KEY in .env
python events.py                  # optional: fetch fresh events into events.json
python app.py                     # open http://127.0.0.1:7860
```
To open it from a phone or laptop on the same Wi-Fi, start it with `python app.py --lan`
and open the second address it prints (like `http://10.50.33.81:7860`). Allow Python through the Windows firewall if asked.

If the internet or an API is down, the app uses the saved `events.json` (offline demo mode).

## Website mode
Set `HACKFINDR_PUBLIC=1` (and `GEMINI_API_KEY` as a secret) on the host. This hides manual fetching, refreshes
events in the background every 6 hours, and limits Gemma use per visitor and per day. Set `HACKFINDR_DATA_DIR` to
a persistent folder if the host has one; otherwise new summaries and poster events reset when the server restarts.

## How it works
| File | What it does |
|---|---|
| `events.py` | fetch every source in `SOURCES` (in website mode, auto-refresh every `REFRESH_HOURS`), normalize to one schema, geocode cities (cached in `geocache.json`), Gemma summaries (cached in `summary_cache.json`), save `events.json` |
| `harness/tools.py` | event search and distance filter used by the map |
| `app.py` | starts `app_template/app.py` (hosting services such as Hugging Face Spaces run this file) |
| `app_template/app.py` | Gradio UI: fixed folium map beside the scrolling event list, filters, details panel with AI chat, fit check, team message and compare, poster upload |
| `gemma_client.py` | the starter's Gemma 4 helper (spacing, retries, cache) |
| `skills/hackfindr/SKILL.md` | agent skill describing how to use the tools |

Distance is the straight-line (haversine) distance from the city you type.
Geocoding uses OpenStreetMap Nominatim at most once per second.

Built on the Gemma 4 hackathon starter.

## License
MIT, see `LICENSE`.
