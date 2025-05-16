# Use an official Python runtime as a parent image (Debian "Bullseye" based)
FROM python:3.9-slim-bullseye

# Prevent Python from using user site packages
ENV PYTHONNOUSERSITE=1
# Ensure apt-get runs non-interactively
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install OS-level build dependencies for Debian
# libpq-dev is the equivalent of postgresql-dev for PostgreSQL client headers
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    # Clean up apt caches to keep image size down
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# CRITICAL STEP: Upgrade build tools and install a recent Cython FIRST.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir "Cython>=3.0.8"

COPY requirements.txt .

# Install Python dependencies.
RUN pip install --no-cache-dir --verbose -r requirements.txt

COPY ./app /app/app
EXPOSE 80
# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]