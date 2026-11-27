"""The picture pass (Marek, 2026-11-27): the best slides, full screen - period.

For every talk the factory looks at what the recording shows and, when the speaker's deck is at hand, finds which
deck page is on screen at each moment - wherever and however it sits in the picture: Presenter View with notes and a
toolbar around it, a screen share with the taskbar, a headshot camera in the corner, a camera filming a small TV.
Every stretch showing a known page is replaced by that page, rendered sharp and full frame. Anything that is not a
deck page (a live demo, a browser, code, the speaker) keeps the recording as it is.

Night mode (Night Shift / Night Light: the shared screen goes yellow) is measured on the same frames:
  - pages that are replaced need nothing (the deck has the true colours);
  - the stretches that stay video get the colours put back: a per-channel curve learned from the deck pages seen
    in the same talk (screen colour -> deck colour), or with no deck, the white point of the screen's own white areas.

Decks: <work folder>/decks/<talk>.pdf (dropped in by hand, e.g. from the Deck Dump Drive folder), else the event's
slides/<talk page>.pdf in the site repo (what the deck dump ships), else none. Needs OpenCV (opencv-python-headless)
and pypdfium2; without them the pass is skipped with a note and the talk is cut as before.
"""
import json
import os
import re
import subprocess

import numpy as np

from . import media

ANALYSIS_WIDTH = 960      # frames and deck pages are compared at this width
SAMPLE_EVERY = 0.5        # seconds between the frames looked at (a page change is placed within a quarter second)
GRID = (16, 9)            # cells of the page compared one by one (a headshot in a corner spoils a few, not the match)
FINE = (480, 270)         # the closer look at the page found: which of its lines are not shown yet (builds)
FINE_GRID = (48, 27)      # 10 px cells at FINE


def available():
    """None when the pass can run, else why not."""
    try:
        import cv2  # noqa: F401
        import pypdfium2  # noqa: F401
    except Exception as ex:                       # ImportError, or a DLL an Application Control policy blocks
        return "picture pass off - %s (pip install opencv-python-headless pypdfium2)" % str(ex)[:120]
    return None


# ---- the deck ------------------------------------------------------------------------------------------------------
def find_deck(run, key, cut=None):
    """The speaker's deck as a PDF path, or None. A cut's own "deck" (path) wins, then <work>/decks/, then the
    site's slides/<talk page>.pdf."""
    if cut and cut.get("deck"):
        p = cut["deck"]
        p = p if os.path.isabs(p) else os.path.join(run.dir, p)
        return p if os.path.exists(p) else None
    folder = os.path.join(run.dir, "decks")
    if os.path.isdir(folder):
        want = _norm(key)
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith(".pdf") and (_norm(f[:-4]) == want or _norm(f[:-4]).startswith(want)):
                return os.path.join(folder, f)
    from . import events
    talk = next((t for t in events.talks(run.ev) if t["key"] == key), None)
    if talk and talk.get("page"):
        p = os.path.join(run.ev["path"], "slides", talk["page"] + ".pdf")
        if os.path.exists(p):
            return p
    return None


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def render_pdf(path, width):
    """Every page as an RGB uint8 array, width px wide."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(path)
    out = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            w = page.get_width()
            bmp = page.render(scale=width / w, rev_byteorder=True)
            arr = bmp.to_numpy()
            out.append(np.ascontiguousarray(arr[:, :, :3]))
            page.close()
    finally:
        doc.close()
    return out


class Deck:
    """A deck ready for matching: per page the colour picture, ORB features and a small grey thumbnail."""

    def __init__(self, path):
        import cv2
        self.path = path
        self.pages = render_pdf(path, ANALYSIS_WIDTH)
        self.orb = cv2.ORB_create(2000)
        self.gray, self.kp, self.des, self.thumb, self.flat_bg, self.fine = [], [], [], [], [], []
        for img in self.pages:
            # a plain background: most of the page's edge is one colour (an accent bar or a logo is fine)
            ring = np.concatenate([img[:4].reshape(-1, 3), img[-4:].reshape(-1, 3),
                                   img[:, :4].reshape(-1, 3), img[:, -4:].reshape(-1, 3)]).astype(np.int16)
            bg = np.median(ring, axis=0)
            self.flat_bg.append(bool((np.abs(ring - bg).max(axis=1) < 12).mean() >= 0.75))
            g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            kp, des = self.orb.detectAndCompute(g, None)
            self.gray.append(g)
            self.kp.append(kp)
            self.des.append(des)
            self.thumb.append(_thumb(g))
            self.fine.append(_thumb(g, FINE))

    def __len__(self):
        return len(self.pages)


THUMB = (160, 90)


def _thumb(gray, size=THUMB):
    """A small blurred grey copy (float) - what pages and frames are compared on."""
    import cv2
    return cv2.resize(cv2.GaussianBlur(gray, (5, 5), 0), size, interpolation=cv2.INTER_AREA).astype(np.float32)


def _robust_fit(A, a, tol=25.0):
    """frame = alpha * page + beta, fitted on the pixels that agree - a taskbar or an unbuilt diagram must not pull
    it. Starts from "no change" (a screen capture) and from plain least squares (a camera, night mode), refits three
    times on the pixels within tol, keeps whichever start ends with more agreeing pixels."""
    best = None
    ink = np.abs(A[:, 0] - np.median(A[:, 0])) > 60          # strong content: text, lines (not light fills)
    lsq, *_ = np.linalg.lstsq(A, a, rcond=None)
    for coef in (np.array([1.0, 0.0]), lsq):
        for _ in range(3):
            keep = np.abs(a - A @ coef) < tol
            if keep.sum() < 50:
                break
            coef, *_ = np.linalg.lstsq(A[keep], a[keep], rcond=None)
        if not 0.4 <= coef[0] <= 2.5:                   # a screen or a camera never flattens the page that much
            continue
        n = int(((np.abs(a - A @ coef) < tol) & ink).sum())   # judged on the page's content, not its white space
        if best is None or n > best[0]:
            best = (n, coef)
    return best[1] if best and best[0] >= 20 else None


def compare(frame_t, page_t, grid=GRID):
    """How well a frame (warped into the page's place) shows a page, robust to things covering part of it (a
    taskbar, a headshot, a toolbar) and aware of builds (bullets that appear one by one - the PDF has the finished
    page). The frame's brightness is fitted to the page's; then, on a grid of small cells, only cells with content
    in the page are judged:
      good     - the frame and the page agree;
      hidden   - the frame is still empty there (a build step not shown yet);
      conflict - the frame shows something else there (other text: another page, another version of the deck).
    Things over the page's blank areas (a taskbar, a cursor, a headshot in an empty corner) are counted as
    "covered" and tolerated.
    Returns {"full": good / page content cells, "build": good / (content - hidden), "conflict", "covered", "diff":
    mean difference in the good cells (0-255), "hidden": [cell numbers], "good": n, "content": n}."""
    none = {"full": 0.0, "build": 0.0, "title": 0.0, "conflict": 1.0, "covered": 1.0, "diff": 255.0, "hidden": [], "good": 0,
            "content": 0}
    a, b = frame_t.ravel(), page_t.ravel()
    A = np.stack([b, np.ones_like(b)], 1)
    coef = _robust_fit(A, a)
    if coef is None or coef[0] < 0.3:                   # no real contrast match (a blank page vs anything)
        return none
    res = np.abs(a - A @ coef)
    gx, gy = grid
    h, w = page_t.shape
    ch, cw = h // gy, w // gx

    def cells(x):
        return x[:ch * gy, :cw * gx].reshape(gy, ch, gx, cw)
    res_c = cells(res.reshape(page_t.shape)).mean(axis=(1, 3)).ravel()
    fit = (A @ coef).reshape(page_t.shape)              # the page in the frame's brightness
    ink_p = cells(fit).std(axis=(1, 3)).ravel() > 4.0
    ink_f = cells(frame_t).std(axis=(1, 3)).ravel() > 4.0
    # the picture's own noise (camera grain, compression), measured where neither has content, sets the tolerance
    plain = ~ink_p                                      # blank in the page (the median shrugs off a headshot)
    noise = float(np.median(res_c[plain])) if plain.sum() >= 4 else 0.0
    good = res_c < max(9.0, 2.5 * noise + 4.0)
    n = int(ink_p.sum())
    if n < 4:
        return none
    good_p = int((ink_p & good).sum())
    hidden = ink_p & ~good & ~ink_f                     # the page has it, the screen is still blank there
    clash = ink_p & ~good & ink_f                       # the screen shows something else where the page has ink
    over = ~ink_p & ink_f & ~good                       # something over the page's blank area (taskbar, cursor)
    # something on top: mostly over the page's blank area (a headshot in an empty corner), or a strip along the very
    # edge (a taskbar across the footer line, a bar down the side); a clash anywhere else is other content
    import cv2
    nlab, lab = cv2.connectedComponents((clash | over).reshape(gy, gx).astype(np.uint8), connectivity=4)
    ring = np.zeros((gy, gx), bool)                     # sides and bottom only: the top rows hold the title
    by, bx = int(np.ceil(0.12 * gy)), int(np.ceil(0.12 * gx))
    ring[gy - by:], ring[:, :bx], ring[:, gx - bx:] = True, True, True
    ring = ring.ravel()
    lab = lab.ravel()
    for k in range(1, nlab):
        m = lab == k
        if over[m].sum() >= 0.6 * m.sum() or (ring[m].all() and m.sum() >= 3):
            over, clash = over | m, clash & ~m
    conflict, covered = int(clash.sum()), int(over.sum())
    n_seen = n - int((ink_p & over).sum())              # the page's content cells not covered by something on top
    if n_seen < 4:
        return none
    good_p = int((ink_p & good & ~over).sum())
    hidden = hidden & ~over
    shown = n_seen - int(hidden.sum())
    # the title: the page's first two rows with content must be on screen and agree (builds never hide the title;
    # two slides of one deck differ there first)
    rows = [j for j in range(gy) if (ink_p & ~over).reshape(gy, gx)[j].any()][:2]
    tcells = np.zeros((gy, gx), bool)
    for j in rows:
        tcells[j] = True
    tcells = tcells.ravel() & ink_p & ~over
    title = float((tcells & good).sum()) / max(1, int(tcells.sum()))
    return {"full": good_p / n_seen, "build": good_p / max(1, shown), "conflict": conflict / n, "title": title,
            "covered": covered / ink_p.size,
            "diff": float(res_c[ink_p & good].mean()) if good_p else 255.0,
            "hidden": [int(i) for i in np.flatnonzero(hidden)], "good": good_p, "content": n_seen}


# ---- looking at one frame ------------------------------------------------------------------------------------------
def _refine(frame_f, page_f):
    """Line the frame's page picture up with the page to the pixel (ECC, a small affine correction): features
    place a filmed or blurred screen a pixel or two off, and at 10 px cells that is enough to make text clash."""
    import cv2
    try:
        warp = np.eye(2, 3, dtype=np.float32)
        crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 1e-4)
        _, warp = cv2.findTransformECC(page_f.astype(np.float32), frame_f.astype(np.float32), warp,
                                       cv2.MOTION_AFFINE, crit, None, 3)
        if np.abs(warp[:, :2] - np.eye(2)).max() > 0.05 or np.abs(warp[:, 2]).max() > 0.05 * page_f.shape[1]:
            return frame_f                              # a big jump is no refinement - keep the original
        h, w = page_f.shape
        return cv2.warpAffine(frame_f, warp, (w, h), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_REPLICATE)
    except cv2.error:
        return frame_f


def unshown(frame_f, page_f):
    """The page's content not on screen yet: cells (10 px at this size) where the page has ink and the screen is
    plain, in blocks of 3+ cells (a line of text, a box) - a lone cell is blur or noise and is never painted out;
    grown into plain neighbours to catch the edges. Returned as cells of FINE_GRID (what paint() uses)."""
    import cv2
    a, b = frame_f.ravel(), page_f.ravel()
    A = np.stack([b, np.ones_like(b)], 1)
    coef = _robust_fit(A, a)
    if coef is None:
        return []
    h, w = page_f.shape
    gx, gy = w // 10, h // 10
    cells = lambda x: x[:10 * gy, :10 * gx].reshape(gy, 10, gx, 10)
    fit = (A @ coef).reshape(page_f.shape)
    res = cells(np.abs(frame_f - fit)).mean(axis=(1, 3))
    ink_p = cells(fit).std(axis=(1, 3)) > 6.0
    plain_f = cells(frame_f).std(axis=(1, 3)) < 2.0
    hid = (ink_p & plain_f & (res > 6.0)).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(hid, connectivity=8)
    keep = np.zeros_like(hid, bool)
    for k in range(1, n):
        if stats[k, cv2.CC_STAT_AREA] >= 3:
            keep |= lab == k
    if keep.any():                                      # its edges: grow into neighbours the screen shows as plain
        grown = cv2.dilate(keep.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        keep |= grown & plain_f & (cells(fit).std(axis=(1, 3)) > 1.0)
    if (gx, gy) != FINE_GRID and keep.any():            # onto the painting grid
        keep = cv2.resize(keep.astype(np.uint8), FINE_GRID, interpolation=cv2.INTER_NEAREST).astype(bool)
    return [int(i) for i in np.flatnonzero(keep.ravel())]


def accept(c, flat_bg=True):
    """"page" when the frame shows the whole page (things over its blank areas are fine), "build" when it shows the
    page part-built - some of its content not revealed yet, nothing contradicting it; only on a plain background,
    where painting the rest out looks right - else None."""
    if c["covered"] > 0.4:                              # mostly something else on top
        return None
    if c["full"] >= 0.85 and c["conflict"] <= 0.08 and c["diff"] <= 7 and len(c["hidden"]) <= 1:
        return "page"                                   # (one hidden cell is noise; two or more: a line not shown yet)
    if (flat_bg and c["hidden"] and c["build"] >= 0.92 and c["conflict"] <= 0.03 and c["diff"] <= 7
            and c["title"] >= 0.8 and c["good"] >= max(6, 0.25 * c["content"])):
        return "build"
    if c["full"] >= 0.85 and c["conflict"] <= 0.03 and c["diff"] <= 7 and flat_bg and c["title"] >= 0.8:
        return "build"
    return None


def screen_like(gray):
    """Share of 8x8 blocks that are perfectly flat: a screen (slides, a desktop) has large flat areas, a camera
    picture has noise everywhere."""
    h, w = gray.shape[0] // 8 * 8, gray.shape[1] // 8 * 8
    b = gray[:h, :w].astype(np.float32).reshape(h // 8, 8, w // 8, 8)
    return float((b.std(axis=(1, 3)) < 1.5).mean())


def white_point(rgb):
    """The colour of the screen's white areas: the median of the brightest pixels (top 3% by luminance), or None
    when the frame has no real light area (a dark theme, a dark slide)."""
    lum = rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    cut = np.percentile(lum, 97)
    if cut < 150:
        return None
    sel = rgb[lum >= cut]
    return [float(x) for x in np.median(sel, axis=0)]


def _quad_ok(corners, fw, fh):
    """The deck page's outline in the frame: convex, big enough (2% of the frame), not absurdly skewed."""
    import cv2
    c = corners.reshape(4, 2)
    if not cv2.isContourConvex(c.astype(np.float32)):
        return False
    area = cv2.contourArea(c.astype(np.float32))
    if area < 0.02 * fw * fh or area > 1.6 * fw * fh:
        return False
    sides = [np.linalg.norm(c[i] - c[(i + 1) % 4]) for i in range(4)]
    return min(sides) > 0.15 * max(sides)


class Matcher:
    """Finds the deck page in a frame. Keeps the last page outline: a steady screen share is checked with a cheap
    warp + compare; features (ORB + RANSAC homography) only when that fails."""

    def __init__(self, deck, fw, fh, loose=False):
        import cv2
        self.loose = loose                               # "deck_match": "loose" - the page by its layout alone
        self.cv2, self.deck, self.fw, self.fh = cv2, deck, fw, fh
        self.orb = cv2.ORB_create(2500)
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        self.H = None                              # page -> frame homography last seen
        self.last = None
        self.near_miss = 0                               # frames whose layout matched a page but the pixels did not
        self._fine_cache = {}

    def _verify(self, gray, H, candidates):
        """Warp the frame back into page space with H and score the candidate pages; those that pass, best first:
        [(page, score, kind)] - a page shown whole beats a build step, then the most agreeing cells, then the
        smaller difference."""
        cv2 = self.cv2
        try:
            Hi = np.linalg.inv(H)
        except np.linalg.LinAlgError:
            return []
        warped, ok_list = {}, []
        for p in candidates:
            shape = self.deck.gray[p].shape
            if shape not in warped:
                w = cv2.warpPerspective(gray, Hi, (shape[1], shape[0]), flags=cv2.INTER_AREA, borderValue=0)
                warped[shape] = _thumb(w)
            c = compare(warped[shape], self.deck.thumb[p])
            ok = accept(c, self.deck.flat_bg[p])
            if ok:
                ok_list.append(((ok == "page", round(c["build"], 2), c["good"], -c["diff"]), p, c, ok))
        ok_list.sort(key=lambda x: x[0], reverse=True)
        return [x[1:] for x in ok_list]

    def _layout_page(self, fits):
        """The page the frame shows by its layout alone (features, no pixel check): (page, H) of its biggest copy
        fully in the frame, when one page clearly leads (30+ matching features, twice the next page's) - else None.
        Used for "deck_match": "loose" (a deck exported with other fonts) and to show the near match on the sheet."""
        by = {}
        for p, H, inl in fits:
            by.setdefault(p, []).append((inl, H))
        ranked = sorted(((max(i for i, _ in v), p) for p, v in by.items()), reverse=True)
        if not ranked or ranked[0][0] < 30 or (len(ranked) > 1 and ranked[0][0] < 2 * ranked[1][0]):
            return None
        p = ranked[0][1]
        whole = [(self._area(H, p), H) for _, H in by[p] if abs(self._area(H, p) - self._area_raw(H, p)) < 0.01]
        whole = [x for x in whole if x[0] >= 0.04]
        if not whole:
            return None
        return p, max(whole, key=lambda x: x[0])[1]

    def _area_raw(self, H, p):
        ph, pw = self.deck.gray[p].shape
        c = self.cv2.perspectiveTransform(np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]).reshape(-1, 1, 2), H)
        return float(self.cv2.contourArea(c.reshape(4, 2))) / (self.fw * self.fh)

    def _area(self, H, p):
        """How much of the frame the page covers there (a share; parts outside the frame don't count)."""
        cv2 = self.cv2
        ph, pw = self.deck.gray[p].shape
        c = cv2.perspectiveTransform(np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]).reshape(-1, 1, 2), H).reshape(4, 2)
        c[:, 0] = np.clip(c[:, 0], 0, self.fw)
        c[:, 1] = np.clip(c[:, 1], 0, self.fh)
        return float(cv2.contourArea(c.astype(np.float32))) / (self.fw * self.fh)

    def _fine_size(self, H, p):
        """The closer look's size: the page as big as it really is on screen, at most FINE (a small TV filmed in
        the room is judged at the detail it has, not blown up into blur)."""
        ph, pw = self.deck.gray[p].shape
        c = self.cv2.perspectiveTransform(np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]).reshape(-1, 1, 2), H).reshape(4, 2)
        w = 0.5 * (np.linalg.norm(c[1] - c[0]) + np.linalg.norm(c[2] - c[3]))
        fw = int(max(160, min(FINE[0], w)) // 10 * 10)
        fh = int(round(fw * FINE[1] / FINE[0] / 10) * 10)
        return fw, fh

    def _fine_page(self, p, size):
        key = (p, size)
        if key not in self._fine_cache:
            self._fine_cache[key] = self.deck.fine[p] if size == FINE else _thumb(self.deck.gray[p], size)
        return self._fine_cache[key]

    def _fine_ok(self, gray, H, p):
        """The closer look (the page's on-screen size, up to 480 px): the page's title text has to agree - at 160 px
        two bold titles blur alike. Returns (the frame's page picture at that size, the page's) or None."""
        cv2 = self.cv2
        sh = self.deck.gray[p].shape
        size = self._fine_size(H, p)
        fine = _thumb(cv2.warpPerspective(gray, np.linalg.inv(H), (sh[1], sh[0]), flags=cv2.INTER_AREA,
                                          borderValue=0), size)
        page = self._fine_page(p, size)
        grid = (size[0] // 10, size[1] // 10)
        # a filmed or soft screen: lined up to the pixel (ECC) and the sharp page blurred to the frame's sharpness
        # (different words still differ at any blur; a wrong title of the same weight only blurs alike at 160 px).
        # A clean screen capture is judged as it is too - whichever agrees best counts.
        ok = lambda cf: cf["title"] >= 0.6 and cf["conflict"] <= 0.35
        if ok(compare(fine, page, grid)):                 # a clean screen capture: no need for more
            return fine, page
        refined = _refine(fine, page)
        for f in (fine, refined):
            for sigma in (0, 0.8, 1.5, 2.5):
                if f is fine and not sigma:
                    continue
                pg = page if not sigma else self.cv2.GaussianBlur(page, (0, 0), sigma)
                if ok(compare(f, pg, grid)):
                    return f, page
        return None

    def _features(self, gray, pages):
        """ORB match against these pages: candidate (page, H, inliers) fits, most inliers first. Two models per page:
        the page only scaled and moved (a screen capture - robust even with few matches) and full perspective (a
        camera filming a screen); the pixel check in look() decides between them."""
        cv2 = self.cv2
        kp, des = self.orb.detectAndCompute(gray, None)
        if des is None or len(kp) < 30:
            return []
        out = []
        for p in pages:
            dd = self.deck.des[p]
            if dd is None or len(self.deck.kp[p]) < 12:
                continue
            pairs = self.bf.knnMatch(dd, des, k=2)
            good = [m for m, n in (x for x in pairs if len(x) == 2) if m.distance < 0.78 * n.distance]
            if len(good) < 15:
                continue
            src = np.float32([self.deck.kp[p][m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
            ph, pw = self.deck.gray[p].shape
            box = np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]).reshape(-1, 1, 2)
            # a page can be on screen more than once (Presenter View: the current slide, the projected screen beside
            # it, the next-slide preview, the thumbnail strip) - each copy is a candidate, the pixel check decides
            left_s, left_d = src, dst
            for _ in range(3):
                if len(left_s) < 12:
                    break
                A, mask = cv2.estimateAffinePartial2D(left_s, left_d, method=cv2.RANSAC, ransacReprojThreshold=4.0)
                if A is None or int(mask.sum()) < 12:
                    break
                if abs(A[0, 1]) < 0.02 * abs(A[0, 0]):
                    H = np.vstack([A, [0, 0, 1]])
                    if _quad_ok(cv2.perspectiveTransform(box, H), self.fw, self.fh):
                        out.append((p, H, int(mask.sum())))
                keep = mask.ravel() == 0
                left_s, left_d = left_s[keep], left_d[keep]
            Hp, maskp = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
            if Hp is not None and int(maskp.sum()) >= 20 and _quad_ok(cv2.perspectiveTransform(box, Hp), self.fw, self.fh):
                out.append((p, Hp, int(maskp.sum())))
        return sorted(out, key=lambda c: -c[2])

    def look(self, rgb, search=True):
        """What one frame shows: {"page": n or None, "hidden": build cells, "cover", "gains"} or {"page": None, "why",
        "near": the page by layout alone}. search=False: only the cheap same-place check."""
        cv2 = self.cv2
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        if gray.std() < 4:                                   # a black / blank frame
            return {"page": None, "why": "blank"}
        n = len(self.deck)
        hit, fits = None, []
        if self.H is not None:                               # the steady case: same place, maybe another page
            for p, c, kind in self._verify(gray, self.H, range(n))[:3]:
                fine = self._fine_ok(gray, self.H, p)
                if fine is not None:
                    hit = (p, c, kind, self.H, fine)
                    break
        if hit is None and not search:
            return {"page": None, "why": "not searched (nothing found in the last frames)"}
        if hit is None:
            order = list(range(n))
            if self.last is not None:                        # neighbours of the last page first
                order.sort(key=lambda p: abs(p - self.last))
            fits = self._features(gray, order[:5])
            if not fits and n > 5:
                fits = self._features(gray, order[5:])
            best = None
            for p0, H, inl in fits[:8]:
                area = self._area(H, p0)
                if best is not None and area <= best[0]:     # the biggest copy that passes is the slide being shown
                    continue                                 # (not the next-slide preview or a thumbnail)
                near = sorted(set(range(max(0, p0 - 2), min(n, p0 + 3))))     # builds: the next pages look alike
                for p, c, kind in self._verify(gray, H, near)[:3]:
                    fine = self._fine_ok(gray, H, p)
                    if fine is not None:
                        best = (area, (p, c, kind, H, fine))
                        break
            hit = best[1] if best else None
        near = self._layout_page(fits) if hit is None and fits else None
        if hit is None and near is not None and self.loose:
            p, H = near
            hit = (p, {}, "page", H, None)
        if hit is None:
            out = {"page": None, "why": "laid out like a deck page, content differs" if fits else "no deck page"}
            if near is not None:
                out["near"] = near[0]
            return out
        p, c, kind, H, fine_pair = hit
        hidden = unshown(*fine_pair) if fine_pair else []
        if hidden and not self.deck.flat_bg[p]:
            return {"page": None, "why": "a build step on a busy background - the video stays"}
        self.H, self.last = H, p
        ph, pw = self.deck.gray[p].shape
        corners = cv2.perspectiveTransform(np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]).reshape(-1, 1, 2), H)
        cover = float(cv2.contourArea(corners.reshape(4, 2))) / (self.fw * self.fh)
        return {"page": p, "hidden": hidden, "cells": round(c.get("full", 0), 2),
                "diff": round(c.get("diff", 0), 1), "loose": fine_pair is None, "cover": round(min(cover, 1.0), 3),
                "gains": self._gains(rgb, H, p)}

    def _gains(self, rgb, H, p):
        """Screen colour vs deck colour, per channel (r, g, b): night mode shows as blue (and some green) well
        below red. Only meaningful on pages with enough light area."""
        cv2 = self.cv2
        page = self.deck.pages[p]
        ph, pw = page.shape[:2]
        w = cv2.warpPerspective(rgb, np.linalg.inv(H), (pw, ph), flags=cv2.INTER_AREA)
        light = page.mean(axis=2) > 200
        if light.mean() < 0.15:
            return None
        return [round(float(np.median(w[..., c][light]) / max(np.median(page[..., c][light]), 1.0)), 3)
                for c in range(3)]


# ---- reading frames ------------------------------------------------------------------------------------------------
def frames(src, start, end, every=SAMPLE_EVERY, width=ANALYSIS_WIDTH):
    """(t, rgb) every `every` seconds over [start, end] of src (t in the recording's own seconds)."""
    info = media.probe(src)
    W, H = info["width"], info["height"]
    if not (W and H):
        return
    h = int(round(width * H / W / 2) * 2)
    args = [media.tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % start, "-to", "%.3f" % end,
            "-i", src, "-an", "-vf", "fps=1/%g,scale=%d:%d:flags=area" % (every, width, h), "-f", "rawvideo",
            "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size, i = width * h * 3, 0
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield start + i * every, np.frombuffer(buf, np.uint8).reshape(h, width, 3)
            i += 1
    finally:
        p.stdout.close()
        p.wait()


# ---- the plan for one stretch of a recording ------------------------------------------------------------------------
def scan(src, start, end, deck=None, on_progress=None, every=SAMPLE_EVERY, loose=False):
    """Look at [start, end] of src: per sample the page shown (with a deck), how screen-like the picture is, and its
    white point. Returns the samples."""
    info = media.probe(src)
    W, H = info["width"], info["height"]
    fw, fh = ANALYSIS_WIDTH, int(round(ANALYSIS_WIDTH * H / W / 2) * 2) if W else 0
    m = Matcher(deck, fw, fh, loose) if deck is not None and len(deck) else None
    import cv2
    out, total, misses = [], max(1, int((end - start) / every)), 0
    for i, (t, rgb) in enumerate(frames(src, start, end, every)):
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        # a long stretch with no deck page (a demo, the speaker, another deck): the full feature search only every
        # 2 s - a page coming back is caught by then; the cheap same-place check still runs on every frame
        search = misses < 4 or misses % 4 == 0
        s = m.look(rgb, search) if m else {"page": None, "why": "no deck"}
        misses = 0 if s.get("page") is not None else misses + 1
        s["t"] = round(t, 2)
        s["screen"] = round(screen_like(gray), 2)
        s["white"] = white_point(rgb)
        out.append(s)
        if on_progress and i % 40 == 0:
            on_progress("looking at the slides - %d%%" % min(99, 100 * i // total), min(99, 100 * i // total))
    return out


def segments(samples, every=SAMPLE_EVERY, min_page=2.0, max_hole=3.0):
    """Stretches showing one deck page (or one build step of it): [{"t0", "t1", "page", "hidden": [cells]}] in the
    recording's seconds. A short gap inside one page (a mouse hover, a flicker, a dropped frame) is bridged; a build
    step that flickers by a cell or two is joined to its neighbour (hiding the union - never showing more than the
    screen did); a page shown for less than min_page is left to the video (no flashing). Boundaries sit halfway
    between the samples either side."""
    n = len(samples)
    seq = [s.get("page") for s in samples]
    hid = [frozenset(s.get("hidden") or ()) for s in samples]
    i = 0
    while i < n:                                  # bridge short holes of None between the same page
        if seq[i] is None:
            j = i
            while j < n and seq[j] is None:
                j += 1
            if 0 < i and j < n and seq[i - 1] == seq[j] and (j - i) * every <= max_hole:
                for k in range(i, j):
                    seq[k], hid[k] = seq[j], hid[i - 1] | hid[j]
            i = j
        else:
            i += 1
    runs, i = [], 0
    while i < n:
        if seq[i] is None:
            i += 1
            continue
        j = i
        while j + 1 < n and seq[j + 1] == seq[i] and hid[j + 1] == hid[i]:
            j += 1
        runs.append({"t0": samples[i]["t"] - (every / 2 if i else 0), "t1": samples[j]["t"] + every / 2,
                     "page": seq[i], "hidden": hid[i]})
        i = j + 1
    # build steps of one page: a short step or one differing by a cell or two joins its neighbour (hiding both)
    merged = []
    for r in runs:
        prev = merged[-1] if merged else None
        if (prev and prev["page"] == r["page"] and abs(prev["t1"] - r["t0"]) < 0.01 and
                (len(prev["hidden"] ^ r["hidden"]) <= 6 or r["t1"] - r["t0"] < min_page or prev["t1"] - prev["t0"] < min_page)):
            prev["t1"], prev["hidden"] = r["t1"], prev["hidden"] | r["hidden"]
        else:
            merged.append(dict(r))
    out = []
    for r in merged:
        if r["t1"] - r["t0"] < min_page:
            continue
        if out and out[-1]["page"] == r["page"] and out[-1]["hidden"] == r["hidden"] and r["t0"] - out[-1]["t1"] <= max_hole:
            out[-1]["t1"] = r["t1"]
            continue
        out.append(r)
    return [{"t0": round(r["t0"], 2), "t1": round(r["t1"], 2), "page": r["page"], "hidden": sorted(r["hidden"])}
            for r in out]


def night_mode(samples, min_share=0.6):
    """Is the shared screen in night mode? From the deck pages (per-channel gains screen/deck), else the white
    point of a screen-like picture. Returns {"on": bool, "gains": [r, g, b] (screen/true), "how": ...}."""
    g = [s["gains"] for s in samples if s.get("gains")]
    if len(g) >= 6:
        med = np.median(np.array(g), axis=0)
        med = med / med.max()
        on = med[2] < 0.85 and med[1] <= 1.0
        return {"on": bool(on), "gains": [round(float(x), 3) for x in med], "how": "deck pages (%d frames)" % len(g)}
    w = [s["white"] for s in samples if s.get("white") and s.get("screen", 0) >= 0.4]
    if len(w) < max(6, len(samples) * 0.2):
        return {"on": False, "gains": None, "how": "too few light screen frames to tell"}
    arr = np.array(w)
    ratio = arr / arr.max(axis=1, keepdims=True)
    warm = (ratio[:, 2] < 0.85) & (ratio[:, 0] >= 0.99)
    med = np.median(ratio[warm], axis=0) if warm.any() else np.median(ratio, axis=0)
    on = warm.mean() >= min_share
    return {"on": bool(on), "gains": [round(float(x), 3) for x in med],
            "how": "white point of the screen (%d%% of %d light frames warm)" % (warm.mean() * 100, len(w))}


def colour_fix(gains):
    """The ffmpeg filter that puts true colours back: each channel scaled by 1/gain (normalised so no channel
    is dimmed), clipped at white."""
    g = np.array(gains, np.float64)
    k = 1 / np.maximum(g, 0.3)
    k = k / k.min()
    # ffmpeg takes -2..2 here: a stronger tint (a blue gain under 0.5) is put right as far as it goes - before
    # 2026-11-27 such a screen stopped the cut ("Value 3.33 for parameter 'bb' out of range")
    k = np.minimum(k, 2.0)
    return "colorchannelmixer=rr=%.4f:gg=%.4f:bb=%.4f" % tuple(k)


def plan(src, start, end, deck_path=None, cfg=None, on_progress=None, loose=False):
    """The picture plan for [start, end] of src: deck pages to put over the video, and a colour fix for the rest.
    {"deck", "pages": [{"t0","t1","page"}] (recording seconds), "night": {...}, "colour": filter or None,
     "shown": share of the stretch replaced, "notes": [...]}"""
    cfg = cfg or {}
    notes = []
    deck = None
    if deck_path:
        try:
            deck = Deck(deck_path)
            notes.append("deck: %s (%d pages)" % (os.path.basename(deck_path), len(deck)))
        except Exception as ex:
            notes.append("deck unreadable: %s" % str(ex)[:120])
    else:
        notes.append("no deck for this talk")
    samples = scan(src, start, end, deck, on_progress, loose=loose)
    if loose and deck is not None:
        notes.append("deck matched by layout alone (\"deck_match\": \"loose\"): builds show whole")
    segs = segments(samples) if cfg.get("slides_replace", True) else []
    shown = sum(s["t1"] - s["t0"] for s in segs) / max(1.0, end - start)
    night = night_mode(samples) if cfg.get("night_mode_fix", True) else {"on": False}
    screen = float(np.mean([s["screen"] >= 0.4 for s in samples])) if samples else 0.0
    colour = None
    if night.get("on"):
        if screen >= 0.7:
            colour = colour_fix(night["gains"])
            notes.append("night mode on the shared screen (%s): colours put back on the video stretches" % night["how"])
        else:
            notes.append("night mode suspected (%s), but the picture is mostly camera - colours left as they are"
                         % night["how"])
    if segs:
        notes.append("%d slide stretch(es) replaced by deck pages, %d%% of the talk" % (len(segs), shown * 100))
        covers = [s.get("cover") for s in samples if s.get("page") is not None and s.get("cover")]
        if covers:
            notes.append("the slides filled %d%% of the picture on average" % (np.median(covers) * 100))
    elif deck is not None:
        notes.append("no deck page recognised on screen - the video stays")
    differs = sum(1 for s in samples if s.get("why") == "laid out like a deck page, content differs")
    looked = sum(1 for s in samples if not str(s.get("why") or "").startswith("not searched"))
    near = segments([{"t": s["t"], "page": s.get("near")} for s in samples])
    if deck is not None and differs > 0.2 * max(1, looked):
        notes.append("the deck looks like ANOTHER VERSION of the one presented (%d%% of the frames looked at show a page "
                     "laid out like the deck's, with different content or fonts) - those stretches keep the video. "
                     "Look at the sheet: if only the fonts or line breaks differ, set \"deck_match\": \"loose\" in "
                     "this talk's cut; if the words differ, ask the speaker for the deck they presented"
                     % (100 * differs / max(1, looked)))
    return {"deck": deck_path, "pages": segs, "near": near, "night": night, "colour": colour, "shown": round(shown, 3),
            "screen": round(screen, 2), "samples": len(samples), "notes": notes,
            "sample_log": [{k: s.get(k) for k in ("t", "page", "cells", "diff", "cover", "hidden")} for s in samples]}


# ---- rendering ------------------------------------------------------------------------------------------------------
def variant(seg):
    """The image key of a stretch: the page, and the cells of it not shown yet (a build step)."""
    return (seg["page"], tuple(seg.get("hidden") or ()))


def paint(img, hidden, bg):
    """Paint the cells of a build step that aren't shown yet in the background colour (in place)."""
    gx, gy = FINE_GRID
    h, w = img.shape[:2]
    for c in hidden:
        cy, cx = divmod(c, gx)
        img[int(cy * h / gy):int(np.ceil((cy + 1) * h / gy)), int(cx * w / gx):int(np.ceil((cx + 1) * w / gx))] = bg
    return img


def write_png(rgb, path):
    """A PNG written by ffmpeg itself - ffmpeg 9 refused to read some of OpenCV's PNGs ("End of file") in the
    concat list, and it always reads its own."""
    media.safe_target(path)
    h, w = rgb.shape[:2]
    r = subprocess.run([media.tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-s", "%dx%d" % (w, h), "-i", "-", "-frames:v", "1", path],
                       input=np.ascontiguousarray(rgb).tobytes(), capture_output=True)
    if r.returncode != 0 or not os.path.exists(path):
        raise RuntimeError("writing %s failed: %s" % (path, r.stderr.decode("utf-8", "replace")[-200:]))
    return path


def page_images(deck_path, variants, W, H, folder):
    """The pages (and build steps) used, rendered sharp at the output size (W x H), letterboxed with the page's own
    edge colour; a build step has its not-yet-shown cells painted in that colour. Returns {variant: png path}."""
    import pypdfium2 as pdfium
    media.safe_target(folder)
    os.makedirs(folder, exist_ok=True)
    out = {}
    doc = pdfium.PdfDocument(deck_path)
    try:
        for k, (p, hidden) in enumerate(sorted(set(variants))):
            page = doc[p]
            pw, ph = page.get_width(), page.get_height()
            scale = min(W / pw, H / ph)
            img = page.render(scale=scale, rev_byteorder=True).to_numpy()[:, :, :3].copy()
            page.close()
            h, w = img.shape[:2]
            ring = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]])
            bg = np.median(ring, axis=0).astype(np.uint8)
            paint(img, hidden, bg)                                  # a build step: paint out what isn't shown yet
            canvas = np.empty((H, W, 3), np.uint8)
            canvas[:] = bg
            y, x = (H - h) // 2, (W - w) // 2
            canvas[y:y + h, x:x + w] = img[:H - y, :W - x]
            path = os.path.join(folder, "page%03d%s.png" % (p + 1, ("-step%d" % k) if hidden else ""))
            write_png(canvas, path)
            out[(p, hidden)] = path
    finally:
        doc.close()
    return out


def slide_track(plan_, start, end, images, folder):
    """An ffconcat list that plays the deck pages at their times (clip seconds, 0 = start) - the overlay input -
    and the enable expression for when it covers the video. Returns (list path, expression) or (None, None)."""
    segs = [s for s in plan_.get("pages") or [] if s["t1"] > start and s["t0"] < end]
    if not segs:
        return None, None
    lines, spans, t = ["ffconcat version 1.0"], [], 0.0
    blank = images[variant(segs[0])]
    for s in segs:
        a, b = max(s["t0"], start) - start, min(s["t1"], end) - start
        if a > t:
            lines += ["file '%s'" % _ff(blank), "duration %.3f" % (a - t)]
        lines += ["file '%s'" % _ff(images[variant(s)]), "duration %.3f" % (b - a)]
        spans.append((a, b))
        t = b
    if t < end - start:
        lines += ["file '%s'" % _ff(blank), "duration %.3f" % (end - start - t)]
    lines.append("file '%s'" % _ff(blank))                  # the concat demuxer needs the last file twice
    path = os.path.join(folder, "slides.ffconcat")
    media.safe_target(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    expr = "+".join("between(t,%.3f,%.3f)" % ab for ab in spans)
    return path, expr


def _ff(p):
    return os.path.abspath(p).replace("\\", "/").replace("'", r"'\''")


def contact_sheet(src, plan_, start, end, dst, deck_path=None, n=12):
    """Before / after strip for a human: n moments across the talk, the recording on the left and what the video
    will show on the right."""
    import cv2
    media.safe_target(dst)
    imgs = {}
    if deck_path and (plan_.get("pages") or plan_.get("near")):
        pages = render_pdf(deck_path, 480)
        imgs = {i: p for i, p in enumerate(pages)}
    rows = []
    for k in range(n):
        t = start + (end - start) * (k + 0.5) / n
        got = list(frames(src, t, t + 1.5, every=1, width=480))[:1]
        if not got:
            continue
        rgb = got[0][1]
        seg = next((s for s in plan_.get("pages") or [] if s["t0"] <= t < s["t1"]), None)
        nseg = None if seg else next((s for s in plan_.get("near") or [] if s["t0"] <= t < s["t1"]), None)
        if seg is None and nseg is not None and nseg["page"] in imgs:
            pg = imgs[nseg["page"]]
            canvas = np.zeros_like(rgb)
            sc = min(rgb.shape[1] / pg.shape[1], rgb.shape[0] / pg.shape[0])
            r = cv2.resize(pg, (int(pg.shape[1] * sc), int(pg.shape[0] * sc)), interpolation=cv2.INTER_AREA)
            y, x = (rgb.shape[0] - r.shape[0]) // 2, (rgb.shape[1] - r.shape[1]) // 2
            canvas[y:y + r.shape[0], x:x + r.shape[1]] = (r * 0.55).astype(np.uint8)
            after, label = canvas, "deck p%d by layout only - NOT used" % (nseg["page"] + 1)
        elif seg is not None and seg["page"] in imgs:
            pg = imgs[seg["page"]].copy()
            if seg.get("hidden"):
                ring = np.concatenate([pg[0], pg[-1], pg[:, 0], pg[:, -1]])
                paint(pg, seg["hidden"], np.median(ring, axis=0).astype(np.uint8))
            canvas = np.zeros_like(rgb)
            sc = min(rgb.shape[1] / pg.shape[1], rgb.shape[0] / pg.shape[0])
            r = cv2.resize(pg, (int(pg.shape[1] * sc), int(pg.shape[0] * sc)), interpolation=cv2.INTER_AREA)
            y, x = (rgb.shape[0] - r.shape[0]) // 2, (rgb.shape[1] - r.shape[1]) // 2
            canvas[y:y + r.shape[0], x:x + r.shape[1]] = r
            after, label = canvas, "deck page %d%s" % (seg["page"] + 1, " (build step)" if seg.get("hidden") else "")
        else:
            after, label = rgb, "video"
            if plan_.get("colour"):
                g = np.array(plan_["night"]["gains"], np.float32)
                k = (1 / np.maximum(g, 0.3))
                k = k / k.min()
                after = np.clip(rgb.astype(np.float32) * k, 0, 255).astype(np.uint8)
                label = "video, colours fixed"
        row = np.concatenate([rgb, np.full((rgb.shape[0], 8, 3), 255, np.uint8), after], axis=1).copy()
        cv2.putText(row, "%d:%02d" % (int(t) // 60, int(t) % 60), (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (255, 0, 0), 2)
        cv2.putText(row, label, (rgb.shape[1] + 16, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
        rows.append(row)
    if not rows:
        return None
    w = max(r.shape[1] for r in rows)
    rows = [np.pad(r, ((0, 6), (0, w - r.shape[1]), (0, 0)), constant_values=255) for r in rows]
    cv2.imwrite(dst, cv2.cvtColor(np.concatenate(rows, axis=0), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 80])
    return dst


def save(path, data):
    media.safe_target(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
