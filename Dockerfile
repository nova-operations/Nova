FROM python:3.12-slim

WORKDIR /app

# Install git
RUN apt-get update && apt-get install -y git && apt-get clean

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set environment variables (these should be overridden by Railway variables)
ENV PYTHONUNBUFFERED=1

# Run the telegram bot as a module to fix imports
CMD ["python", "-m", "nova.telegram_bot"]
