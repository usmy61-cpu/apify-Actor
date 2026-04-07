# Use Apify's Python + Playwright base image
FROM apify/actor-python-playwright:3.11

# Set working directory
WORKDIR /usr/src/app

# Copy requirements first for Docker layer caching
COPY requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (Chromium only to reduce image size)
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy source code
COPY . ./

# Run the actor
CMD ["python", "-m", "src.main"]
