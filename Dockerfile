# Use an official Python runtime as a parent image
FROM python:3.9-alpine

# Set the working directory in the container
WORKDIR /app

# Install necessary packages for psycopg2-binary
# gcc, musl-dev are for building, postgresql-dev for headers
RUN apk --no-cache add gcc musl-dev postgresql-dev

# Copy the requirements file
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY ./app /app/app

# Expose the port the app runs on (FastAPI default is 8000, but we'll map 80 in compose)
EXPOSE 80

# Command to run the FastAPI application (will be overridden by docker-compose)
# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
