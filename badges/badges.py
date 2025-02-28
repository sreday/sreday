from PIL import Image, ImageDraw, ImageFont
from flask import Flask, Response
import wifi_qrcode_generator.generator
import qrcode
import csv
import os
import io
import requests


HOST = os.getenv("HOST", "0.0.0.0")
PORT = os.getenv("PORT", "8080")
CSV_LIST = os.getenv("CSV_LIST", "attendees.csv")
API_KEY = os.getenv("API_KEY")
EVENT_ID = os.getenv("EVENT_ID")
WIFI_ID = os.getenv("WIFI_ID")
WIFI_PASSWORD = os.getenv("WIFI_PASSWORD")
WIFI_AUTH = os.getenv("WIFI_AUTH", "WPA")


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

def generate_badge(data, template_path="./template.png"):
    badge = Image.open(template_path)
    draw = ImageDraw.Draw(badge)
    width, height = badge.size

    font= "Monaco.ttf"
    
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
    
    if WIFI_ID and WIFI_PASSWORD:
        qrwifi = wifi_qrcode_generator.generator.wifi_qrcode(
            ssid=WIFI_ID, 
            hidden=False,
            authentication_type=WIFI_AUTH,
            password=WIFI_PASSWORD,
        )
        qr = qrwifi.make_image().get_image()
        qr = qr.resize((175,175))
        qr_w, qr_h = qr.size
        badge.paste(qr, (width - qr_w - 5, height - qr_h - 5))
        draw.text((width - qr_w, height - qr_h - 30), "wifi:", font=ImageFont.truetype(font, 20), fill="white")

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


def generate_all_badges(csv_path, iqrwifi=None, output_path="./badges"):
    counter = 0
    db = {}
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
            db[data["email"]] = data
            print(data)
            output_filename = f"./badges/{row['name'].replace(' ', '_').lower()}.png"
            bg = generate_badge(data)
            bg.save(output_filename)
            counter += 1
    print(f"Generated {counter} badges")
    return db

def download_all_guests(api_key, event_id):
    next_cursor = None
    headers = {
        "accept": "application/json",
        "x-luma-api-key": api_key,
    }
    db = {}
    while True:
        url = f"https://api.lu.ma/public/v1/event/get-guests?event_api_id={event_id}&approval_status=approved"
        if next_cursor:
            url += f"&pagination_cursor={next_cursor}"
        response = requests.get(url, headers=headers).json()
        for row in response["entries"]:
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
            print(data)
        print(f"Downloaded {len(db)} users from {url}")
        next_cursor = response.get("next_cursor")
        if not next_cursor:
            break
    return db

app = Flask(__name__)
db = {}

def serve_image(image):
    img_io = io.BytesIO()
    image.save(img_io, 'PNG')
    img_io.seek(0)
    return Response(img_io.read(), mimetype='image/png')

@app.route('/<email>')
def make_badge(email):
    data = db.get(email)
    if not data and API_KEY and EVENT_ID:
        db.update(download_all_guests(API_KEY, EVENT_ID))
    data = db.get(email)
    if not data:
        return Flask.abort(404)
    image = generate_badge(data)
    return serve_image(image)

@app.route('/refresh/<email>')
def dwmake_badge(email):
    db.update(download_all_guests(API_KEY, EVENT_ID))
    data = db.get(email)
    if not data:
        return Flask.abort(404)
    image = generate_badge(data)
    return serve_image(image)


if __name__ == '__main__':
    if CSV_LIST and os.path.exists(CSV_LIST):
        db = generate_all_badges(CSV_LIST)
    if API_KEY and EVENT_ID: 
        db.update(download_all_guests(API_KEY, EVENT_ID))
    app.run(host=HOST, port=int(PORT))