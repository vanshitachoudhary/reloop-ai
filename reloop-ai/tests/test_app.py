import json, os, sys, types, unittest
from decimal import Decimal
from unittest import mock

class FakeTable:
    def __init__(self): self.items = {}
    def put_item(self, Item): self.items[Item["id"]] = dict(Item)
    def get_item(self, Key): return {"Item": self.items.get(Key["id"])}
    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
        self.items[Key["id"]]["s"] = ExpressionAttributeValues[":s"]
        if ":c" in ExpressionAttributeValues: self.items[Key["id"]]["ct"] = ExpressionAttributeValues[":c"]
        if ":d" in ExpressionAttributeValues: self.items[Key["id"]]["sd"] = ExpressionAttributeValues[":d"]

fake = FakeTable()
sys.modules["boto3"] = types.SimpleNamespace(client=lambda *a: mock.Mock(),
    resource=lambda *a: types.SimpleNamespace(Table=lambda n: fake))
os.environ.update(TABLE="t", BUCKET="b")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import app

def call(method, path, body=None):
    r = app.handler({"requestContext": {"http": {"method": method}}, "rawPath": path, "body": json.dumps(body or {})}, None)
    return r["statusCode"], json.loads(r["body"])

class Tests(unittest.TestCase):
    def test_classify(self):
        r = app.classify([{"Name": "Bottle", "Confidence": 93, "Parents": []}, {"Name": "Plastic", "Confidence": 80}])
        self.assertEqual(r["category"], "plastic")
    def test_low_confidence_is_unknown(self):
        self.assertEqual(app.classify([{"Name": "Can", "Confidence": 40}])["category"], "unknown")
    def test_specific_label_beats_generic_bottle(self):
        r = app.classify([{"Name": "Bottle", "Confidence": 95}, {"Name": "Glass", "Confidence": 80}])
        self.assertEqual(r["category"], "glass")
        self.assertTrue(r["review"])  # close call -> ask the user to confirm
        self.assertEqual({a["category"] for a in r["alts"]}, {"glass", "plastic"})
    def test_match(self):
        self.assertEqual(app.match("ewaste", 1), "EcoE Recyclers")
    def test_validation(self):
        self.assertEqual(call("POST", "/listings", {"i": "x", "c": "plastic", "q": 0})[0], 400)
        self.assertEqual(call("POST", "/listings", {"i": "x", "c": "gold", "q": 5})[0], 400)
    def test_lifecycle(self):
        code, rec = call("POST", "/listings", {"i": "Bottles", "c": "plastic", "q": 2.5, "z": 1})
        self.assertEqual((code, rec["r"]), (201, "GreenCycle Hub"))
        self.assertEqual(call("PATCH", "/listings/" + rec["id"], {"s": 2})[0], 400)  # cannot skip a step
        self.assertEqual(call("PATCH", "/listings/" + rec["id"], {"s": 1})[0], 200)
        self.assertEqual(call("PATCH", "/listings/" + rec["id"], {"s": 2})[0], 200)
        self.assertIn("ct", fake.items[rec["id"]])

    def test_preferred_recycler_and_slot(self):
        _, rec = call("POST", "/listings", {"i": "Glass", "c": "glass", "q": 4, "z": 1, "r": "ClearGlass Works"})
        self.assertEqual(rec["r"], "ClearGlass Works")
        _, bad = call("POST", "/listings", {"i": "Glass", "c": "glass", "q": 4, "z": 2, "r": "MetalMart Scrap"})
        self.assertEqual(bad["r"], "ClearGlass Works")  # recycler must accept the category
        call("PATCH", "/listings/" + rec["id"], {"s": 1, "sd": "Mon 12 Oct - Morning"})
        self.assertEqual(fake.items[rec["id"]]["sd"], "Mon 12 Oct - Morning")

if __name__ == "__main__": unittest.main()
