#!/usr/bin/env python3
"""
Weather Station Data Collector
Reads serial data from weather station and sends to InfluxDB2
"""

import serial
import json
import time
import logging
from datetime import datetime
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import threading
import sys
import os

# Configuration
SERIAL_PORT = '/dev/tty.usbmodem1101'  # Adjust based on your setup
SERIAL_BAUD = 9600
INFLUXDB_URL = 'http://weather.local:8086'
INFLUXDB_TOKEN = 'n9Kn1y9G2Tsq85Xhkh-EP_-oBSs7GXDXx33SsrSshFYcIcfa7nTrv3oYAWF9Twz3MCfsL2rOomPtKv1iyJ_nKA=='  # Get from InfluxDB2 setup
INFLUXDB_ORG = 'rotse-test'
INFLUXDB_BUCKET = 'weather'

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('weather_station.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class WeatherStationCollector:
    def __init__(self):
        self.running = False
        self.serial_conn = None
        self.influx_client = None
        self.write_api = None

    def setup_serial(self):
        """Initialize serial connection"""
        try:
            self.serial_conn = serial.Serial(
                port=SERIAL_PORT,
                baudrate=SERIAL_BAUD,
                timeout=5
            )
            logger.info(f"Serial connection established on {SERIAL_PORT}")
            return True
        except Exception as e:
            logger.error(f"Serial connection failed ... {e}")
            return False

    def setup_influxdb(self):
        """Initialize InfluxDB connection"""
        try:
            self.influx_client = InfluxDBClient(
                url=INFLUXDB_URL,
                token=INFLUXDB_TOKEN,
                org=INFLUXDB_ORG
            )
            self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)

            # Test connection
            buckets = self.influx_client.buckets_api().find_buckets()
            logger.info("InfluxDB connection established")
            return True
        except Exception as e:
            logger.error(f"InfluxDB connection failed: {e}")
            return False

    def parse_weather_data(self, data_line):
        """
        Parse weather station data
        Expected format: "T:22.6 H:50.1 P:1011.9 WS:0.00"
        """
        try:
            # Remove any whitespace and split by space
            data_line = data_line.strip()

            # Parse key:value pairs separated by spaces
            data_dict = {}
            field_mapping = {
                'T': 'temperature',
                'H': 'humidity',
                'P': 'pressure',
                'WS': 'windspeed'
            }

            for pair in data_line.split(' '):
                if ':' in pair:
                    key, value = pair.split(':', 1)
                    key = key.strip()

                    # Map short keys to descriptive names
                    field_name = field_mapping.get(key, key.lower())

                    try:
                        data_dict[field_name] = float(value.strip())
                    except ValueError:
                        logger.warning(f"Could not convert {key}:{value} to float")
                        continue

            return data_dict if data_dict else None

        except Exception as e:
            logger.error(f"Error parsing data: {e}")
            return None

    def write_to_influxdb(self, data):
        """Write data to InfluxDB"""
        try:
            # Create a point for each measurement
            points = []

            for field, value in data.items():
                if isinstance(value, (int, float)):
                    point = Point("weather") \
                        .tag("location", "default") \
                        .field(field, value) \
                        .time(datetime.utcnow(), WritePrecision.NS)
                    points.append(point)

            if points:
                self.write_api.write(bucket=INFLUXDB_BUCKET, record=points)
                logger.info(f"Data written to InfluxDB: {data}")

        except Exception as e:
            logger.error(f"Error writing to InfluxDB: {e}")

    def collect_data(self):
        """Main data collection loop"""
        logger.info("Starting data collection...")

        while self.running:
            try:
                if self.serial_conn and self.serial_conn.in_waiting > 0:
                    line = self.serial_conn.readline().decode('utf-8').strip()

                    if line:
                        logger.debug(f"Received: {line}")

                        # Parse the data
                        parsed_data = self.parse_weather_data(line)

                        if parsed_data:
                            # Write to InfluxDB
                            self.write_to_influxdb(parsed_data)

                time.sleep(0.1)  # Small delay to prevent excessive CPU usage

            except serial.SerialException as e:
                logger.error(f"Serial error: {e}")
                # Try to reconnect
                time.sleep(5)
                self.setup_serial()

            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                time.sleep(1)

    def start(self):
        """Start the collector"""
        logger.info("Starting weather station collector...")

        # Setup connections
        if not self.setup_serial():
            return False

        if not self.setup_influxdb():
            return False

        self.running = True

        # Start data collection in a separate thread
        self.data_thread = threading.Thread(target=self.collect_data)
        self.data_thread.daemon = True
        self.data_thread.start()

        return True

    def stop(self):
        """Stop the collector"""
        logger.info("Stopping weather station collector...")
        self.running = False

        if self.serial_conn:
            self.serial_conn.close()

        if self.influx_client:
            self.influx_client.close()


def main():
    collector = WeatherStationCollector()

    try:
        if collector.start():
            logger.info("Weather station collector started successfully")

            # Keep the main thread alive
            while True:
                time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        collector.stop()


if __name__ == "__main__":
    main()