#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   echo -e "${RED}This script should not be run as root${NC}"
   exit 1
fi

echo -e "${GREEN}Installing Weather Daemon...${NC}"

# Update system
echo -e "${YELLOW}Updating system packages...${NC}"
sudo apt update

# Install Python and pip if not present
echo -e "${YELLOW}Installing Python dependencies...${NC}"
sudo apt install -y python3 python3-pip python3-venv

# Create virtual environment
echo -e "${YELLOW}Creating virtual environment...${NC}"
python3 -m venv /opt/weather-daemon-venv
source /opt/weather-daemon-venv/bin/activate

# Install Python packages
echo -e "${YELLOW}Installing Python packages...${NC}"
pip install --upgrade pip
pip install -r requirements.txt

# Create directories
echo -e "${YELLOW}Creating directories...${NC}"
sudo mkdir -p /etc/weather-daemon
sudo mkdir -p /var/log/weather-daemon
sudo chown pi:pi /var/log/weather-daemon

# Copy configuration file
echo -e "${YELLOW}Installing configuration file...${NC}"
sudo cp config.json /etc/weather-daemon/
sudo chown pi:pi /etc/weather-daemon/config.json

# Create wrapper script
echo -e "${YELLOW}Creating wrapper script...${NC}"
sudo tee /usr/local/bin/weather-daemon > /dev/null << 'EOF'
#!/bin/bash
source /opt/weather-daemon-venv/bin/activate
exec python3 /opt/weather-daemon/weather_daemon.py "$@"
EOF

# Copy main script
sudo mkdir -p /opt/weather-daemon
sudo cp weather_daemon.py /opt/weather-daemon/
sudo chown -R pi:pi /opt/weather-daemon

# Make wrapper executable
sudo chmod +x /usr/local/bin/weather-daemon

# Install systemd service
echo -e "${YELLOW}Installing systemd service...${NC}"
sudo cp weather-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload

# Add user to dialout group for serial access
echo -e "${YELLOW}Adding user to dialout group...${NC}"
sudo usermod -a -G dialout pi

echo -e "${GREEN}Installation complete!${NC}"
echo
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Edit /etc/weather-daemon/config.json with your InfluxDB settings"
echo "2. Connect your Pi Pico to the Raspberry Pi"
echo "3. Start the service: sudo systemctl start weather-daemon"
echo "4. Enable auto-start: sudo systemctl enable weather-daemon"
echo "5. Check status: sudo systemctl status weather-daemon"
echo "6. View logs: sudo journalctl -u weather-daemon -f"
echo
echo -e "${YELLOW}You may need to log out and back in for group changes to take effect.${NC}"