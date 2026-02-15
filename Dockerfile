# Stage 1: Build React frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.11-slim
WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ .

# Copy built frontend into static directory
COPY --from=frontend-build /app/backend/static ./static/

# Create logs directory
RUN mkdir -p /app/logs

EXPOSE 9876

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9876"]
