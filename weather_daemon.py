#!/usr/bin/env python3
"""
Weather Station Daemon
Reads serial data from weather station and sends to InfluxDB2
Designed to run as a systemd service on Raspberry Pi
"""

import serial
import json
import time
import logging
import signal
import sys
import os
from datetime import datetime
from pathlib import Path
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import threading

# Configuration - Consider moving to config file
CONFIG_FILE = '/etc/weather-station/config.json'
DEFAULT_CONFIG = {
    'serial': {
        'port': '/dev/ttyUSB0',  # Common for Pi USB devices
        'baudrate': 9600,
        'timeout': 5
    },
    'influxdb': {
        'url': 'http://weather.local:8086',
        'token': 'n9Kn1y9G2Tsq85Xhkh-EP_-oBSs7GXDXx33SsrSshFYcIcfa7nTrv3oYAWF9Twz3MCfsL2rOomPtKv1iyJ_nKA==',
        'org': 'rotse-test',
        'bucket': 'weather'
    },
    'logging': {
        'level': 'INFO',
        'file': '/var/log/weather-station/weather.log',
        'max_bytes': 10485760,  # 10MB
        'backup_count': 5
    },
    'collection': {
        'retry_delay': 5,
        'loop_delay': 0.1
    }
}


class WeatherStationDaemon:
    def __init__(self, config_file=CONFIG_FILE):
        self.config = self.load_config(config_file)
        self.running = False
        self.serial_conn = None
        self.influx_client = None
        self.write_api = None
        self.data_thread = None
        self.logger = None

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)

        self.setup_logging()

    def load_config(self, config_file):
        """Load configuration from file, create default if not exists"""
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    config = json.load(f)
                # Merge with defaults for any missing keys
                return self.merge_config(DEFAULT_CONFIG, config)
            else:
                # Create config directory and file
                os.makedirs(os.path.dirname(config_file), exist_ok=True)
                with open(config_file, 'w') as f:
                    json.dump(DEFAULT_CONFIG, f, indent=4)
                return DEFAULT_CONFIG
        except Exception as e:
            print(f"Error loading config, using defaults: {e}")
            return DEFAULT_CONFIG

    def merge_config(self, default, loaded):
        """Recursively merge loaded config with defaults"""
        result = default.copy()
        for key, value in loaded.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self.merge_config(result[key], value)
            else:
                result[key] = value
        return result

    def setup_logging(self):
        """Setup logging with rotation"""
        from logging.handlers import RotatingFileHandler

        log_config = self.config['logging']

        # Create log directory
        log_file = log_config['file']
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        # Setup logger
        self.logger = logging.getLogger('weather_daemon')
        self.logger.setLevel(getattr(logging, log_config['level']))

        # File handler with rotation
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=log_config['max_bytes'],
            backupCount=log_config['backup_count']
        )
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        )

        # Console handler for systemd journal
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(
            logging.Formatter('%(levelname)s - %(message)s')
        )

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.stop()
        sys.exit(0)

    def setup_serial(self):
        """Initialize serial connection with retries"""
        serial_config = self.config['serial']

        try:
            if self.serial_conn and self.serial_conn.is_open:
                self.serial_conn.close()

            self.serial_conn = serial.Serial(
                port=serial_config['port'],
                baudrate=serial_config['baudrate'],
                timeout=serial_config['timeout']
            )
            self.logger.info(f"Serial connection established on {serial_config['port']}")
            return True

        except Exception as e:
            self.logger.error(f"Serial connection failed: {e}")
            return False

    def setup_influxdb(self):
        """Initialize InfluxDB connection"""
        influx_config = self.config['influxdb']

        try:
            if self.influx_client:
                self.influx_client.close()

            self.influx_client = InfluxDBClient(
                url=influx_config['url'],
                token=influx_config['token'],
                org=influx_config['org']
            )
            self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)

            # Test connection
            buckets = self.influx_client.buckets_api().find_buckets()
            self.logger.info("InfluxDB connection established")
            return True

        except Exception as e:
            self.logger.error(f"InfluxDB connection failed: {e}")
            return False

    def parse_weather_data(self, data_line):
        """
        Parse weather station data
        Expected format: "T:22.6 H:50.1 P:1011.9 WS:0.00"
        """
        try:
            data_line = data_line.strip()
            if not data_line:
                return None

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
                    field_name = field_mapping.get(key, key.lower())

                    try:
                        data_dict[field_name] = float(value.strip())
                    except ValueError:
                        self.logger.warning(f"Could not convert {key}:{value} to float")
                        continue

            return data_dict if data_dict else None

        except Exception as e:
            self.logger.error(f"Error parsing data '{data_line}': {e}")
            return None

    def write_to_influxdb(self, data):
        """Write data to InfluxDB with error handling"""
        try:
            points = []
            bucket = self.config['influxdb']['bucket']

            for field, value in data.items():
                if isinstance(value, (int, float)):
                    point = Point("weather") \
                        .tag("location", "default") \
                        .field(field, value) \
                        .time(datetime.utcnow(), WritePrecision.NS)
                    points.append(point)

            if points:
                self.write_api.write(bucket=bucket, record=points)
                self.logger.info(f"Data written to InfluxDB: {data}")
                return True

        except Exception as e:
            self.logger.error(f"Error writing to InfluxDB: {e}")
            return False

    def collect_data(self):
        """Main data collection loop"""
        self.logger.info("Starting data collection loop...")
        retry_delay = self.config['collection']['retry_delay']
        loop_delay = self.config['collection']['loop_delay']

        while self.running:
            try:
                if not self.serial_conn or not self.serial_conn.is_open:
                    self.logger.warning("Serial connection lost, attempting reconnect...")
                    if not self.setup_serial():
                        time.sleep(retry_delay)
                        continue

                if self.serial_conn.in_waiting > 0:
                    line = self.serial_conn.readline().decode('utf-8').strip()

                    if line:
                        self.logger.debug(f"Received: {line}")
                        parsed_data = self.parse_weather_data(line)

                        if parsed_data:
                            if not self.write_to_influxdb(parsed_data):
                                # InfluxDB write failed, try to reconnect
                                self.logger.warning("InfluxDB write failed, attempting reconnect...")
                                self.setup_influxdb()

                time.sleep(loop_delay)

            except serial.SerialException as e:
                self.logger.error(f"Serial error: {e}")
                time.sleep(retry_delay)
                self.setup_serial()

            except Exception as e:
                self.logger.error(f"Unexpected error in collection loop: {e}")
                time.sleep(retry_delay)

        self.logger.info("Data collection loop ended")

    def start(self):
        """Start the daemon"""
        self.logger.info("Starting weather station daemon...")

        # Setup connections
        if not self.setup_serial():
            self.logger.error("Failed to establish serial connection")
            return False

        if not self.setup_influxdb():
            self.logger.error("Failed to establish InfluxDB connection")
            return False

        self.running = True

        # Start data collection thread
        self.data_thread = threading.Thread(target=self.collect_data, daemon=True)
        self.data_thread.start()

        self.logger.info("Weather station daemon started successfully")
        return True

    def stop(self):
        """Stop the daemon gracefully"""
        self.logger.info("Stopping weather station daemon...")
        self.running = False

        # Wait for data thread to finish
        if self.data_thread and self.data_thread.is_alive():
            self.data_thread.join(timeout=5)

        # Close connections
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()

        if self.influx_client:
            self.influx_client.close()

        self.logger.info("Weather station daemon stopped")

    def run(self):
        """Main run method for daemon"""
        if not self.start():
            sys.exit(1)

        try:
            # Keep main thread alive
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("Received keyboard interrupt")
        finally:
            self.stop()


def main():
    """Main entry point"""
    daemon = WeatherStationDaemon()
    daemon.run()


if __name__ == "__main__":
    main()