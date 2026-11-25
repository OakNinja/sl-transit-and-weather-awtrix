FROM python:3.12-slim

WORKDIR /app
COPY app.py /app/app.py
COPY assets /app/assets

ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/app.py"]
