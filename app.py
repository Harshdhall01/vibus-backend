"""
ViBus - Complete Backend API
--------------------------------
Full Flask API: original endpoints (/search, /bus/<id>, /autocomplete,
/health) PLUS the new /api/* endpoints shaped exactly for the frontend.

HOW TO RUN:
    1. Install dependencies:
       pip install flask flask-cors pymongo

    2. Your real database password is already filled in below.

    3. Run:
       python app.py

    4. API available at http://localhost:5000
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from datetime import datetime
import math
import os
from dotenv import load_dotenv

load_dotenv()  # reads variables from a local .env file, if present (local dev only)

DB_PASSWORD = os.environ.get("DB_PASSWORD")
if not DB_PASSWORD:
    raise RuntimeError(
        "DB_PASSWORD environment variable is not set. "
        "For local development, create a .env file with: DB_PASSWORD=your_password_here"
    )
CONNECTION_STRING = (
    f"mongodb+srv://dhallharsh2006:{DB_PASSWORD}"
    f"@cluster0.grznwjk.mongodb.net/?appName=Cluster0"
)

app = Flask(__name__)
CORS(app, origins=["https://vibus-frontend.vercel.app"])

client = MongoClient(CONNECTION_STRING)
db = client["hbus"]
buses_collection = db["buses"]


def normalize(name):
    return name.strip().lower()


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def diff_minutes(hhmm_a, hhmm_b):
    ah, am = map(int, hhmm_a.split(":"))
    bh, bm = map(int, hhmm_b.split(":"))
    d = (bh * 60 + bm) - (ah * 60 + am)
    if d < 0:
        d += 1440
    return d


def compute_distance_km(stops):
    total = 0.0
    FALLBACK_HOP_KM = 62
    for i in range(len(stops) - 1):
        a, b = stops[i], stops[i + 1]
        if a.get("latitude") and b.get("latitude"):
            total += haversine_km(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
        else:
            total += FALLBACK_HOP_KM
    return round(total)


def bus_to_summary(bus):
    stops = bus["stops"]
    dep = bus["departure_time"]
    arr = stops[-1]["estimated_time"]
    return {
        "id": bus["bus_id"],
        "from": stops[0]["name"],
        "to": stops[-1]["name"],
        "operator": bus["bus_type"],
        "ac": bus["is_ac"],
        "distanceKm": bus["distance_km"] or compute_distance_km(stops),
        "dep": dep,
        "arr": arr,
        "durationMin": diff_minutes(dep, arr),
        "stops": None,
    }


def bus_to_detail(bus):
    stops = bus["stops"]
    dep = bus["departure_time"]
    arr = stops[-1]["estimated_time"]

    now = datetime.now().strftime("%H:%M")
    now_minutes = diff_minutes("00:00", now)
    dep_minutes = diff_minutes("00:00", dep)
    arr_minutes = diff_minutes("00:00", arr)
    if arr_minutes < dep_minutes:
        arr_minutes += 1440
    bus_in_progress = dep_minutes <= now_minutes <= arr_minutes or (
        arr_minutes > 1440 and now_minutes <= arr_minutes - 1440
    )

    live_stop_index = None
    if bus_in_progress:
        for i, s in enumerate(stops):
            stop_minutes = diff_minutes("00:00", s["estimated_time"])
            if stop_minutes < dep_minutes:
                stop_minutes += 1440
            if stop_minutes >= now_minutes:
                live_stop_index = i
                break

    out_stops = []
    for i, s in enumerate(stops):
        tag = "Departure" if i == 0 else ("Arrival" if i == len(stops) - 1 else "")
        sub = f"{s['name']} Bus Stand" if i == 0 else (f"{s['name']} ISBT" if i == len(stops) - 1 else "")
        stop_obj = {
            "seq": i + 1,
            "name": s["name"],
            "sub": sub,
            "tag": tag,
            "time": s["estimated_time"],
        }
        if live_stop_index == i:
            stop_obj["isLive"] = True
            stop_obj["estimate"] = s["estimated_time"]
        out_stops.append(stop_obj)

    return {
        "id": bus["bus_id"],
        "from": stops[0]["name"],
        "to": stops[-1]["name"],
        "operator": bus["bus_type"],
        "ac": bus["is_ac"],
        "distanceKm": bus["distance_km"] or compute_distance_km(stops),
        "dep": dep,
        "arr": arr,
        "durationMin": diff_minutes(dep, arr),
        "stops": out_stops,
    }


# ---------------------------------------------------------------------
# Original endpoints
# ---------------------------------------------------------------------

@app.route("/health")
def health():
    try:
        client.admin.command("ping")
        count = buses_collection.count_documents({})
        return jsonify({"status": "ok", "total_buses_in_db": count})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/search")
def search():
    from_stop = request.args.get("from", "").strip()
    to_stop = request.args.get("to", "").strip()

    if not from_stop or not to_stop:
        return jsonify({"error": "Both 'from' and 'to' query parameters are required"}), 400

    from_norm = normalize(from_stop)
    to_norm = normalize(to_stop)

    candidates = buses_collection.find({
        "stops.name": {"$regex": f"^{from_stop}$", "$options": "i"}
    })

    results = []
    for bus in candidates:
        stop_order = {normalize(s["name"]): s["order"] for s in bus["stops"]}
        if from_norm in stop_order and to_norm in stop_order:
            if stop_order[to_norm] > stop_order[from_norm]:
                bus["_id"] = str(bus["_id"])
                results.append(bus)

    results.sort(key=lambda b: b["departure_time"])

    return jsonify({
        "from": from_stop,
        "to": to_stop,
        "count": len(results),
        "buses": results,
    })


@app.route("/bus/<bus_id>")
def get_bus(bus_id):
    bus = buses_collection.find_one({"bus_id": bus_id})
    if not bus:
        return jsonify({"error": "Bus not found"}), 404
    bus["_id"] = str(bus["_id"])
    return jsonify(bus)


@app.route("/autocomplete")
def autocomplete():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"suggestions": []})

    pipeline = [
        {"$unwind": "$stops"},
        {"$match": {"stops.name": {"$regex": query, "$options": "i"}}},
        {"$group": {"_id": "$stops.name"}},
        {"$limit": 10},
    ]
    results = buses_collection.aggregate(pipeline)
    suggestions = sorted(set(r["_id"] for r in results))

    return jsonify({"suggestions": suggestions})


# ---------------------------------------------------------------------
# New endpoints - shaped exactly for the frontend's BusAPI
# ---------------------------------------------------------------------

@app.route("/api/cities")
def api_cities():
    query = request.args.get("q", "").strip()
    limit = int(request.args.get("limit", 5))
    if not query:
        return jsonify([])

    query_norm = normalize(query)

    pipeline = [
        {"$unwind": "$stops"},
        {"$match": {"stops.name": {"$regex": query, "$options": "i"}}},
        {"$group": {"_id": "$stops.name"}},
    ]
    results = buses_collection.aggregate(pipeline)
    all_names = sorted(set(r["_id"] for r in results))

    def rank(name):
        name_norm = normalize(name)
        if name_norm == query_norm:
            return 0
        if name_norm.startswith(query_norm):
            return 1
        return 2

    all_names.sort(key=lambda n: (rank(n), n))
    return jsonify(all_names[:limit])


@app.route("/api/buses")
def api_buses_search():
    from_stop = request.args.get("from", "").strip()
    to_stop = request.args.get("to", "").strip()

    if not from_stop or not to_stop:
        return jsonify([])

    from_norm = normalize(from_stop)
    to_norm = normalize(to_stop)

    candidates = buses_collection.find({
        "stops.name": {"$regex": f"^{from_stop}$", "$options": "i"}
    })

    results = []
    for bus in candidates:
        stop_order = {normalize(s["name"]): s["order"] for s in bus["stops"]}
        if from_norm in stop_order and to_norm in stop_order:
            if stop_order[to_norm] > stop_order[from_norm]:
                results.append(bus_to_summary(bus))

    results.sort(key=lambda b: b["dep"])
    return jsonify(results)


@app.route("/api/buses/<bus_id>")
def api_bus_detail(bus_id):
    bus = buses_collection.find_one({"bus_id": bus_id})
    if not bus:
        return jsonify({"error": "Bus not found"}), 404
    return jsonify(bus_to_detail(bus))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting ViBus API on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)