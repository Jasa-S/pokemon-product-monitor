FROM python:3.13-alpine

WORKDIR /app
COPY monitor.py /app/monitor.py

RUN addgroup -S monitor && adduser -S monitor -G monitor && mkdir /data && chown monitor:monitor /data
USER monitor

ENV PYTHONUNBUFFERED=1 DATABASE_PATH=/data/monitor.db
VOLUME ["/data"]
CMD ["python", "/app/monitor.py"]
