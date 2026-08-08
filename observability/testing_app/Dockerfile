FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY testing_app.py .
COPY assets/ assets/

EXPOSE 8050
CMD ["python3", "-u", "testing_app.py"]
