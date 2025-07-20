#!/usr/bin/env python3
"""
Data Upload Daemon for ROTSE-III Namibia Telescope Weather Station.
"""

import serial
import time
import re
import json
import logging
import signal
import sys
import os
import threading
import queue
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from pathlib import Path


class WeatherDaemon:
    def __init__(self, config_path="/etc/weather-daemon/config.json"):
        self.config_path = config_path
        self.running = True
        self.serial_connection = None
        self.influx_client = None
        self.write_api = None

        # Retry and watchdog settings
        self.serial_retry_count = 0
        self.influx_retry_count = 0
        self.last_data_time = datetime.now()
        self.last_heartbeat = datetime.now()
        self.data_queue = queue.Queue(maxsize=1000)  # Buffer for offline data
        self.watchdog_thread = None
        self.data_sender_thread = None
        self.queue_lock = threading.Lock()

        # Setup logging
        self.setup_logging()

        # Load configuration
        self.load_config()

        # Setup signal handlers
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)

    def setup_logging(self):
        """Setup logging configuration"""
        log_dir = Path("/var/log/weather-daemon")
        log_dir.mkdir(parents=True, exist_ok=True)

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('/var/log/weather-daemon/weather-daemon.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger('WeatherDaemon')

    def load_config(self):
        """Load configuration from JSON file"""
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)

            # Serial configuration
            self.serial_port = config.get('serial_port', '/dev/ttyACM0')
            self.baud_rate = config.get('baud_rate', 9600)
            self.serial_timeout = config.get('serial_timeout', 1.0)

            # InfluxDB configuration
            self.influx_url = config['influxdb']['url']
            self.influx_token = config['influxdb']['token']
            self.influx_org = config['influxdb']['org']
            self.influx_bucket = config['influxdb']['bucket']

            # Data configuration
            self.measurement_name = config.get('measurement_name', 'weather_data')
            self.location_tag = config.get('location_tag', 'outdoor')
            self.read_interval = config.get('read_interval', 5.0)

            # Retry and resilience configuration
            self.max_serial_retries = config.get('max_serial_retries', 10)
            self.max_influx_retries = config.get('max_influx_retries', 20)
            self.serial_retry_delay = config.get('serial_retry_delay', 5)
            self.influx_retry_delay = config.get('influx_retry_delay', 30)
            self.watchdog_timeout = config.get('watchdog_timeout', 300)  # 5 minutes
            self.data_timeout = config.get('data_timeout', 600)  # 10 minutes
            self.health_check_interval = config.get('health_check_interval', 60)
            self.queue_flush_interval = config.get('queue_flush_interval', 10)
            self.restart_on_hang = config.get('restart_on_hang', True)

            self.logger.info(f"Configuration loaded from {self.config_path}")

        except FileNotFoundError:
            self.logger.error(f"Configuration file not found: {self.config_path}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in configuration file: {e}")
            sys.exit(1)
        except KeyError as e:
            self.logger.error(f"Missing required configuration key: {e}")
            sys.exit(1)

    def connect_serial(self):
        """Connect to the Pi Pico via serial with retry logic"""
        retry_count = 0
        while retry_count < self.max_serial_retries and self.running:
            try:
                if self.serial_connection and self.serial_connection.is_open:
                    self.serial_connection.close()

                self.serial_connection = serial.Serial(
                    self.serial_port,
                    self.baud_rate,
                    timeout=self.serial_timeout
                )

                # Test the connection by attempting to read
                self.serial_connection.flush()
                self.serial_retry_count = 0
                self.logger.info(f"Connected to serial port {self.serial_port}")
                return True

            except serial.SerialException as e:
                retry_count += 1
                self.serial_retry_count = retry_count
                self.logger.warning(f"Serial connection attempt {retry_count}/{self.max_serial_retries} failed: {e}")

                if retry_count < self.max_serial_retries:
                    time.sleep(self.serial_retry_delay)

            except Exception as e:
                retry_count += 1
                self.logger.error(f"Unexpected error connecting to serial: {e}")
                if retry_count < self.max_serial_retries:
                    time.sleep(self.serial_retry_delay)

        self.logger.error(f"Failed to connect to serial after {self.max_serial_retries} attempts")
        return False

    def connect_influxdb(self):
        """Connect to InfluxDB2 with retry logic"""
        retry_count = 0
        while retry_count < self.max_influx_retries and self.running:
            try:
                if self.influx_client:
                    self.influx_client.close()

                self.influx_client = InfluxDBClient(
                    url=self.influx_url,
                    token=self.influx_token,
                    org=self.influx_org,
                    timeout=30000  # 30 second timeout
                )
                self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)

                # Test connection
                self.influx_client.ping()
                self.influx_retry_count = 0
                self.logger.info("Connected to InfluxDB2")
                return True

            except Exception as e:
                retry_count += 1
                self.influx_retry_count = retry_count
                self.logger.warning(f"InfluxDB connection attempt {retry_count}/{self.max_influx_retries} failed: {e}")

                if retry_count < self.max_influx_retries:
                    time.sleep(self.influx_retry_delay)

        self.logger.error(f"Failed to connect to InfluxDB after {self.max_influx_retries} attempts")
        return False

    def parse_weather_data(self, data_line):
        """Parse weather data from Pi Pico output"""
        # Expected format: T:{temp:.1f} H:{hum:.1f} P:{pres:.1f} WS:{wind_speed_avg:.2f}
        pattern = r'T:(\d+\.?\d*)\s+H:(\d+\.?\d*)\s+P:(\d+\.?\d*)\s+WS:(\d+\.?\d*)'

        match = re.search(pattern, data_line)
        if match:
            return {
                'temperature': float(match.group(1)),
                'humidity': float(match.group(2)),
                'pressure': float(match.group(3)),
                'wind_speed': float(match.group(4))
            }
        return None

    def send_to_influxdb(self, weather_data, timestamp=None):
        """Send weather data to InfluxDB2 with retry logic"""
        if timestamp is None:
            timestamp = datetime.utcnow()

        try:
            point = Point(self.measurement_name)
            point.tag("location", self.location_tag)
            point.field("temp", weather_data['temperature'])
            point.field("humid", weather_data['humidity'])
            point.field("press", weather_data['pressure'])
            point.field("wind_speed", weather_data['wind_speed'])
            point.time(timestamp)

            self.write_api.write(bucket=self.influx_bucket, org=self.influx_org, record=point)
            self.logger.debug(f"Data sent to InfluxDB: {weather_data}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to send data to InfluxDB: {e}")
            return False

    def queue_data(self, weather_data, timestamp=None):
        """Queue weather data for sending (with overflow protection)"""
        if timestamp is None:
            timestamp = datetime.utcnow()

        try:
            with self.queue_lock:
                if self.data_queue.full():
                    # Remove oldest data to make room
                    try:
                        old_data = self.data_queue.get_nowait()
                        self.logger.warning("Data queue full, dropping oldest data point")
                    except queue.Empty:
                        pass

                self.data_queue.put_nowait((weather_data, timestamp))
                self.logger.debug(f"Queued data point. Queue size: {self.data_queue.qsize()}")

        except Exception as e:
            self.logger.error(f"Failed to queue data: {e}")

    def data_sender_worker(self):
        """Background thread to send queued data"""
        while self.running:
            try:
                if self.data_queue.empty():
                    time.sleep(self.queue_flush_interval)
                    continue

                # Try to send queued data
                if self.influx_client:
                    sent_count = 0
                    failed_items = []

                    # Process up to 10 items at a time
                    for _ in range(min(10, self.data_queue.qsize())):
                        try:
                            weather_data, timestamp = self.data_queue.get_nowait()
                            if self.send_to_influxdb(weather_data, timestamp):
                                sent_count += 1
                            else:
                                failed_items.append((weather_data, timestamp))
                        except queue.Empty:
                            break

                    # Re-queue failed items
                    for item in failed_items:
                        try:
                            self.data_queue.put_nowait(item)
                        except queue.Full:
                            self.logger.warning("Queue full, dropping failed data point")

                    if sent_count > 0:
                        self.logger.info(f"Sent {sent_count} queued data points")

                else:
                    # No InfluxDB connection, wait longer
                    time.sleep(self.influx_retry_delay)

            except Exception as e:
                self.logger.error(f"Error in data sender thread: {e}")
                time.sleep(self.queue_flush_interval)

    def watchdog_worker(self):
        """Background watchdog thread to monitor daemon health"""
        while self.running:
            try:
                now = datetime.now()

                # Check if we've received data recently
                if (now - self.last_data_time).total_seconds() > self.data_timeout:
                    self.logger.warning(f"No data received for {self.data_timeout} seconds")

                    # Try to reconnect serial
                    if self.serial_connection and self.serial_connection.is_open:
                        self.logger.info("Attempting to reconnect serial connection")
                        self.connect_serial()

                # Check if main thread is responsive
                if (now - self.last_heartbeat).total_seconds() > self.watchdog_timeout:
                    self.logger.error(f"Main thread unresponsive for {self.watchdog_timeout} seconds")

                    if self.restart_on_hang:
                        self.logger.error("Forcing daemon restart due to hang")
                        os._exit(1)  # Force exit to trigger systemd restart

                # Log health status
                queue_size = self.data_queue.qsize()
                if queue_size > 0:
                    self.logger.info(f"Health check: Queue size: {queue_size}, "
                                     f"Serial retries: {self.serial_retry_count}, "
                                     f"InfluxDB retries: {self.influx_retry_count}")

                time.sleep(self.health_check_interval)

            except Exception as e:
                self.logger.error(f"Error in watchdog thread: {e}")
                time.sleep(self.health_check_interval)

    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def cleanup(self):
        """Clean up resources"""
        self.logger.info("Starting cleanup...")

        # Stop background threads
        if self.watchdog_thread and self.watchdog_thread.is_alive():
            self.watchdog_thread.join(timeout=5)

        if self.data_sender_thread and self.data_sender_thread.is_alive():
            self.data_sender_thread.join(timeout=10)

        # Try to send any remaining queued data
        if self.influx_client and not self.data_queue.empty():
            self.logger.info(f"Attempting to send {self.data_queue.qsize()} remaining data points")
            remaining = min(50, self.data_queue.qsize())  # Send up to 50 points
            for _ in range(remaining):
                try:
                    weather_data, timestamp = self.data_queue.get_nowait()
                    self.send_to_influxdb(weather_data, timestamp)
                except queue.Empty:
                    break
                except Exception as e:
                    self.logger.error(f"Error sending final data: {e}")
                    break

        if self.serial_connection and self.serial_connection.is_open:
            self.serial_connection.close()
            self.logger.info("Serial connection closed")

        if self.influx_client:
            self.influx_client.close()
            self.logger.info("InfluxDB connection closed")

        self.logger.info("Cleanup complete")

    def run(self):
        """Main daemon loop with enhanced error handling"""
        self.logger.info("Starting Weather Daemon...")

        # Start background threads
        self.watchdog_thread = threading.Thread(target=self.watchdog_worker, daemon=True)
        self.data_sender_thread = threading.Thread(target=self.data_sender_worker, daemon=True)

        self.watchdog_thread.start()
        self.data_sender_thread.start()

        # Connect to serial port
        if not self.connect_serial():
            self.logger.error("Failed to connect to serial port after all retries")
            # Continue anyway - watchdog will try to reconnect

        # Connect to InfluxDB
        if not self.connect_influxdb():
            self.logger.error("Failed to connect to InfluxDB after all retries")
            # Continue anyway - data will be queued

        self.logger.info("Weather Daemon started successfully")

        consecutive_errors = 0
        max_consecutive_errors = 10

        try:
            while self.running:
                try:
                    # Update heartbeat
                    self.last_heartbeat = datetime.now()

                    # Check serial connection
                    if not self.serial_connection or not self.serial_connection.is_open:
                        self.logger.warning("Serial connection lost, attempting to reconnect...")
                        if not self.connect_serial():
                            time.sleep(self.serial_retry_delay)
                            continue

                    # Check InfluxDB connection
                    if not self.influx_client:
                        self.logger.info("Attempting to reconnect to InfluxDB...")
                        self.connect_influxdb()

                    # Read from serial
                    if self.serial_connection and self.serial_connection.is_open:
                        try:
                            if self.serial_connection.in_waiting > 0:
                                line = self.serial_connection.readline().decode('utf-8').strip()

                                if line:
                                    self.logger.debug(f"Received: {line}")

                                    # Parse weather data
                                    weather_data = self.parse_weather_data(line)

                                    if weather_data:
                                        self.last_data_time = datetime.now()
                                        self.logger.info(f"Weather data parsed: {weather_data}")

                                        # Try to send immediately, queue if failed
                                        if self.influx_client and self.send_to_influxdb(weather_data):
                                            consecutive_errors = 0
                                        else:
                                            self.logger.warning("Failed to send immediately, queuing data")
                                            self.queue_data(weather_data)

                                    else:
                                        self.logger.debug(f"Could not parse line: {line}")

                        except serial.SerialException as e:
                            consecutive_errors += 1
                            self.logger.error(f"Serial error: {e}")

                            # Try to reconnect
                            if not self.connect_serial():
                                self.logger.error("Failed to reconnect to serial port")
                                time.sleep(self.serial_retry_delay)

                        except UnicodeDecodeError as e:
                            consecutive_errors += 1
                            self.logger.warning(f"Unicode decode error (corrupt data): {e}")

                    # Check for too many consecutive errors
                    if consecutive_errors >= max_consecutive_errors:
                        self.logger.error(f"Too many consecutive errors ({consecutive_errors}), forcing restart")
                        if self.restart_on_hang:
                            os._exit(1)
                        else:
                            consecutive_errors = 0
                            time.sleep(60)  # Wait a minute before continuing

                    time.sleep(self.read_interval)

                except Exception as e:
                    consecutive_errors += 1
                    self.logger.error(f"Unexpected error in main loop: {e}")
                    time.sleep(self.read_interval)

        except KeyboardInterrupt:
            self.logger.info("Interrupted by user")

        finally:
            self.cleanup()
            self.logger.info("Weather Daemon stopped")


def main():
    """Main entry point"""
    daemon = WeatherDaemon()
    daemon.run()


if __name__ == "__main__":
    main()