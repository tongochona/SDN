from flask import Flask, render_template, jsonify, request
import csv
import os
from collections import defaultdict
import logging
import requests

app = Flask(__name__)

CSV_DIR = "SDN/web/data"

# Thiết lập logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

@app.route("/")
def index():
    switch_ids = get_switch_ids()
    return render_template("dashboard.html", switches=switch_ids)

@app.route("/api/port_stats")
def port_stats():
    dpid = request.args.get("dpid", "1")
    filepath = os.path.join(CSV_DIR, f"port_stats_{dpid}.csv")
    return jsonify(read_csv(filepath, ["timestamp", "port_no", "tx_packets", "tx_bytes", "rx_packets", "rx_bytes"]))

@app.route("/api/flow_stats")
def flow_stats():
    dpid = request.args.get("dpid", "1")
    filepath = os.path.join(CSV_DIR, f"flow_stats_{dpid}.csv")
    return jsonify(read_csv(filepath, ["timestamp", "in_port", "packet_count", "byte_count"]))

@app.route("/api/table_stats")
def table_stats():
    dpid = request.args.get("dpid", "1")
    filepath = os.path.join(CSV_DIR, f"table_stats_{dpid}.csv")
    return jsonify(read_csv(filepath, ["timestamp", "table_id", "active_count", "lookup_count", "matched_count"]))

@app.route("/api/queue_stats")
def queue_stats():
    dpid = request.args.get("dpid", "1")
    filepath = os.path.join(CSV_DIR, f"queue_stats_{dpid}.csv")
    return jsonify(read_csv(filepath, ["timestamp", "port_no", "queue_id", "tx_bytes", "tx_packets", "tx_errors"]))

@app.route("/api/meter_stats")
def meter_stats():
    dpid = request.args.get("dpid", "1")
    filepath = os.path.join(CSV_DIR, f"meter_stats_{dpid}.csv")
    return jsonify(read_csv(filepath, ["timestamp", "meter_id", "flow_count", "packet_in_count", "byte_in_count", "duration_sec"]))

@app.route("/api/all_bandwidth")
def network_bandwidth():
    switch_ids = get_switch_ids()
    timeline = defaultdict(float)
    port_counts = defaultdict(set)
    for dpid in switch_ids:
        filepath = os.path.join(CSV_DIR, f"port_stats_{dpid}.csv")
        if not os.path.exists(filepath):
            logger.warning(f"File not found: {filepath}")
            continue
        data = read_csv(filepath, ["timestamp", "port_no", "tx_bytes", "rx_bytes"])
        for entry in data:
            t = entry["timestamp"]
            t_rounded = round(t / 10) * 10
            tx = entry.get("tx_bytes", 0)
            rx = entry.get("rx_bytes", 0)
            delta_t = entry.get("delta_t", 10)
            if delta_t > 0:
                normalized_bytes = (tx + rx) * 10 / delta_t
                timeline[t_rounded] += normalized_bytes
            port_counts[t_rounded].add(f"{dpid}:{entry['port_no']}")
    
    total_ports = sum(len(set([f"{dpid}:{row['port_no']}" 
                              for row in read_csv(os.path.join(CSV_DIR, f"port_stats_{dpid}.csv"), 
                                                ["timestamp", "port_no"])])) 
                      for dpid in switch_ids)
    result = []
    for t, b in sorted(timeline.items()):
        if len(port_counts[t]) >= total_ports * 0.8:
            mbps = round(b * 8 / 1_000_000, 2)
            result.append({"timestamp": t, "mbps": mbps})
    
    return jsonify(result[-20:])

@app.route("/api/drop_stats")
def drop_stats():
    timeline = defaultdict(int)
    for dpid in get_switch_ids():
        filepath = os.path.join(CSV_DIR, f"flow_stats_{dpid}.csv")
        if not os.path.exists(filepath):
            continue
        data = read_csv(filepath, ["timestamp", "in_port", "out_port", "packet_count"])
        flow_drops = defaultdict(lambda: defaultdict(int))
        for entry in data:
            t = entry["timestamp"]
            t_rounded = round(t / 10) * 10
            in_port = entry.get("in_port", "0")
            out_port = entry.get("out_port", "0")
            packet_count = entry.get("packet_count", 0)
            if in_port != "-":
                flow_drops[t_rounded][(in_port, out_port)] += packet_count
        for t_rounded, flows in flow_drops.items():
            for (in_port, out_port), packet_count in flows.items():
                timeline[t_rounded] += packet_count
    result = [{"timestamp": t, "dropped": timeline[t]} for t in sorted(timeline)]
    return jsonify(result[-20:])

@app.route("/api/sflow_metrics")
def sflow_blackhole_metrics():
    try:
        metrics = {
            "ifinoctets": "Bytes In",
            "ifoutoctets": "Bytes Out",
            "ifindiscards": "Input Discards",
            "ifoutdiscards": "Output Discards",
            "nf.dstip": "Top Destination IPs",
            "nf.srcip": "Top Source IPs"
        }
        results = {}
        for metric, label in metrics.items():
            url = f"http://127.0.0.1:8008/metric/ALL/{metric}/json"
            try:
                resp = requests.get(url, timeout=5)
                resp.raise_for_status()  # Ném lỗi nếu status code không phải 200
                values = resp.json()
                processed = []
                if isinstance(values, list):
                    processed = [
                        {
                            "label": f"{v.get('agent', 'unknown')}:{v.get('dataSource', 'unknown')}",
                            "value": max(0, round(v.get("metricValue", 0), 2))
                        }
                        for v in values
                        if isinstance(v, dict)
                    ]
                else:
                    logger.warning(f"Unexpected sFlow data format for {metric}: {values}")
                results[metric] = {"name": label, "data": processed[:10]}
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to fetch sFlow metric {metric}: {e}")
                results[metric] = {"name": label, "data": []}
        return jsonify(results)
    except Exception as e:
        logger.error(f"Error fetching blackhole metrics: {e}")
        return jsonify({})

def read_csv(filepath, columns):
    raw_data = defaultdict(list)
    if os.path.exists(filepath):
        with open(filepath, newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = row.get("port_no", row.get("in_port", row.get("table_id", row.get("queue_id", row.get("meter_id", "0")))))
                raw_data[key].append(row)

    timeline = defaultdict(lambda: defaultdict(dict))
    for key, rows in raw_data.items():
        rows.sort(key=lambda x: float(x["timestamp"]))
        for i in range(len(rows)):
            t = float(rows[i]["timestamp"])
            t_rounded = round(t / 10) * 10
            for col in columns:
                if col in ["port_no", "in_port", "out_port", "table_id", "queue_id", "meter_id"]:
                    timeline[t_rounded][key][col] = rows[i][col]
            delta_t = 10.0
            if i == 0:
                for col in columns:
                    if col not in ["timestamp", "port_no", "in_port", "out_port", "table_id", "queue_id", "meter_id"]:
                        timeline[t_rounded][key][col] = 0.0
            else:
                delta_t = float(rows[i]["timestamp"]) - float(rows[i-1]["timestamp"])
                for col in columns:
                    if col not in ["timestamp", "port_no", "in_port", "out_port", "table_id", "queue_id", "meter_id"]:
                        try:
                            delta_value = float(rows[i][col]) - float(rows[i-1][col])
                            delta_value = max(0, delta_value)
                            timeline[t_rounded][key][col] = delta_value
                        except:
                            timeline[t_rounded][key][col] = 0.0
            timeline[t_rounded][key]["delta_t"] = delta_t

    data = []
    for t in sorted(timeline.keys())[-20:]:
        for key, values in timeline[t].items():
            d = {"timestamp": t}
            d.update(values)
            if "tx_bytes" in values:
                d["tx_mbps"] = round(float(values["tx_bytes"]) * 8 / 1_000_000, 2)
            if "rx_bytes" in values:
                d["rx_mbps"] = round(float(values["rx_bytes"]) * 8 / 1_000_000, 2)
            if "byte_count" in values:
                d["mbps"] = round(float(values["byte_count"]) * 8 / 1_000_000, 2)
            if "byte_in_count" in values:
                d["in_mbps"] = round(float(values["byte_in_count"]) * 8 / 1_000_000, 2)
            data.append(d)
    return data

def get_switch_ids():
    ids = set()
    for filename in os.listdir(CSV_DIR):
        if filename.startswith("port_stats_") and filename.endswith(".csv"):
            dpid = filename.replace("port_stats_", "").replace(".csv", "")
            ids.add(dpid)
    return sorted(list(ids))

if __name__ == "__main__":
    app.run(debug=True)
