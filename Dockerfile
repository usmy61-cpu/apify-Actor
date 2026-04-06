# Use Apify's Playwright base image (includes Chromium + all dependencies)
FROM apify/actor-node-playwright-chrome:18

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install --omit=dev

# Copy all source files
COPY . ./

# Run the actor
CMD ["node", "src/main.js"]
