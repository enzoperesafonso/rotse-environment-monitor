# Weather Daemon for Pi Pico to InfluxDB2

A robust Python daemon that reads weather data from a Raspberry Pi Pico via serial connection and sends it to an InfluxDB2 database. **Designed for remote weather stations** with comprehensive error handling, retry mechanisms, and resilience features.

## Features

- **Robust Error Handling**: Automatically retries failed connections and operations
- **Data Buffering**: Queues data when network is unavailable, sends when reconnected
- **Watchdog Monitoring**: Detects hangs and automatically restarts if needed
- **Serial Auto-Recovery**: Automatically reconnects to Pi Pico if connection is lost
- **InfluxDB Resilience**: Handles network outages and database unavailability
- **Background Processing**: Multi-threaded design for better reliability
- **Comprehensive Logging**: Detailed logs for remote debugging
- **Systemd Integration**: Automatic startup and restart on failure
- **Remote Station Ready**: Built specifically for unattended operation

## Data Format Support

Parses Pi Pico data in format: `T:{temp:.1f} H:{hum:.1f} P:{pres:.1f} WS:{wind_speed_avg:.2f}`

## Requirements

- Raspberry Pi with Python 3.6+
- Pi Pico connected via USB/serial
- InfluxDB2 server accessible on the network
- Internet connection for initial setup

## Installation

1. Clone or download this repository to your Raspberry Pi
2. Make the install script executable and run it:
```bash
chmod +x install.sh
./install.sh
```

3. Edit the configuration file with your settings:
```bash
sudo nano /etc/weather-daemon/config.json
```

4. Update the configuration with your InfluxDB2 details and resilience settings
5. Start the service:
```bash
sudo systemctl start weather-daemon
sudo systemctl enable weather-daemon
```

## Configuration

The configuration file at `/etc/weather-daemon/config.json` includes resilience settings:

```json
{
  "serial_port": "/dev/ttyACM0",
  "baud_rate": 9600,
  "serial_timeout": 1.0,
  "read_interval": 5.0,
  
  "influxdb": {
    "url": "http://192.168.1.100:8086",
    "token": "your-influxdb-token-here",
    "org": "your-org-name",
    "bucket": "weather-data"
  },
  
  "measurement_name": "weather_data",
  "location_tag": "outdoor",
  
  "max_serial_retries": 10,
  "max_influx_retries": 20,
  "serial_retry_delay": 5,
  "influx_retry_delay": 30,
  "watchdog_timeout": 300,
  "data_timeout": 600,
  "health_check_interval": 60,
  "queue_flush_interval": 10,
  "restart_on_hang": true
}
```

### Resilience Configuration Options

- `max_serial_retries`: Maximum attempts to reconnect serial (default: 10)
- `max_influx_retries`: Maximum attempts to reconnect InfluxDB (default: 20)
- `serial_retry_delay`: Seconds between serial reconnection attempts (default: 5)
- `influx_retry_delay`: Seconds between InfluxDB reconnection attempts (default: 30)
- `watchdog_timeout`: Seconds before considering daemon hung (default: 300)
- `data_timeout`: Seconds without data before triggering reconnection (default: 600)
- `health_check_interval`: Seconds between health checks (default: 60)
- `queue_flush_interval`: Seconds between queue processing attempts (default: 10)
- `restart_on_hang`: Whether to restart daemon if it hangs (default: true)

## Remote Station Features

### Automatic Recovery
- **Serial Connection**: Automatically detects and recovers from Pi Pico disconnection
- **Network Outages**: Queues data during network issues, sends when reconnected
- **InfluxDB Downtime**: Buffers up to 1000 data points during database outages
- **Hang Detection**: Watchdog thread monitors main process and restarts if hung

### Data Integrity
- **Buffering**: Data is queued in memory during connection issues
- **Retry Logic**: Failed transmissions are automatically retried
- **Graceful Shutdown**: Attempts to send remaining queued data on shutdown
- **Overflow Protection**: Oldest data is discarded if buffer fills up

### Monitoring & Diagnostics
- **Health Checks**: Regular status monitoring with detailed logging
- **Queue Status**: Reports queue size and retry counts
- **Connection Status**: Monitors both serial and InfluxDB connections
- **Error Tracking**: Counts consecutive errors and forces restart if needed

## Usage

### Service Management

```bash
# Start the service
sudo systemctl start weather-daemon

# Stop the service
sudo systemctl stop weather-daemon

# Enable auto-start on boot
sudo systemctl enable weather-daemon

# Check service status
sudo systemctl status weather-daemon

# View logs
sudo journalctl -u weather-daemon -f

# View recent logs with priority
sudo journalctl -u weather-daemon --since="1 hour ago" -p info
```

### Remote Monitoring

```bash
# Check daemon health
sudo journalctl -u weather-daemon --since="10 minutes ago" | grep -E "(Health check|Connected|Failed)"

# Monitor queue status
sudo journalctl -u weather-daemon --since="1 hour ago" | grep -E "(Queue size|Queued|Sent)"

# Check for errors
sudo journalctl -u weather-daemon --since="1 day ago" -p err
```

## Troubleshooting

### Remote Station Issues

1. **Daemon Not Responding**:
   - Check if service is running: `sudo systemctl status weather-daemon`
   - View recent logs: `sudo journalctl -u weather-daemon --since="1 hour ago"`
   - If hung, it should auto-restart within 5 minutes

2. **Data Not Reaching InfluxDB**:
   - Check queue status in logs: `grep "Queue size" /var/log/weather-daemon/weather-daemon.log`
   - Verify InfluxDB connectivity: `curl -I http://your-influxdb-url:8086/ping`
   - Data will be queued and sent when connection is restored

3. **Pi Pico Connection Issues**:
   - Check if device is present: `ls /dev/ttyACM*`
   - Verify serial port in config matches actual device
   - Daemon will automatically retry connection

4. **High Memory Usage**:
   - Large data queue during extended outages
   - Queue is limited to 1000 points to prevent memory issues
   - Oldest data is discarded if buffer fills

### Log Analysis

```bash
# Check for recent errors
sudo journalctl -u weather-daemon --since="1 day ago" -p err

# Monitor connection status
sudo journalctl -u weather-daemon -f | grep -E "(Connected|Failed|Retry)"

# Check data processing
sudo journalctl -u weather-daemon -f | grep -E "(parsed|sent|queued)"

# View health status
sudo journalctl -u weather-daemon -f | grep "Health check"
```

### Performance Tuning

For extreme remote conditions, you can adjust:

```json
{
  "max_influx_retries": 50,
  "influx_retry_delay": 60,
  "watchdog_timeout": 900,
  "data_timeout": 1200,
  "queue_flush_interval": 30
}
```

## System Integration

### Systemd Features
- **Automatic Restart**: Service restarts on failure with backoff
- **Startup Limits**: Prevents restart loops (5 attempts in 5 minutes)
- **Watchdog Support**: 10-minute watchdog timeout for hung processes
- **Logging**: Integrated with systemd journal

### Log Rotation
Logs are automatically rotated by systemd. For custom log rotation:

```bash
sudo tee /etc/logrotate.d/weather-daemon > /dev/null << 'EOF'
/var/log/weather-daemon/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    copytruncate
    notifempty
}
EOF
```

## Files

- `weather_daemon.py`: Main daemon script with resilience features
- `config.json`: Configuration file with retry settings
- `weather-daemon.service`: Enhanced systemd service file
- `requirements.txt`: Python dependencies
- `install.sh`: Installation script
- `README.md`: This documentation

## Architecture

```
Pi Pico → Serial → Weather Daemon → InfluxDB2
                      ↓
                 [Data Queue] ← Watchdog Thread
                      ↓
                 Sender Thread
```

The daemon uses a multi-threaded architecture:
- **Main Thread**: Reads serial data and manages connections
- **Watchdog Thread**: Monitors health and handles recovery
- **Sender Thread**: Processes queued data in background

## License

MIT License - Feel free to modify and distribute as needed.