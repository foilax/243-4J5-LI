import serial
import time
import sys

port = '/dev/ttyACM0'
baud = 115200

try:
    ser = serial.Serial(port, baud, timeout=1)
except Exception as e:
    print(f"Error opening port: {e}")
    sys.exit(1)

print("Resetting ESP32...")
# Toggle DTR/RTS to reset
ser.dtr = False
ser.rts = False
time.sleep(0.1)

# Assert Reset (EN=Low)
ser.dtr = False
ser.rts = True
time.sleep(0.1)

# Release Reset (EN=High)
ser.rts = False
ser.dtr = False

print("Monitoring output...")
start_time = time.time()
while time.time() - start_time < 60:
    if ser.in_waiting:
        try:
            line = ser.readline().decode('utf-8', errors='replace').strip()
            if line:
                print(line)
        except Exception as e:
            print(f"Error reading: {e}")
    time.sleep(0.01)

ser.close()
