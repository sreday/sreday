"""Offline test of the picture and sound passes (2026-11-27) - no Descript, no Whisper.

  env/bin/python tests/av_smoke.py          (Windows: env\\Scripts\\python.exe tests\\av_smoke.py)

Builds, with a known truth:
  - a 5-page deck PDF (pypdfium2) and a "screen share" video made from it: a page with a taskbar and a headshot box
    over it, a build step (last bullet not shown yet), the whole page, a live demo (a terminal - not in the deck),
    Presenter View (the page at 55% with notes beside it), a later page, and a slide that is NOT in the deck;
  - speech-like sound: the camera's copy muffled, echoing and noisy; a separate recorder's copy clean, starting 12 s
    into the video, its clock running 120 ppm fast;
then runs the factory itself on a fixture event: add (video + --sound), analyse (the sound placed on the video), the
picture plan, the cut (deck pages laid over the video, the recorder's sound laid in) - and checks the output file
frame by frame and sample by sample. Night mode: a warm copy of the screen share is detected and put right.
Saturated sound: the better track (a clipped recorder minute goes to the camera; a clipped channel gives way to the
clean one), the judge of the repair tools (stand-ins for the tools and Whisper - the real tools are tested on real
speech, see README), Studio Sound 100% only as the last resort; a limiter is not taken for clipping.
The temp folder is left in place for a look (its path is printed).
"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="live-factory-av-")
FAILS = []
W, H, FPS, LEN = 1280, 720, 10, 125.0
SR = 48000


def check(what, ok, detail=""):
    print("  %s %s%s" % ("ok  " if ok else "FAIL", what, (" - " + str(detail)) if detail else ""))
    if not ok:
        FAILS.append(what)


def ff(*args, inp=None):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + list(args), input=inp, capture_output=True)
    if r.returncode != 0:
        raise SystemExit("ffmpeg failed: " + r.stderr.decode("utf-8", "replace")[-400:])


# ---- the deck --------------------------------------------------------------------------------------------------------
TITLES = ["Reliability Without Heroics", "Three Signals That Matter", "Budgets Beat Alerts",
          "What We Changed First", "Results After Six Months"]


def page_image(i, bullets=3, title=None):
    import cv2
    img = np.full((720, 1280, 3), 250, np.uint8)
    cv2.rectangle(img, (0, 0), (18, 720), (40, 90, 200), -1)
    cv2.putText(img, title or TITLES[i], (70, 110), cv2.FONT_HERSHEY_DUPLEX, 1.9, (30, 30, 30), 3, cv2.LINE_AA)
    cv2.line(img, (70, 140), (1210, 140), (40, 90, 200), 4)
    for b in range(bullets):
        y = 260 + b * 110
        cv2.circle(img, (95, y - 12), 9, (40, 90, 200), -1)
        cv2.putText(img, "Point %d of slide %d: %s" % (b + 1, i + 1, ["latency", "errors", "saturation"][b]),
                    (130, y), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (50, 50, 50), 3, cv2.LINE_AA)
        cv2.rectangle(img, (900, y - 50), (900 + 60 * (i + b + 1) % 280 + 20, y + 10), (200, 120, 60), -1)
    cv2.putText(img, "TESTday Testcity 2026 Q4   |   %d" % (i + 1), (70, 690), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (120, 120, 120), 1, cv2.LINE_AA)
    return img                                                   # BGR


def write_pdf(pages, path):
    import cv2
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument.new()
    for k, img in enumerate(pages):
        jpg = os.path.join(TMP, "deckpage%d.jpg" % k)
        cv2.imwrite(jpg, img, [cv2.IMWRITE_JPEG_QUALITY, 97])
        page = pdf.new_page(960, 540)
        im = pdfium.PdfImage.new(pdf)
        im.load_jpeg(jpg)
        im.set_matrix(pdfium.PdfMatrix().scale(960, 540))
        page.insert_obj(im)
        page.gen_content()
    pdf.save(path)


# ---- the screen share --------------------------------------------------------------------------------------------------
def frame_at(t, deck):
    """(BGR frame, truth) - truth = (page or None, build?)"""
    import cv2
    rng = np.random.default_rng(int(t * 10))
    f = np.zeros((H, W, 3), np.uint8)

    def place(img, x, y, w, h):
        f[y:y + h, x:x + w] = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)

    def taskbar():
        f[H - 48:] = (40, 36, 32)
        for k in range(8):
            cv2.rectangle(f, (520 + 34 * k, H - 38), (544 + 34 * k, H - 12), (200, 180, 120), -1)

    if t < 20:
        place(deck[0], 0, 0, W, H)
        taskbar()
        f[470:660, 1010:1260] = rng.integers(60, 200, (190, 250, 3), dtype=np.uint8)   # the speaker's headshot
        return f, (0, False)
    if t < 30:
        place(page_image(1, bullets=2), 0, 0, W, H)
        taskbar()
        return f, (1, True)
    if t < 45:
        place(deck[1], 0, 0, W, H)
        taskbar()
        return f, (1, False)
    if t < 60:                                                           # a live demo: a terminal
        f[:] = (20, 20, 20)
        for k in range(16):
            cv2.putText(f, "$ kubectl get pods -n prod  # %d %s" % (k, "Running" if (k + int(t)) % 3 else "CrashLoop"),
                        (30, 50 + 40 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (90, 220, 90), 2)
        return f, (None, False)
    if t < 80:                                                           # Presenter View
        f[:] = (45, 45, 45)
        place(deck[2], 30, 60, 704, 396)
        cv2.putText(f, "Notes: remember the budget story", (770, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (230, 230, 230), 2)
        place(deck[3], 790, 420, 400, 225)
        return f, (2, False)
    if t < 95:
        place(deck[4], 0, 0, W, H)
        taskbar()
        return f, (4, False)
    if t < 110:
        place(page_image(3, title="Not In The Deck At All"), 0, 0, W, H)     # same template, another slide
        taskbar()
        return f, (None, False)
    # a camera in the room filming a small TV: perspective, blur, noise, a dim room around it
    f[:] = rng.integers(25, 70, (H, W, 3), dtype=np.uint8)
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = np.float32([[380, 170], [900, 205], [890, 500], [372, 470]])
    tv = cv2.warpPerspective(cv2.GaussianBlur(deck[3], (5, 5), 0), cv2.getPerspectiveTransform(src, dst), (W, H))
    mask = cv2.warpPerspective(np.full((H, W), 255, np.uint8), cv2.getPerspectiveTransform(src, dst), (W, H)) > 0
    f[mask] = (tv[mask] * 0.85 + 10).astype(np.uint8)
    f[:] = np.clip(f.astype(np.int16) + rng.integers(-6, 7, (H, W, 3)), 0, 255).astype(np.uint8)
    return f, (3, False)


def speechlike(seconds, seed=1):
    """Voiced, syllable-like sound: a gliding pitch (110-220 Hz) with its harmonics under formant-ish weights, in
    bursts - like speech, most of its energy below 3 kHz, something a loudness fingerprint can lock onto."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    out = np.zeros(n, np.float32)
    t = 0.0
    while t < seconds:
        d = rng.uniform(0.08, 0.3)
        a, b = int(t * SR), min(n, int((t + d) * SR))
        k = np.arange(b - a) / SR
        f0 = rng.uniform(110, 220) * (1 + 0.15 * k / max(d, 1e-3))
        phase = 2 * np.pi * np.cumsum(f0) / SR
        x = sum(np.sin(h * phase) / h * (1.6 if 400 < h * f0.mean() < 1200 else 1.0) for h in range(1, 18))
        out[a:b] = rng.uniform(0.2, 1.0) * np.hanning(b - a) * x * 0.12
        t += d + rng.uniform(0.02, 0.4) + (rng.uniform(0.5, 1.5) if rng.random() < 0.08 else 0)
    return out


def wav(path, x):
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    ff("-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-", path, inp=pcm.tobytes())


def build_media(deck, folder):
    import cv2
    clean = speechlike(LEN + 5)
    cam = os.path.join(folder, "cam_sound.wav")
    wav(cam, clean[:int(LEN * SR)])
    rec_from, rec_to, drift = 12.0, 115.0, 120e-6
    rec = clean[int(rec_from * SR):int(rec_to * SR)]
    n_out = int(len(rec) / (1 + drift))                                  # the recorder's clock runs fast: fewer samples
    rec = np.interp(np.linspace(0, len(rec) - 1, n_out), np.arange(len(rec)), rec).astype(np.float32)
    recorder = os.path.join(folder, "ZOOM0001.wav")
    wav(recorder, np.stack([rec, rec * 0.0]).T.ravel() if False else rec)
    video = os.path.join(folder, "SCREEN.mp4")
    truth = []
    p = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                          "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "-", "-i", cam,
                          "-af", "lowpass=f=2000,aecho=0.8:0.7:40|95|170:0.5|0.35|0.2,volume=0.8",
                          "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                          "-c:a", "aac", "-shortest", video], stdin=subprocess.PIPE)
    for k in range(int(LEN * FPS)):
        f, tr = frame_at(k / FPS, deck)
        truth.append(tr)
        p.stdin.write(f.tobytes())
    p.stdin.close()
    p.wait()
    warm = os.path.join(TMP, "SCREEN_warm.mp4")
    ff("-i", video, "-vf", "colorchannelmixer=rr=1:gg=0.85:bb=0.6", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
       "-an", warm)
    return video, warm, recorder, clean, (rec_from, rec_to, drift), truth


def fixture(root):
    ev = os.path.join(root, "testbrand", "2026-testcity-q4")
    os.makedirs(os.path.join(ev, "_db"))
    os.makedirs(os.path.join(ev, "slides"))
    with open(os.path.join(ev, "metadata.yml"), "w") as f:
        f.write('brand_name: "TESTday"\nbrand_domain: "testday.example"\nstart_time: "2026-11-05T09:00:00+00:00"\n'
                'city_name: "Testcity"\ndate_string: "November 5, 2026"\ndays: 1\nrooms:\n- main stage\n')
    import csv
    row = {"YouTube": "", "status": "talk", "name": "Alice Walker", "track": "1", "day": "1", "organization": "Acme",
           "title": "Reliability Without Heroics", "duration": "2", "abstract": "SLOs and error budgets."}
    with open(os.path.join(ev, "_db", "talks.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        w.writeheader()
        w.writerow(row)
    settings = os.path.join(root, "settings.av.yml")
    with open(settings, "w") as f:
        f.write("github_root: %s\nbrands:\n  testbrand: {repo: testbrand}\nwork_root: %s\npublish_status: false\n"
                "pad_before_seconds: 0\npad_after_seconds: 0\nzoom_black_bars: false\ntalk_min_minutes: 0.2\n"
                % (json.dumps(root), json.dumps(os.path.join(root, "work"))))
    return settings, ev, row


def pcm(path, start, seconds):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % start, "-t", "%.3f" % seconds, "-i", path, "-ac", "1",
                        "-ar", str(SR), "-f", "s16le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32767


def lag_ms(out, ref, reach=0.2):
    """Where ref sits in out (ms, + = out late) and how alike they are."""
    m = int(reach * SR)
    n = len(out) - 2 * m
    seg = out[m:m + n]
    c = np.correlate(ref[:n + 2 * m], seg, "valid")
    i = int(np.argmax(c))
    alike = float(c[i] / (np.linalg.norm(seg) * np.linalg.norm(ref[i:i + n]) + 1e-9))
    return (2 * m - i) * 1000.0 / SR, alike             # seg starts reach into out, and ref reach before it


def frame(path, t):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", path, "-frames:v", "1", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], capture_output=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(H, W, 3).astype(np.float32)


def main():
    print("LIVE factory picture + sound test in %s" % TMP)
    settings, evdir, row = fixture(TMP)
    os.environ["LIVE_FACTORY_SETTINGS"] = settings
    from live_factory import events, picture, pipeline
    why = picture.available()
    if why:
        raise SystemExit(why)
    deck = [page_image(i) for i in range(5)]
    talk_page = events.talk_url(row)
    deck_pdf = os.path.join(evdir, "slides", talk_page + ".pdf")
    write_pdf(deck, deck_pdf)
    media_dir = os.path.join(TMP, "card")
    os.makedirs(media_dir)
    video, warm, recorder, clean, (rec_from, rec_to, drift), truth = build_media(deck, media_dir)

    run = pipeline.Run("testbrand-2026-testcity-q4")
    ids = run.add([video], day=1)
    sids = run.add([recorder], day=1)
    check("add: the video, and the .wav as a separate sound", ids == ["r1"] and sids == ["r2"] and run.is_sound("r2"),
          (ids, sids))
    check("add: a folder takes only its videos (the .wav in it is not taken as a video)",
          run.add([media_dir], day=1) == [] and len(run.state["recordings"]) == 2)
    for rid in ("r1", "r2"):                                   # no Whisper here: an empty transcript stands in
        os.makedirs(run.rec_dir(rid), exist_ok=True)
        with open(os.path.join(run.rec_dir(rid), "transcript.json"), "w") as f:
            json.dump({"segments": []}, f)
        run.rset(rid, "transcript", "done", note="test")
    run.analyse(["r1", "r2"])
    st = (run.state["recordings"]["r2"]["steps"].get("sync") or {})
    sy = run.syncs().get("r2") or {}
    s0 = (sy.get("stretches") or [{}])[0]
    check("sync: the recorder placed on r1", st.get("status") == "done" and s0.get("rec") == "r1", st.get("note"))
    if s0:
        check("sync: where it starts (12 s, within 20 ms)", abs(s0["shift"] - rec_from) < 0.02, "%.3f s" % s0["shift"])
        check("sync: the drift (+120 ppm, within 40)", abs((s0["scale"] - 1) * 1e6 - 120) < 40, "%+.0f ppm" % ((s0["scale"] - 1) * 1e6))
    check("sound recordings stay out of the lineup match", [r for r, _ in __import__("live_factory.segment", fromlist=["x"])
                                                            .track_recordings(run.state, "1")] == ["r1"])

    # the picture plan, through the factory
    run.save_cuts({"cuts": [{"key": "Alice Walker", "parts": [{"rec": "r1", "start": 0, "end": LEN - 0.5}],
                             "approved": True, "confidence": "high", "reason": "test"}], "skipped": []})
    cut = run.cuts()["cuts"][0]
    plans, deck_found, notes = run.picture_for("Alice Walker", cut, cut["parts"])
    check("deck found in the event's slides/", deck_found and os.path.samefile(deck_found, deck_pdf), deck_found)
    plan = plans[0]
    got = {}
    for k in range(int((LEN - 0.5) * FPS)):
        t = k / FPS
        seg = next((s for s in plan["pages"] if s["t0"] <= t < s["t1"]), None)
        got[k] = (seg["page"], bool(seg["hidden"])) if seg else (None, False)
    wrong = [k / FPS for k in got if got[k][0] is not None and got[k][0] != truth[k][0]]
    early = [k / FPS for k in got if truth[k][1] and got[k][0] == truth[k][0] and not got[k][1]]
    missed = [k / FPS for k in got if truth[k][0] is not None and got[k][0] is None]
    # within 1 s of a change the plan may lag by its sampling - only count the steady parts
    changes = [k / FPS for k in range(1, len(truth)) if truth[k] != truth[k - 1]]
    steady = lambda ts: [t for t in ts if all(abs(t - c) > 1.0 for c in changes)]
    check("picture: never a wrong deck page", not steady(wrong), steady(wrong)[:5])
    check("picture: the build step is never shown whole", not steady(early), steady(early)[:5])
    check("picture: the demo and the slide not in the deck keep the video",
          not [t for t in steady(wrong)] and all(got[k][0] is None for k in got if truth[k][0] is None and
                                                 all(abs(k / FPS - c) > 1.0 for c in changes)))
    check("picture: every deck page found (taskbar, headshot, Presenter View)", len(steady(missed)) < 0.05 * len(got),
          "%d of %d frames missed: %s" % (len(steady(missed)), len(got), steady(missed)[:4]))
    print("     plan: " + "; ".join("%.1f-%.1f p%d%s" % (s["t0"], s["t1"], s["page"] + 1, " (build)" if s["hidden"] else "")
                                   for s in plan["pages"]))

    # the cut itself (it stops at the Descript upload: there is no token here)
    run.sync_talks()
    try:
        run.process("Alice Walker")
    except Exception:
        pass
    t = run.state["talks"]["Alice Walker"]
    lean = os.path.join(run.cut_dir, "Alice Walker.mp4")
    check("cut made", (t["steps"].get("cut") or {}).get("status") == "done" and os.path.exists(lean),
          (t["steps"].get("cut") or {}).get("note") or t.get("error"))
    if os.path.exists(lean):
        import cv2
        ref = lambda i: cv2.cvtColor(cv2.resize(deck[i], (W, H)), cv2.COLOR_BGR2RGB).astype(np.float32)
        d0 = np.abs(frame(lean, 10) - ref(0)).mean()
        check("cut: 0:10 shows deck page 1 full frame (taskbar and headshot gone)", d0 < 6, "mean diff %.1f" % d0)
        d2 = np.abs(frame(lean, 70) - ref(2)).mean()
        check("cut: 1:10 shows page 3 full frame instead of Presenter View", d2 < 6, "mean diff %.1f" % d2)
        b = frame(lean, 25)
        low = b[530:600, 100:1000].std()
        check("cut: 0:25 build step - the third bullet not shown yet", low < 8 and np.abs(b[:300] - ref(1)[:300]).mean() < 6,
              "std where the bullet would be %.1f" % low)
        d3 = np.abs(frame(lean, 118) - ref(3)).mean()
        check("cut: 1:58 the small TV filmed in the room becomes page 4 full frame", d3 < 6, "mean diff %.1f" % d3)
        demo = frame(lean, 52)
        check("cut: 0:52 the demo stays (a dark terminal)", demo.mean() < 60, "mean %.0f" % demo.mean())
        # sound: the recorder's clean copy where it covers, the camera's elsewhere, in sync
        def treble(x):                                  # share of the sound above 2.5 kHz (the camera's stops at 2)
            sp = np.abs(np.fft.rfft(x)) ** 2
            fr = np.fft.rfftfreq(len(x), 1 / SR)
            return float(sp[fr > 2500].sum() / (sp.sum() + 1e-9))
        for at in (20.0, 90.0):
            out = pcm(lean, at, 0.5)
            want = clean[int((at - 0.2) * SR):int((at + 0.7) * SR)]
            ms, alike = lag_ms(out, want)
            check("cut: sound at %d s is the recorder's (its treble is there) and in sync (< 20 ms)" % at,
                  treble(pcm(lean, at, 4)) > 3 * treble(pcm(video, at, 4)) and abs(ms) < 20 and alike > 0.8,
                  "%.1f ms, alike %.2f; treble %.3f vs the camera's %.3f" % (ms, alike, treble(pcm(lean, at, 4)),
                                                                           treble(pcm(video, at, 4))))
        out = pcm(lean, 3, 6)
        check("cut: before the recorder starts, the camera's sound fills in", np.abs(out).mean() > 0.002,
              "level %.4f" % np.abs(out).mean())

    # the check before Descript: the good cut passes, broken ones are stopped
    chk = (t["steps"].get("check") or {})
    check("check before Descript: the good cut passes", chk.get("status") == "done" and not (t.get("check") or {}).get("problems"),
          (t.get("check") or {}).get("problems") or chk.get("note"))
    check("check before Descript: a sheet of the cut itself", os.path.exists(os.path.join(run.cut_dir, "Alice Walker.check.jpg")))
    if os.path.exists(lean):
        from live_factory import media, precheck
        part = run.cuts()["cuts"][0]["parts"]
        padded, _, _ = run.padded("Alice Walker", part)
        plans2, _, _ = run.picture_for("Alice Walker", run.cuts()["cuts"][0], padded)
        sounds2, _ = run.sound_for(run.cuts()["cuts"][0], padded)
        size = (media.probe(lean)["width"], media.probe(lean)["height"])
        extra = run.picture_extra("Alice Walker", plans2[0], padded[0], size)
        extra["sound"] = sounds2[0]
        parts = [(run.rec_path("r1"), padded[0]["start"], padded[0]["end"])]
        leak = os.path.join(TMP, "leak.mp4")
        ff("-i", lean, "-i", run.rec_path("r1"), "-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0[a]", "-map", "0:v",
           "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-t", "%.2f" % (LEN - 0.5), leak)
        probs, _ = precheck.check(leak, parts, [extra], size)
        check("check before Descript: the camera's pilot sound mixed in is caught", any("not the separate" in x for x in probs), probs)
        late = os.path.join(TMP, "late.mp4")
        ff("-i", lean, "-af", "adelay=150:all=1", "-c:v", "copy", "-c:a", "aac", "-t", "%.2f" % (LEN - 0.5), late)
        probs, _ = precheck.check(late, parts, [extra], size)
        check("check before Descript: sound 150 ms late is caught", any("sync" in x or "not the separate" in x for x in probs), probs)
        nopages = os.path.join(TMP, "nopages.mp4")
        ff("-i", run.rec_path("r1"), "-t", "%.2f" % (LEN - 0.5), "-c", "copy", nopages)
        probs, _ = precheck.check(nopages, parts, [{"slides": extra["slides"]}], size)
        check("check before Descript: deck pages missing from the picture are caught", any("deck page" in x for x in probs),
              probs[:2])

    # night mode
    p_warm = picture.plan(warm, 0, LEN - 0.5, None)
    check("night mode: a warm screen share is detected", p_warm["night"]["on"] and p_warm["colour"], p_warm["night"])
    if p_warm["night"].get("gains"):
        g = p_warm["night"]["gains"]
        check("night mode: its colour cast measured (0.85 green, 0.6 blue)", abs(g[1] - 0.85) < 0.05 and abs(g[2] - 0.6) < 0.05, g)
    p_plain = picture.plan(video, 0, LEN - 0.5, None)
    check("night mode: the normal screen share is left alone", not p_plain["night"]["on"], p_plain["night"])
    p_deck = picture.plan(warm, 0, LEN - 0.5, deck_pdf)
    check("night mode with the deck: the pages still found", p_deck["shown"] > 0.5, "%.0f%%" % (100 * p_deck["shown"]))

    print("saturated sound")
    from live_factory import saturation
    sat = t.get("saturation") or {}
    check("saturation: the clean cut is heard as not clipped", sat.get("before") and not sat.get("some"), sat.get("note"))
    check("saturation: a clean talk keeps Studio Sound 80%", "Studio Sound at 80%" in run.edit_prompt("Alice Walker", "x"))
    # a voice with round, vowel-like peaks (the buzzy test voice above has sawtooth peaks, which no mic clips like a
    # voice), 150 s, as a small video; then the same driven ~12 dB into a hard ceiling
    rng = np.random.default_rng(7)
    vo = np.zeros(int(150 * SR), np.float32)
    t0 = 0.0
    while t0 < 149:
        d = rng.uniform(0.12, 0.35)
        a0, b0 = int(t0 * SR), min(len(vo), int((t0 + d) * SR))
        ph = 2 * np.pi * np.cumsum(np.full(b0 - a0, rng.uniform(100, 200))) / SR
        vo[a0:b0] = rng.uniform(0.3, 1.0) * np.hanning(b0 - a0) * sum(np.sin(h * ph + h) / h ** 1.5 for h in range(1, 7)) * 0.25
        t0 += d + rng.uniform(0.05, 0.3)
    vwav = os.path.join(TMP, "vowels.wav")
    wav(vwav, vo)
    plain = os.path.join(TMP, "vowels.mp4")
    ff("-f", "lavfi", "-i", "color=c=gray:s=320x180:r=10:d=150", "-i", vwav, "-c:v", "libx264", "-preset", "veryfast",
       "-c:a", "aac", "-b:a", "192k", "-shortest", plain)
    cl = saturation.scan(plain)
    check("saturation: a clean voice is not clipped", not cl["some"], saturation.describe(cl))
    hot = os.path.join(TMP, "clipped.mp4")
    ff("-i", plain, "-c:v", "copy", "-af", "volume=12dB,asoftclip=type=hard:threshold=0.6,volume=-3dB",
       "-c:a", "aac", "-b:a", "192k", hot)
    # 1. the better track: a recorder clipped in its 2nd minute, the camera clean -> that minute goes to the camera
    rec_hot = os.path.join(TMP, "recorder_hot.wav")
    ff("-i", vwav, "-af", "volume=12dB:enable='between(t,60,120)',asoftclip=type=hard:threshold=0.6:"
       "enable='between(t,60,120)',volume=-12dB:enable='between(t,60,120)'", "-c:a", "pcm_s16le", rec_hot)
    runs = run.unclipped_runs(rec_hot, 0.0, 150.0, plain, 0.0, 150.0, 1.0, {})
    check("better track: the recorder's clipped minute goes to the camera, the rest stays the recorder's",
          [(r0, r1, bool(w)) for r0, r1, w in runs] == [(0.0, 60.0, False), (60.0, 120.0, True), (120.0, 150.0, False)], runs)
    # 2. the better channel: left clipped, right clean -> the right one on both
    st_hot = os.path.join(TMP, "left_clipped.mp4")
    ff("-i", plain, "-c:v", "copy", "-af", "pan=stereo|c0=c0|c1=c0,asplit[l][r];[l]pan=mono|c0=c0,volume=12dB,"
       "asoftclip=type=hard:threshold=0.6,volume=-12dB[lc];[r]pan=mono|c0=c1[rc];[lc][rc]join=inputs=2:channel_layout=stereo",
       "-c:a", "aac", "-b:a", "192k", st_hot)
    rc = run.declip_cut("Alice Walker", {}, st_hot)
    check("better channel: the clean right channel replaces the clipped left one", not (rc.get("after") or {}).get("some")
          and "channel is clipped" in rc.get("note", ""), rc.get("note"))
    # 3. no choice, no tool helps (here: none installed) -> Studio Sound 100%, the last resort
    keep = run.cfg.get("declip_python"), os.environ.pop("LIVE_FACTORY_DECLIP_PYTHON", None)
    run.cfg["declip_python"] = os.path.join(TMP, "no-such-python.exe")
    rec = run.declip_cut("Alice Walker", {}, hot)
    check("saturation: the clipped copy is heard as SATURATED", rec["saturated"] and rec["before"]["clipped_minutes"] >= 2,
          rec.get("note"))
    check("last resort: no tool -> Studio Sound 100%", rec["last_resort"] and "last resort" in rec["note"], rec.get("note"))
    with run.lock:
        t["saturation"] = rec
    check("last resort: the edit prompt says Studio Sound 100%", "Studio Sound at 100%" in run.edit_prompt("Alice Walker", "x"))
    with run.lock:
        t["saturation"] = dict(rec, last_resort=False, repaired=True)
    check("repaired by a tool: the normal Studio Sound 80%", "Studio Sound at 80%" in run.edit_prompt("Alice Walker", "x"))
    with run.lock:
        t["saturation"] = sat
    run.cfg["declip_python"] = keep[0]
    if keep[1]:
        os.environ["LIVE_FACTORY_DECLIP_PYTHON"] = keep[1]
    # 4. the judge, with stand-in tools and a stand-in Whisper: a tool that helps passes, one that garbles the words or
    # leaves the peaks fails, and the best one wins
    import shutil
    real_words, real_run = saturation._words, saturation.run_tool
    said = "so the third group of alerts is where the real incident was hiding all along and nobody saw it".split()
    heard = {"clipped": (said, 0.68), "good": (said, 0.72), "garble": (["pokemon"] * 5 + said[5:], 0.60),
             "same": (said, 0.69)}
    saturation._words = lambda path, cfg=None: heard[os.path.basename(path).split("-")[-1].split(".")[0]
                                                     if "-" in os.path.basename(path) else "clipped"]

    def fake_run(tool, wi, wo, cfg=None):
        shutil.copy(plain if tool != "x:same" else wi, wo)   # "good"/"garble": the clean sound; "same": no change
    saturation.run_tool = fake_run
    try:
        sample = os.path.join(TMP, "sample-clipped.wav")
        saturation.to_wav(hot, sample, 30, 60)
        tr = saturation.trial(sample, os.path.join(TMP, "trial"), {}, ["x:good", "x:garble", "x:same"])
        verdict = {r["tool"]: r["good"] for r in tr["results"]}
        check("judge: the tool that helps passes, the garbling one and the no-op fail, the best wins",
              verdict == {"x:good": True, "x:garble": False, "x:same": False} and tr["best"] == "x:good",
              [(r["tool"], r["why"]) for r in tr["results"]])
    finally:
        saturation._words, saturation.run_tool = real_words, real_run
    quiet = os.path.join(TMP, "limited.mp4")            # a limiter holding the peaks down is not clipping
    ff("-i", plain, "-c:v", "copy", "-af", "volume=14dB,alimiter=limit=0.6:attack=5:release=50",
       "-c:a", "aac", "-b:a", "192k", quiet)
    lim = saturation.scan(quiet)
    check("saturation: a limiter holding 14 dB of peaks is not clipping", not lim["some"], saturation.describe(lim))

    print("\n%s" % ("all good" if not FAILS else "%d failed: %s" % (len(FAILS), ", ".join(FAILS))))
    print("files: " + TMP)
    sys.exit(1 if FAILS else 0)


main()
