from PIL import Image, ImageDraw, ImageFont
from flask import Flask, Response, render_template, request, session, redirect, url_for
import wifi_qrcode_generator.generator
import qrcode
import csv
import os
import io
import requests


HOST = os.getenv("HOST", "0.0.0.0")
PORT = os.getenv("PORT", "8080")
FONT = os.getenv("FONT", "Monaco")
CSV_LIST = os.getenv("CSV_LIST", "attendees.csv")
API_KEY = os.getenv("API_KEY")
EVENT_ID = os.getenv("EVENT_ID")
WIFI_ID = os.getenv("WIFI_ID")
WIFI_PASSWORD = os.getenv("WIFI_PASSWORD")
WIFI_AUTH = os.getenv("WIFI_AUTH", "WPA")
DEBUG = os.getenv("DEBUG")


def make_qrcode(url):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=6,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    return img

def find_font_size(text, font, image, target_width_ratio):
    tested_font_size = 100
    tested_font = ImageFont.truetype(font, tested_font_size)
    observed_width = tested_font.getlength(text)
    estimated_font_size = tested_font_size / (observed_width / image.width) * target_width_ratio
    return round(estimated_font_size)

def generate_badge(data, template_path="./template.png", font=FONT):
    badge = Image.open(template_path)
    draw = ImageDraw.Draw(badge)
    width, height = badge.size

    for pos, txt, maxsize, ratio in [
        ((100, 100), data['name'], 60, 0.5),
        ((100, 200), data['company'], 40, 0.6),
        ((100, 300), data['title'], 40, 0.8),
    ]:
        draw.text(pos, txt, font=ImageFont.truetype(font, min(maxsize, find_font_size(txt, font, badge, ratio))), fill="white")
    
    if data["linkedin"]:
        qr = make_qrcode(data["linkedin"])
        qr_w, qr_h = qr.size
        badge.paste(qr.get_image(), (width - qr_w - 5, 5))
    
    if WIFI_ID:
        qrwifi = wifi_qrcode_generator.generator.wifi_qrcode(
            ssid=WIFI_ID, 
            hidden=False,
            authentication_type=WIFI_AUTH,
            password=WIFI_PASSWORD,
        )
        qr = qrwifi.make_image().get_image()
        qr = qr.resize((175,175))
        qr_w, qr_h = qr.size
        badge.paste(qr, (5, height - qr_h - 5))
        draw.text((5, height - qr_h - 30), "wifi:", font=ImageFont.truetype(font, 20), fill="white")

    return badge

def caps(text):
    return " ".join(w.capitalize() for w in text.split())

def extract_linkedin(data):
    linkedin = ""
    for key, val in data.items():
        if "linkedin" in key.lower():
            linkedin = val.lower()
            break
    # normalise
    if linkedin in [
        "none",
    ]:
        linkedin = ""
    else:
        for prefix in [
            "https://",
        ]:
            if not linkedin.startswith(prefix):
                linkedin = prefix + linkedin
        if "/" not in linkedin:
            linkedin = "https://www.linkedin.com/in/" + linkedin
    return linkedin

def generate_all_badges(db, output_path="./badges"):
    print(f">> Generating badges from database")
    for data in db.values():
        output_filename = f"{output_path}/{data['name'].replace(' ', '_').lower()}.png"
        badge = generate_badge(data)
        badge.save(output_filename)
    print(f"Generated {len(db)} badges")

def read_guests_csv(csv_path):
    print(f">> Reading csv from {csv_path}")
    output = {}
    with open(csv_path, 'r') as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row["approval_status"] != "approved":
                continue
            data = {
                "name": caps(row["name"]),
                "email": row["email"],
                "title": row["Job Title"],
                "company": row["Company"],
                "linkedin": extract_linkedin(row),
            }
            output[data["email"]] = data
            if DEBUG:
                print(data)
    print(f"Read {len(output)} attendees")
    return output

def luma_get_all(path, api_key):
    print(f">> Downloading all for {path}")
    next_cursor = None
    headers = {
        "accept": "application/json",
        "x-luma-api-key": api_key,
    }
    output = []
    while True:
        url = f"https://api.lu.ma/{path}"
        if next_cursor:
            url += f"&pagination_cursor={next_cursor}"
        response = requests.get(url, headers=headers).json()
        for row in response["entries"]:
            output.append(row)
            if DEBUG:
                print(row)
        print(f"Downloaded {len(output)} items from {url}")
        next_cursor = response.get("next_cursor")
        if not next_cursor:
            break
    print(f"Done downloading guests")
    return output

def download_all_guests(event_id, api_key):
    print(f">> Downloading all guests for event {event_id}")
    db = {}
    path = f"public/v1/event/get-guests?event_api_id={event_id}&approval_status=approved"
    rows = luma_get_all(path, api_key)
    for row in rows:
        row = row["guest"] # blah
        transpose = {
            entry["label"]: entry["answer"]
            for entry in row["registration_answers"]
        }
        data = {
            "name": caps(row["name"]),
            "email": row["email"],
            "title": transpose["Job Title"],
            "company": transpose["Company"],
            "linkedin": extract_linkedin(transpose),
        }
        db[data["email"]] = data
        if DEBUG:
            print(data)
    return db

def download_all_events(api_key):
    print(f">> Downloading all events")
    db = {}
    path = f"public/v1/calendar/list-events?sort_direction=desc&sort_column=start_at"
    rows = luma_get_all(path, api_key)
    return rows

app = Flask(__name__)
app.secret_key = "badgerbadgerbadgermushroommushroom"
app.db = dict()

def serve_image(image):
    img_io = io.BytesIO()
    image.save(img_io, 'PNG')
    img_io.seek(0)
    return Response(img_io.read(), mimetype='image/png')

@app.route('/')
def index():
    key = session.get("key")
    if key:
        events = download_all_events(key)
        app.db["__events"] = {
            event.get("api_id") : event
            for event in events
        }
        return render_template('events.html', events=events)
    else:
        return render_template('login.html')

@app.route('/luma', methods=['POST'])
def luma_check():
    key = request.form.get('key')
    print(key)
    events = download_all_events(key)
    session["key"] = key
    return redirect("/")

@app.route('/logout')
def logout():
    session["key"] = None
    return redirect("/")

@app.route('/event/<event>')
def list_event(event):
    key = session.get("key")
    if key:
        event_data = app.db["__events"].get(event)
        app.db[event] = download_all_guests(event, key)
        return render_template('guests.html', db=app.db[event], event=event_data, event_id=event)
    return redirect("/")

@app.route('/<event>/<email>')
def make_badge(event, email):
    data = app.db.get(event, {}).get(email)
    if not data:
        return "not found", 404
    image = generate_badge(data)
    return serve_image(image)

@app.route('/submit', methods=['POST'])
def submit():
    data = {
        'name': request.form.get('name'),
        'email': request.form.get('email'),
        'title': request.form.get('title'),
        'company': request.form.get('company'),
        'linkedin': request.form.get('linkedin')
    }
    image = generate_badge(data)
    return serve_image(image)

if __name__ == '__main__':
    app.run(host=HOST, port=int(PORT))