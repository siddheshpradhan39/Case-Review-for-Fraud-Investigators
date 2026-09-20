FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
COPY data/calibration.json data/sample_cases.csv ./data/
ENV DATA_DIR=/var/data PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "mkdir -p $DATA_DIR && uvicorn app.main:app --host 0.0.0.0 --port $PORT"]
