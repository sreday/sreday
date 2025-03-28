# SREday

In-person conferences for Site Reliability, DevOps and Cloud engineers.


## Running locally

```sh
# if needed
make env
source env/bin/activate

make deps

# builds all years
make all

# runs a small script to serve the pages with python
make serve
```

## Adding a new conference

1. Copy over the template to a new folder
    1. The name needs to follow the pattern `YYYY-location-qX`
    1. Let's say we add `2026-tokyo-q1`
1. Modify the `2026-tokyo-q1/metadata.yaml` file:
    1. Update the location, time, date
    1. Update the `2026-tokyo-q1/_db/talks.csv` file
1. Add the conference to the home page
    1. Upload the splash screen
        1. Put it in `assets/images/events/sreday-2026-tokyo-q1.jpeg`
    1. Modify the `home/metadata.yaml` file:
        1. Add a new item to the events list
        1. Make sure the url matches the format, e.g `2026-tokyo-q1`

```yaml
events:

  - name: SREday Tokyo 2026 Q1
    location: Tokyo, Japan
    photo_url: ./assets/images/events/sreday-2026-tokyo-q1.jpeg
    url: ./2026-tokyo-q1/
...
```