FROM python:3.13-alpine

RUN adduser -D -H -u 10001 send

WORKDIR /app
COPY localsend_send.py /app/localsend_send.py
RUN chown -R send:send /app

USER send
EXPOSE 53200
ENV LOCALSEND_SEND_PORT=53200

ENTRYPOINT ["python3", "/app/localsend_send.py"]
