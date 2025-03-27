# Badge printer

This generates badge images for printing at the conference.

It can work in two modes:
* pre-generate all badges
* generate the badge images on the fly through an HTTP server

Configuration:
* take a .csv (`CSV_LIST`) in the format exported from lu.ma and generate images for every attendee in `./badges/${name}.png`
* if present (`API_KEY` and `EVENT_ID`) it will connect to lu.ma to list all attendees
* if present, it will add a QR code for LinkedIn so that people can scan easily
* if present (`WIFI_ID`, `WIFI_PASSWORD` and `WIFI_AUTH`) it will add a QR code to connect to the wifi
* `HOST` defaults to 0.0.0.0 and `PORT` to 8080

The badges are generated like this:
* take the template from `template.png`
* use these dimensions
* try to fit the name, company and job title
* place the QR code for LinkedIn in top right corner
* place the QR code for Wifi in the bottom right corner


## MVP

1. Export the attendees info to csv from luma to `attendees.csv`
1. Prep the script

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

1. Generate the badges

```sh
# set any env vars like above
python badges.py
```

1. Print all the badges

```sh
bash print-all.sh
```