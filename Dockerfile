FROM python:3.12-slim

WORKDIR /app

# Install git
RUN apt-get update && apt-get install -y git && apt-get clean

# Upgrade pip to the latest version (26.0.1+) to avoid root user warnings
RUN pip install --upgrade pip --root-user-action=ignore

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

# Copy application code
COPY . .

# Ensure start script is executable
RUN chmod +x start.sh

# Set environment variables (these should be overridden by Railway variables)
ENV PYTHONUNBUFFERED=1

# Run the startup script
CMD ["./start.sh"]
