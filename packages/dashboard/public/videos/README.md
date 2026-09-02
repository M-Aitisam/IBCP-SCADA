# Hero video assets

`HeroVideo.tsx` looks for these files. **All are optional** — with none of them
present the hero renders its designed CSS backdrop and looks finished. Drop the
files in and the component picks them up with no code change.

| File | Purpose | Required |
|---|---|---|
| `geovision-hero.mp4` | primary source (H.264, broadest support) | no |
| `geovision-hero.webm` | smaller at equal quality where supported | no |
| `geovision-hero-poster.jpg` | first frame, shown while buffering | no |

## What the footage should show

Relevant to the platform, not generic stock:

- Earth observed from orbit, ideally South/Central Asia
- satellite imagery sweeps, orbital passes
- river systems, the Indus basin, flood plains
- agricultural land, irrigation patterns, vegetation
- cloud and weather movement
- slow orbital drift or a gentle push-in

Avoid: people, offices, generic "technology" abstractions, fast cuts, anything
with burned-in branding or captions.

## Encoding

Aim for **under 6 MB**. This is a decorative background behind a dark scrim —
it is heavily obscured, so bitrate spent on fine detail is wasted.

```bash
# 1080p H.264, ~10s loop, no audio track at all
ffmpeg -i source.mp4 -t 10 -an \
  -vf "scale=1920:-2,fps=24" \
  -c:v libx264 -crf 30 -preset slow -movflags +faststart \
  geovision-hero.mp4

# WebM / VP9
ffmpeg -i source.mp4 -t 10 -an \
  -vf "scale=1920:-2,fps=24" \
  -c:v libvpx-vp9 -crf 38 -b:v 0 \
  geovision-hero.webm

# Poster from the first frame
ffmpeg -i geovision-hero.mp4 -vframes 1 -q:v 4 geovision-hero-poster.jpg
```

`-movflags +faststart` matters: it moves the MP4 index to the front of the file
so playback can begin before the whole thing has downloaded.

`-an` strips audio. The video is muted in the DOM anyway, and an audio track is
bytes that can never be heard.

## When the video does NOT play

By design, in all of these cases, leaving the CSS backdrop visible:

- `prefers-reduced-motion: reduce`
- viewport narrower than 768px
- `navigator.connection.saveData`, or a 2g effective connection
- autoplay refused by the browser (iOS low-power mode)
- files missing or failing to decode

It also pauses when the hero scrolls out of view, via IntersectionObserver.

## Sourcing

Public-domain sources suitable for this footage:

- NASA Scientific Visualization Studio — https://svs.gsfc.nasa.gov/
- NASA Earth Observatory — https://earthobservatory.nasa.gov/
- ESA Copernicus / Sentinel Hub — https://www.esa.int/

Check the licence for each clip before shipping it.
