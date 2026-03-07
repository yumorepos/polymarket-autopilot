FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cache layer)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Copy source code
COPY . .

# Create data directory for SQLite
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "polymarket_autopilot"]
CMD ["--help"]
