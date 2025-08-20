import importlib
import sys
import subprocess

# ---------- 1. Check for missing packages ----------
required_packages = ["pynput"]
missing_packages = []

for pkg in required_packages:
    try:
        importlib.import_module(pkg)
    except ImportError:
        missing_packages.append(pkg)

if missing_packages:
    print("The following packages are missing:", ", ".join(missing_packages))
    print("You can install them all with this command:")
    print(f"pip install {' '.join(missing_packages)}")
    sys.exit(1)

# ---------- 2. Standard imports ----------
import argparse
import csv
import os
import ctypes
from datetime import datetime
from pynput import keyboard
import time
import threading
import random  # for shuffling rows
import glob
import threading
import json
import ast

# ---------- 3. Command-line argument ----------
parser = argparse.ArgumentParser(description="Keylogger for bigram and trigram durations")
parser.add_argument("--output-dir", default="./individual_runs", help="Directory to store log files")
args = parser.parse_args()
output_dir = args.output_dir
os.makedirs(output_dir, exist_ok=True)

# ---------- 4. File setup ----------
timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
bigram_file = os.path.join(output_dir, f"bigrams_{timestamp}.csv")
trigram_file = os.path.join(output_dir, f"trigrams_{timestamp}.csv")

bigram_durations = {}   # {bigram: [durations]}
trigram_durations = {}  # {trigram: [durations]}
key_buffer = []         # store recent keys
last_time = None
last_flush = time.time()

# create the lock before any thread uses it (fixes race on undefined 'lock')
lock = threading.Lock()

# ---------- 5. CSV Initialization ----------
def init_csv_files():
    for file_path, header in [(bigram_file, ["bigram", "durations"]), 
                              (trigram_file, ["trigram", "durations"])]:
        if not os.path.exists(file_path):
            with open(file_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(header)

# ---------- 6. Flush function with row shuffling ----------
def flush_to_csv():
    global last_flush
    with lock:
        # ---------- Bigram CSV ----------
        rows = []
        for bigram, durations in bigram_durations.items():
            # Shuffle the durations to obscure the order of typing for privacy
            shuffled_durations = durations[:]  # make a copy
            random.shuffle(shuffled_durations)  # shuffle durations for privacy
            # store as JSON for safe round-tripping
            rows.append([bigram, json.dumps(shuffled_durations)])
        
        # Shuffle the rows themselves to further prevent reconstructing typing sequences
        random.shuffle(rows)  # shuffle rows for privacy
        tmp_path = bigram_file + ".tmp"
        with open(tmp_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["bigram", "durations"])
            writer.writerows(rows)
        os.replace(tmp_path, bigram_file)  # atomic replace

        # ---------- Trigram CSV ----------
        rows = []
        for trigram, durations in trigram_durations.items():
            # Shuffle the durations to obscure typing order
            shuffled_durations = durations[:]  # copy
            random.shuffle(shuffled_durations)  # shuffle durations for privacy
            # store as JSON for safe round-tripping
            rows.append([trigram, json.dumps(shuffled_durations)])

        # Shuffle the rows for additional privacy
        random.shuffle(rows)
        tmp_path = trigram_file + ".tmp"
        with open(tmp_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["trigram", "durations"])
            writer.writerows(rows)
        os.replace(tmp_path, trigram_file)  # atomic replace

    last_flush = time.time()

# ---------- 7. Periodic flush ----------
def periodic_flush():
    while True:
        if time.time() - last_flush >= 10:  # flush every 10 seconds
            flush_to_csv()
        time.sleep(1)

flush_thread = threading.Thread(target=periodic_flush, daemon=True)
flush_thread.start()

# ---------- 8. Helper: key to string ----------
def key_to_str(key):
    """Convert key to readable string, keeping Shift/Ctrl/Alt separate.
       Characters are converted to lowercase to reflect the key pressed, not resulting char."""
    if hasattr(key, 'char') and key.char is not None:
        return key.char.lower()  # normalize char to lowercase
    else:
        return f"<{key.name}>" if hasattr(key, 'name') else str(key)

# ---------- 9. Key press handler ----------
def on_press(key):
    global last_time, key_buffer
    current_time = time.time() * 1000  # milliseconds
    key_str = key_to_str(key)

    if last_time is not None and key_buffer:
        interval = current_time - last_time
        prev_key = key_buffer[-1]
        print(f"Duration: {prev_key} → {key_str} = {interval:.2f} ms")

        with lock:
            # Bigram
            bigram = prev_key + key_str
            bigram_durations.setdefault(bigram, []).append(interval)

            # Trigram
            if len(key_buffer) >= 2:
                trigram = key_buffer[-2] + prev_key + key_str
                prev_bigram_interval = bigram_durations.get(key_buffer[-2] + prev_key, [0])[-1]
                trigram_duration = prev_bigram_interval + interval
                trigram_durations.setdefault(trigram, []).append(trigram_duration)

                if key_buffer[-2] in ("<ctrl>", "<ctrl_l>", "<ctrl_r>") and prev_key in ("<shift>", "<shift_l>", "<shift_r>") and key_str == "<esc>":
                    print("\nDetected CTRL → SHIFT → ESC trigram. Exiting the script.\n")
                    return False

    # Update buffer
    key_buffer.append(key_str)
    if len(key_buffer) > 2:
        key_buffer.pop(0)

    last_time = current_time

# ---------- 10. Listener ----------
init_csv_files()
print("\nLogging started. Write a CTRL → SHIFT → ESC trigram to stop.\n")

try:
    # lock already created above
    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()
except KeyboardInterrupt:
    pass  # graceful stop

# ---------- 11. Merge all past runs into one combined file ----------
def merge_all_files(output_dir, pattern, combined_filename):
    """
    Merge all CSV files matching the pattern into a single combined CSV.
    Each bigram/trigram gets one row with durations merged from all runs.
    Durations and rows are shuffled to improve privacy and make sharing easier.
    """

    combined_data = {}

    # Collect all files matching the pattern (e.g., bigrams_*.csv)
    for file_path in glob.glob(os.path.join(output_dir, pattern)):
        if file_path.endswith(combined_filename):  # skip the final combined file itself
            continue
        with open(file_path, newline='') as f:
            reader = csv.reader(f)
            header = next(reader, None)  # skip header
            for row in reader:
                if len(row) != 2:
                    continue
                key, durations_str = row
                try:
                    # Prefer JSON, fallback to safe literal_eval for legacy rows
                    if isinstance(durations_str, str):
                        try:
                            durations = json.loads(durations_str)
                        except json.JSONDecodeError:
                            durations = ast.literal_eval(durations_str)
                    else:
                        durations = durations_str
                    if not isinstance(durations, list):
                        durations = [durations]
                except Exception:
                    continue

                combined_data.setdefault(key, []).extend(durations)

    # Shuffle durations per key
    rows = []
    for key, durations in combined_data.items():
        shuffled_durations = durations[:]
        random.shuffle(shuffled_durations)
        rows.append([key, json.dumps(shuffled_durations)])

    # Shuffle rows to further hide sequence info
    random.shuffle(rows)

    # Write final combined file
    combined_path = os.path.join(output_dir, combined_filename)
    tmp_path = combined_path + ".tmp"
    with open(tmp_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["key", "durations"])
        writer.writerows(rows)
    os.replace(tmp_path, combined_path)

    print(f"Combined file saved: {combined_path}")


# ---------- 12. Final save and merge ----------
flush_to_csv()

# Merge all bigram files into one big final file
merge_all_files(output_dir, "bigrams_*.csv", "bigrams_all.csv")

# Merge all trigram files into one big final file
merge_all_files(output_dir, "trigrams_*.csv", "trigrams_all.csv")
print("\nThank you for contributing your duration-data.")
