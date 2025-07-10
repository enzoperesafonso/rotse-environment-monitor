#!/usr/bin/env python3
"""
Weather Station Data Collector Daemon
Reads serial data from the weather station and sends to InfluxDB2
"""

import serial
import json
import time
import logging
import logging.handlers
import argparse
import signal
import sys
import os
from datetime import datetime
from pathlib import Path
import yaml
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import threading


class WeatherStationCollector:
    def __init__(self, config):
        self.config = config
        self.running = False
        self.serial_conn = None
        self.influx_client = None
        self.write_api = None
        self.logger = logging.getLogger(__name__)

    def setup_serial(self):
        """Initialize serial connection"""
        try:
            self.serial_conn = serial.Serial(
                port=self.config['serial']['port'],
                baudrate=self.config['serial']['baudrate'],
                timeout=self.config['serial']['timeout']
            )
            self.logger.info(f"Serial connection established on {self.config['serial']['port']}")
            return True
        except Exception as e:
            self.logger.error(f"Serial connection failed: {e}")
            return False

    def setup_influxdb(self):
        """Initialize InfluxDB connection"""
        try:
            self.influx_client = InfluxDBClient(
                url=self.config['influxdb']['url'],
                token=self.config['influxdb']['token'],
                org=self.config['influxdb']['org']
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
                        self.logger.warning(f"Could not convert {key}:{value} to float")
                        continue

            return data_dict if data_dict else None

        except Exception as e:
            self.logger.error(f"Error parsing data: {e}")
            return None

    def write_to_influxdb(self, data):
        """Write data to InfluxDB"""
        try:
            # Create a point for each measurement
            points = []

            for field, value in data.items():
                if isinstance(value, (int, float)):
                    point = Point("weather") \
                        .tag("location", self.config['data']['location']) \
                        .field(field, value) \
                        .time(datetime.utcnow(), WritePrecision.NS)
                    points.append(point)

            if points:
                self.write_api.write(bucket=self.config['influxdb']['bucket'], record=points)
                self.logger.info(f"Data written to InfluxDB: {data}")

        except Exception as e:
            self.logger.error(f"Error writing to InfluxDB: {e}")

    def collect_data(self):
        """Main data collection loop"""
        self.logger.info("Starting data collection...")

        while self.running:
            try:
                if self.serial_conn and self.serial_conn.in_waiting > 0:
                    line = self.serial_conn.readline().decode('utf-8').strip()

                    if line:
                        self.logger.debug(f"Received: {line}")

                        # Parse the data
                        parsed_data = self.parse_weather_data(line)

                        if parsed_data:
                            # Write to InfluxDB
                            self.write_to_influxdb(parsed_data)

                time.sleep(self.config['data']['loop_delay'])

            except serial.SerialException as e:
                self.logger.error(f"Serial error: {e}")
                # Try to reconnect
                time.sleep(self.config['data']['reconnect_interval'])
                self.setup_serial()

            except Exception as e:
                self.logger.error(f"Unexpected error: {e}")
                time.sleep(1)

    def start(self):
        """Start the collector"""
        self.logger.info("Starting weather station collector...")

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
        self.logger.info("Stopping weather station collector...")
        self.running = False

        if self.serial_conn:
            self.serial_conn.close()

        if self.influx_client:
            self.influx_client.close()


def load_config(config_path):
    """Load configuration from YAML file"""
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        return config
    except Exception as e:
        print(f"Error loading config: {e}")
        sys.exit(1)


def setup_logging(config):
    """Setup logging configuration"""
    log_config = config.get('logging', {})

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Setup root logger
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_config.get('level', 'INFO')))

    # Clear any existing handlers
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler with rotation
    if 'log_file' in log_config:
        # Ensure log directory exists
        log_file = Path(log_config['log_file'])
        log_file.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            log_config['log_file'],
            maxBytes=log_config.get('max_bytes', 10485760),  # 10MB
            backupCount=log_config.get('backup_count', 5)
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)


def signal_handler(signum, frame, collector):
    """Handle shutdown signals"""
    print(f"\nReceived signal {signum}, shutting down...")
    collector.stop()
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description='Weather Station Data Collector')
    parser.add_argument(
        '--config', '-c',
        default='/etc/weather-collector/config.yaml',
        help='Path to configuration file'
    )
    parser.add_argument(
        '--validate-config',
        action='store_true',
        help='Validate configuration and exit'
    )

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    if args.validate_config:
        print("Configuration is valid!")
        sys.exit(0)

    # Setup logging
    setup_logging(config)
    logger = logging.getLogger(__name__)

    # Create collector
    collector = WeatherStationCollector(config)

    # Setup signal handlers
    signal.signal(signal.SIGINT, lambda s, f: signal_handler(s, f, collector))
    signal.signal(signal.SIGTERM, lambda s, f: signal_handler(s, f, collector))

    try:
        if collector.start():
            logger.info("Weather station collector started successfully")

            # Keep the main thread alive
            while True:
                time.sleep(1)

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        collector.stop()


if __name__ == "__main__":
    main()