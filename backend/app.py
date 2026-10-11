"""ReLoop AI backend: one Lambda behind API Gateway (HTTP API).
POST /scan            image (base64) -> S3 -> Amazon Rekognition -> waste category
GET  /listings        list saved listings (DynamoDB)
POST /listings        create listing + recycler match
PATCH /listings/{id}  advance status (0 Matched, 1 Scheduled, 2 Completed)
"""
import base64, json, os, time, uuid
from decimal import Decimal
import boto3

s3 = boto3.client("s3")
rek = boto3.client("rekognition")
table = boto3.resource("dynamodb").Table(os.environ["TABLE"])
BUCKET = os.environ["BUCKET"]

# Demo recycler records (NOT real partners).
RECYCLERS = [
    {"n": "GreenCycle Hub", "acc": ["plastic", "paper"], "z": 1},
    {"n": "MetalMart Scrap", "acc": ["metal"], "z": 2},
    {"n": "EcoE Recyclers", "acc": ["ewaste"], "z": 3},
    {"n": "ClearGlass Works", "acc": ["glass", "plastic"], "z": 2},
    {"n": "PaperLoop Co-op", "acc": ["paper"], "z": 2},
]
LABELS = {
    "plastic": {"Plastic", "Plastic Bottle", "Water Bottle", "Bottle", "Plastic Bag", "Bag", "Container", "Cup",
                "Plastic Wrap", "Bottle Cap", "Straw", "Packaging"},
    "paper": {"Paper", "Cardboard", "Carton", "Box", "Newspaper", "Book", "Paper Bag", "Envelope", "Magazine",
              "Paper Towel", "Cardboard Box"},
    "metal": {"Can", "Tin", "Tin Can", "Soda Can", "Aluminium", "Aluminum", "Metal", "Steel", "Aluminium Foil"},
    "glass": {"Glass", "Jar", "Glass Bottle", "Wine Glass", "Beer Bottle", "Wine Bottle", "Goblet"},
    "ewaste": {"Electronics", "Mobile Phone", "Phone", "Smartphone", "Laptop", "Computer", "Battery", "Cable",
               "Charger", "Computer Hardware", "Circuit Board", "Remote Control", "Mouse", "Keyboard",
               "Computer Keyboard", "Monitor", "Headphones", "Tablet Computer", "Electrical Device", "Speaker"},
}
# Generic labels are weaker evidence than material-specific ones (a "Bottle" may be glass or plastic).
GENERIC = {"Bottle", "Bag", "Container", "Box", "Cup", "Can", "Metal", "Book", "Packaging"}


def resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, default=lambda o: (int(o) if o == int(o) else float(o)) if isinstance(o, Decimal) else str(o))}


def classify(labels):
    scores = {}
    for lb in labels:
        keys = {lb["Name"]} | {p["Name"] for p in lb.get("Parents", [])}
        w = 0.75 if lb["Name"] in GENERIC else 1.0
        for cat, vocab in LABELS.items():
            if keys & vocab:
                scores[cat] = max(scores.get(cat, 0), lb["Confidence"] * w)
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    names = [lb["Name"] for lb in labels]
    out = {"item": names[0] if names else "", "labels": names[:5],
           "alts": [{"category": c, "confidence": round(v)} for c, v in ranked[:3]]}
    if not ranked or ranked[0][1] < 55:
        return {**out, "category": "unknown", "confidence": 0, "review": True}
    close = len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 10
    return {**out, "category": ranked[0][0], "confidence": round(ranked[0][1]),
            "review": ranked[0][1] < 75 or close}


def scan(body):
    try:
        raw = base64.b64decode(body["image"], validate=True)
    except Exception:
        return resp(400, {"error": "invalid image"})
    if not raw or len(raw) > 4_000_000:
        return resp(400, {"error": "image must be under 4 MB"})
    try:
        s3.put_object(Bucket=BUCKET, Key=f"scans/{uuid.uuid4()}.jpg", Body=raw, ContentType="image/jpeg")
        out = rek.detect_labels(Image={"Bytes": raw}, MaxLabels=15, MinConfidence=50)
    except Exception as exc:  # Rekognition rejects non JPEG/PNG; surface a clean error
        print("scan failed:", repr(exc))
        return resp(502, {"error": "scan unavailable"})
    return resp(200, classify(out["Labels"]))


def match(cat, zone):
    ok = [r for r in RECYCLERS if cat in r["acc"]]
    return min(ok, key=lambda r: abs(r["z"] - zone))["n"] if ok else "No recycler"


def pick(cat, zone, pref):
    if any(r["n"] == pref and cat in r["acc"] for r in RECYCLERS):
        return pref
    return match(cat, zone)


def create(body):
    try:
        cat, q, zone = body["c"], float(body["q"]), int(body.get("z", 2))
        item = str(body["i"]).strip()[:100]
    except (KeyError, ValueError, TypeError):
        return resp(400, {"error": "invalid listing"})
    if cat not in LABELS or not item or not (0 < q <= 5000):
        return resp(400, {"error": "invalid listing"})
    rec = {"id": f"l{int(time.time()*1000)}", "i": item, "c": cat, "q": Decimal(str(q)),
           "z": zone, "r": pick(cat, zone, body.get("r")), "s": 0, "t": int(time.time() * 1000)}
    table.put_item(Item=rec)
    return resp(201, rec)


def advance(lid, body):
    try:
        s = int(body["s"])
    except (KeyError, ValueError, TypeError):
        return resp(400, {"error": "invalid status"})
    sd = body.get("sd")
    sd = sd[:40] if isinstance(sd, str) and sd.strip() else None
    cur = table.get_item(Key={"id": lid}).get("Item")
    if not cur:
        return resp(404, {"error": "not found"})
    if s != int(cur["s"]) + 1 or s > 2:  # only one step forward
        return resp(400, {"error": "invalid transition"})
    expr, vals = "SET s = :s", {":s": s}
    if s == 2:
        expr += ", ct = :c"
        vals[":c"] = int(time.time() * 1000)
    if s == 1 and sd:
        expr += ", sd = :d"
        vals[":d"] = sd
    table.update_item(Key={"id": lid}, UpdateExpression=expr, ExpressionAttributeValues=vals)
    return resp(200, {"id": lid, "s": s})


def handler(event, _ctx):
    try:
        return _route(event)
    except Exception as exc:
        print("unhandled:", repr(exc))
        return resp(500, {"error": "server error"})


def _route(event):
    m = event["requestContext"]["http"]["method"]
    path = event["rawPath"]
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return resp(400, {"error": "bad json"})
    if m == "POST" and path.endswith("/scan"):
        return scan(body)
    if path.endswith("/listings"):
        if m == "GET":
            items = table.scan(Limit=200)["Items"]
            return resp(200, sorted(items, key=lambda x: -int(x.get("t", 0))))
        if m == "POST":
            return create(body)
    if m == "PATCH" and "/listings/" in path:
        return advance(path.rsplit("/", 1)[1], body)
    return resp(404, {"error": "not found"})
