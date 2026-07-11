#!/usr/bin/env python3
import time
import os
import requests
import datetime
import glob
import sys

# Constants
EXPORTER_URL = "http://localhost:8000/metrics"
PROMETHEUS_URL = "http://localhost:9090/api/v1/query"
CSV_DIR = "/home/telmat/sec-xapp/csv"

def get_latest_mitigation_csv():
    files = glob.glob(os.path.join(CSV_DIR, "mitigation_events_*.csv"))
    if not files:
        # Create a mock file if none exists
        filename = os.path.join(CSV_DIR, f"mitigation_events_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        with open(filename, "w") as f:
            f.write("epoch_ms,action,rnti,ue_id,prb_limit,attack,confidence\n")
        return filename
    return max(files, key=os.path.getmtime)

def test_dashboard_latency():
    print("======================================================================")
    print("RUNNING DASHBOARD COMPLIANCE LATENCY TEST (SUB-OBJECTIVE 2 / CONSTRAINT 2)")
    print("======================================================================")
    
    csv_file = get_latest_mitigation_csv()
    print(f"Target CSV Log file: {csv_file}")
    
    # Generate a unique RNTI to avoid metric caching/collision
    test_rnti = str(int(time.time()) % 100000)
    print(f"Generated test RNTI: {test_rnti}")
    
    # 1. Record Start Time and Write Row to CSV
    t_start = time.time()
    mock_epoch_ms = int(t_start * 1000)
    
    # Write a simulated THROTTLE event
    with open(csv_file, "a") as f:
        f.write(f"{mock_epoch_ms},THROTTLE,{test_rnti},{test_rnti},25,ul_flood,0.99\n")
        f.flush()
        os.fsync(f.fileno())
    
    print(f"[*] Simulated THROTTLE event written to CSV at timestamp: {mock_epoch_ms}")

    # 2. Poll Exporter Metrics Endpoint (Port 8000)
    print("[*] Polling Exporter `/metrics` endpoint...")
    t_exporter_detect = None
    timeout = 10.0 # seconds
    
    while time.time() - t_start < timeout:
        try:
            r = requests.get(EXPORTER_URL, timeout=1.0)
            if r.status_code == 200:
                if f'xapp_ue_mitigation_active{{rnti="{test_rnti}"}} 1.0' in r.text:
                    t_exporter_detect = time.time()
                    break
        except Exception as e:
            pass
        time.sleep(0.05) # 50ms polling interval
        
    if t_exporter_detect is None:
        print("[FAIL] Exporter failed to expose new metrics within timeout!")
        sys.exit(1)
        
    exporter_latency = t_exporter_detect - t_start
    print(f"[SUCCESS] Exporter detected and registered event in: {exporter_latency:.4f} seconds")
    
    # 3. Poll Prometheus API Query Endpoint (Port 9090)
    print("[*] Polling Prometheus Query API...")
    t_prometheus_detect = None
    query_params = {
        'query': f'xapp_ue_mitigation_active{{rnti="{test_rnti}"}}'
    }
    
    while time.time() - t_start < timeout:
        try:
            r = requests.get(PROMETHEUS_URL, params=query_params, timeout=1.0)
            if r.status_code == 200:
                res = r.json()
                results = res.get('data', {}).get('result', [])
                if results:
                    val = results[0].get('value', [])
                    if val and float(val[1]) == 1.0:
                        t_prometheus_detect = time.time()
                        break
        except Exception as e:
            pass
        time.sleep(0.1) # 100ms polling interval
        
    if t_prometheus_detect is None:
        print("[FAIL] Prometheus failed to scrape metrics within timeout!")
        sys.exit(1)
        
    prometheus_latency = t_prometheus_detect - t_exporter_detect
    total_data_delay = t_prometheus_detect - t_start
    
    print(f"[SUCCESS] Prometheus scraped metrics in: {prometheus_latency:.4f} seconds")
    print("----------------------------------------------------------------------")
    print(f"Total E2E Data Update Delay: {total_data_delay:.4f} seconds")
    print("----------------------------------------------------------------------")
    
    # Assert ITU-T Compliance Constraint (T_total <= 5.0s)
    if total_data_delay <= 5.0:
        print(f"[PASS] Dashboard Compliance Met! Delay ({total_data_delay:.2f}s) <= Constraint (5.0s)")
        print("======================================================================")
        # Append a restore to keep clean state
        with open(csv_file, "a") as f:
            f.write(f"{int(time.time()*1000)},RESTORE,{test_rnti},{test_rnti},100,ul_flood,0.99\n")
            f.flush()
        sys.exit(0)
    else:
        print(f"[FAIL] Delay ({total_data_delay:.2f}s) exceeded constraint (5.0s)")
        print("======================================================================")
        sys.exit(1)

if __name__ == "__main__":
    test_dashboard_latency()
