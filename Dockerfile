# Stage 1: Build React Frontend
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --cache /app/frontend/.npm_cache --prefer-offline
COPY frontend/ ./
RUN npm run build

# Stage 2: Runtime Environment
FROM python:3.13-slim
WORKDIR /app

# Install pip requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Backend codebase
COPY backend/ ./backend/

# Copy compiled SPA static assets from builder stage
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

EXPOSE 8000

# Set production execution options
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
