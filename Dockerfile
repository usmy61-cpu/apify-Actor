# Use Apify's Python + Playwright base image
# Playwright + Chromium are pre-installed in this image — no need to reinstall
FROM apify/actor-python-playwright:3.11

# Set working directory
WORKDIR /usr/src/app

# Copy requirements first for Docker layer caching
COPY requirements.txt ./

# Install Python dependencies only
# python-jobspy is the correct PyPI package name (imports as 'from jobspy import ...')
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . ./

# Run the actor
CMD ["python", "-m", "src.main"]
